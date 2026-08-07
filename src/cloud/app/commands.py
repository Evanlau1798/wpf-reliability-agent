from enum import StrEnum
from datetime import datetime, timedelta
from typing import Literal

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import BaseModel, Field

from app.contracts import sha256_canonical
from app.firestore_client import COMMANDS_COLLECTION
from app.models import CommandResult, DiagnosticCommand, ResultStatus


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class CommandLeaseRequest(BaseModel):
    app_session_id: str = Field(min_length=1, max_length=256)
    wait_seconds: int = Field(ge=0, le=25)
    max_commands: Literal[1]


def command_result_hash(result: CommandResult) -> str:
    return sha256_canonical(result.model_dump(mode="json", exclude={"result_hash"}))


def write_command(client: firestore.Client, command: DiagnosticCommand) -> None:
    client.collection(COMMANDS_COLLECTION).document(command.command_id).create(
        _command_document(command)
    )


def write_command_once(client: firestore.Client, command: DiagnosticCommand) -> str:
    collection = client.collection(COMMANDS_COLLECTION)
    query = collection.where(
        filter=FieldFilter("idempotency_key", "==", command.idempotency_key)
    ).limit(1)

    @firestore.transactional
    def write(transaction: firestore.Transaction) -> str:
        existing = next(transaction.get(query), None)
        if existing is not None:
            command_id = (existing.to_dict() or {}).get("command_id")
            if not isinstance(command_id, str) or not command_id:
                raise ValueError("Existing command idempotency record is invalid")
            return command_id
        transaction.create(collection.document(command.command_id), _command_document(command))
        return command.command_id

    return write(client.transaction())


def expire_command_if_needed(
    client: firestore.Client,
    *,
    command_id: str,
    now: datetime,
) -> bool:
    document = client.collection(COMMANDS_COLLECTION).document(command_id)

    @firestore.transactional
    def expire(transaction: firestore.Transaction) -> bool:
        snapshot = document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Command does not exist")
        data = snapshot.to_dict() or {}
        if data.get("status") not in {
            CommandStatus.PENDING.value,
            CommandStatus.LEASED.value,
        }:
            return False
        command = DiagnosticCommand.model_validate(data)
        if command.expires_at_utc > now:
            return False
        transaction.update(
            document,
            {
                "status": CommandStatus.EXPIRED.value,
                "lease_owner": None,
                "lease_until": None,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return True

    return expire(client.transaction())


def pending_command_query(client: firestore.Client, app_session_id: str):
    return client.collection(COMMANDS_COLLECTION).where(
        filter=FieldFilter("status", "==", CommandStatus.PENDING.value)
    ).where(
        filter=FieldFilter("target_app_session_id", "==", app_session_id)
    ).order_by("issued_at_utc").order_by("__name__").limit(1)


def lease_next_command(
    client: firestore.Client,
    *,
    app_session_id: str,
    lease_owner: str,
    now: datetime,
    duration: timedelta,
) -> DiagnosticCommand | None:
    if not lease_owner:
        raise ValueError("Lease owner is required")
    if duration <= timedelta(0):
        raise ValueError("Lease duration must be positive")
    query = pending_command_query(client, app_session_id)

    @firestore.transactional
    def lease(transaction: firestore.Transaction) -> DiagnosticCommand | None:
        snapshot = next(transaction.get(query), None)
        if snapshot is None:
            return None
        command = DiagnosticCommand.model_validate(snapshot.to_dict() or {})
        if command.expires_at_utc <= now:
            transaction.update(
                snapshot.reference,
                {
                    "status": CommandStatus.EXPIRED.value,
                    "lease_owner": None,
                    "lease_until": None,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return None
        transaction.update(
            snapshot.reference,
            {
                "status": CommandStatus.LEASED.value,
                "lease_owner": lease_owner,
                "lease_until": now + duration,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return command

    return lease(client.transaction())


def validate_command_completion_binding(
    client: firestore.Client,
    *,
    command_id: str,
    lease_owner: str,
    result: CommandResult,
) -> DiagnosticCommand:
    document = client.collection(COMMANDS_COLLECTION).document(command_id)

    @firestore.transactional
    def validate(transaction: firestore.Transaction) -> DiagnosticCommand:
        snapshot = document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Command does not exist")
        return _validate_completion_data(
            snapshot.to_dict() or {},
            command_id=command_id,
            lease_owner=lease_owner,
            result=result,
        )

    return validate(client.transaction())


def complete_command_once(
    client: firestore.Client,
    *,
    command_id: str,
    lease_owner: str,
    result: CommandResult,
) -> bool:
    document = client.collection(COMMANDS_COLLECTION).document(command_id)

    @firestore.transactional
    def complete(transaction: firestore.Transaction) -> bool:
        snapshot = document.get(transaction=transaction)
        if not snapshot.exists:
            raise ValueError("Command does not exist")
        data = snapshot.to_dict() or {}
        if data.get("status") in {
            CommandStatus.COMPLETED.value,
            CommandStatus.FAILED.value,
            CommandStatus.EXPIRED.value,
        } and isinstance(data.get("result_hash"), str):
            _validate_replayed_completion(
                data,
                command_id=command_id,
                lease_owner=lease_owner,
                result=result,
            )
            if data["result_hash"] == result.result_hash:
                return True
            raise ValueError("Command result conflict")

        _validate_completion_data(
            data,
            command_id=command_id,
            lease_owner=lease_owner,
            result=result,
        )
        status_value = (
            CommandStatus.COMPLETED.value
            if result.status is ResultStatus.SUCCEEDED
            else (
                CommandStatus.EXPIRED.value
                if result.status is ResultStatus.EXPIRED
                else CommandStatus.FAILED.value
            )
        )
        transaction.update(
            document,
            {
                "status": status_value,
                "completion_result": result.model_dump(mode="json"),
                "result_hash": result.result_hash,
                "lease_until": None,
                "completed_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        return False

    return complete(client.transaction())


def _validate_completion_data(
    data: dict[str, object],
    *,
    command_id: str,
    lease_owner: str,
    result: CommandResult,
) -> DiagnosticCommand:
    if data.get("status") != CommandStatus.LEASED.value:
        raise ValueError("Command is not leased")
    if data.get("lease_owner") != lease_owner:
        raise ValueError("Lease owner mismatch")
    command = DiagnosticCommand.model_validate(data)
    _validate_result_binding(command, command_id=command_id, result=result)
    return command


def _validate_replayed_completion(
    data: dict[str, object],
    *,
    command_id: str,
    lease_owner: str,
    result: CommandResult,
) -> None:
    if data.get("lease_owner") != lease_owner:
        raise ValueError("Lease owner mismatch")
    command = DiagnosticCommand.model_validate(data)
    _validate_result_binding(command, command_id=command_id, result=result)


def _validate_result_binding(
    command: DiagnosticCommand,
    *,
    command_id: str,
    result: CommandResult,
) -> None:
    if result.app_session_id != command.target_app_session_id:
        raise ValueError("App session mismatch")
    if (
        result.command_id != command_id
        or result.command_id != command.command_id
        or result.incident_id != command.incident_id
    ):
        raise ValueError("Command completion binding mismatch")
    if result.result_hash != command_result_hash(result):
        raise ValueError("Result hash mismatch")


def _command_document(command: DiagnosticCommand) -> dict[str, object]:
    document = command.model_dump(mode="json")
    document.update(
        {
            "status": CommandStatus.PENDING.value,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )
    return document
