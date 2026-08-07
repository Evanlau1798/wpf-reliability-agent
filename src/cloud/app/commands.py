from enum import StrEnum
from datetime import datetime

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.firestore_client import COMMANDS_COLLECTION
from app.models import DiagnosticCommand


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


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
