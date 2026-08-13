import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import commands
from app.commands import (
    CommandStatus,
    command_result_hash,
    complete_command_once,
    expire_command_if_needed,
    lease_next_command,
    pending_command_query,
    validate_command_completion_binding,
    write_command,
    write_command_once,
)
from app.models import CommandResult, DiagnosticCommand


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"
COMMAND_NOW = datetime(2026, 8, 7, 0, 0, 30, tzinfo=UTC)


def test_command_statuses_match_p0_lifecycle() -> None:
    assert [status.value for status in CommandStatus] == [
        "PENDING",
        "LEASED",
        "COMPLETED",
        "FAILED",
        "EXPIRED",
    ]


def test_command_writer_persists_all_contract_fields() -> None:
    client = Mock()
    document = client.collection.return_value.document.return_value
    command = DiagnosticCommand.model_validate_json(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )

    write_command(client, command)

    client.collection.assert_called_once_with("commands")
    client.collection.return_value.document.assert_called_once_with(command.command_id)
    stored = document.create.call_args.args[0]
    serialized = json.loads(command.model_dump_json())
    for field in DiagnosticCommand.model_fields:
        assert stored[field] == serialized[field]
    assert stored["status"] == CommandStatus.PENDING.value
    assert stored["created_at"] is commands.firestore.SERVER_TIMESTAMP
    assert stored["updated_at"] is commands.firestore.SERVER_TIMESTAMP


def test_command_idempotency_key_returns_existing_command_without_second_create(
    monkeypatch,
) -> None:
    client = Mock()
    transaction = Mock()
    query = client.collection.return_value.where.return_value.limit.return_value
    client.transaction.return_value = transaction
    transaction.get.return_value = iter(
        [Mock(to_dict=lambda: {"command_id": "command-existing"})]
    )
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    command = DiagnosticCommand.model_validate_json(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )

    command_id = write_command_once(client, command)

    assert command_id == "command-existing"
    transaction.get.assert_called_once_with(query)
    transaction.create.assert_not_called()


def test_expired_pending_command_transitions_to_expired(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = client.collection.return_value.document.return_value
    client.transaction.return_value = transaction
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    payload["status"] = CommandStatus.PENDING.value
    document.get.return_value = Mock(exists=True, to_dict=lambda: payload)
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)

    expired = expire_command_if_needed(
        client,
        command_id=payload["command_id"],
        now=datetime(2026, 8, 7, 0, 2, tzinfo=UTC),
    )

    assert expired is True
    transaction.update.assert_called_once_with(
        document,
        {
            "status": CommandStatus.EXPIRED.value,
            "lease_owner": None,
            "lease_until": None,
            "updated_at": commands.firestore.SERVER_TIMESTAMP,
        },
    )


def test_pending_command_query_binds_target_app_session() -> None:
    client = Mock()
    collection = client.collection.return_value
    status_query = collection.where.return_value

    pending_command_query(client, "session-1")

    status_filter = collection.where.call_args.kwargs["filter"]
    session_filter = status_query.where.call_args.kwargs["filter"]
    assert (status_filter.field_path, status_filter.op_string, status_filter.value) == (
        "status",
        "==",
        CommandStatus.PENDING.value,
    )
    assert (session_filter.field_path, session_filter.op_string, session_filter.value) == (
        "target_app_session_id",
        "==",
        "session-1",
    )


def test_pending_command_query_uses_deterministic_order() -> None:
    client = Mock()
    collection = client.collection.return_value
    status_query = collection.where.return_value
    session_query = status_query.where.return_value
    issued_query = session_query.order_by.return_value
    id_query = issued_query.order_by.return_value
    limited_query = id_query.limit.return_value

    query = pending_command_query(client, "session-1")

    assert query is limited_query
    session_query.order_by.assert_called_once_with("issued_at_utc")
    issued_query.order_by.assert_called_once_with("__name__")
    id_query.limit.assert_called_once_with(1)


def test_pending_command_is_leased_transactionally(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    snapshot = Mock()
    snapshot.reference = Mock()
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    payload["expires_at_utc"] = "2026-08-07T00:10:00Z"
    snapshot.to_dict.return_value = payload
    client.transaction.return_value = transaction
    transaction.get.return_value = iter([snapshot])
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 7, 0, 2, tzinfo=UTC)

    leased = lease_next_command(
        client,
        app_session_id="session-1",
        lease_owner="device-test",
        now=now,
        duration=timedelta(seconds=30),
    )

    assert leased is not None
    assert leased.command_id == "command-read-1"
    transaction.update.assert_called_once_with(
        snapshot.reference,
        {
            "status": CommandStatus.LEASED.value,
            "lease_owner": "device-test",
            "lease_until": now + timedelta(seconds=30),
            "updated_at": commands.firestore.SERVER_TIMESTAMP,
        },
    )


def test_lease_conflict_returns_command_to_only_one_caller(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    snapshot = Mock()
    snapshot.reference = Mock()
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    payload["expires_at_utc"] = "2026-08-07T00:10:00Z"
    snapshot.to_dict.return_value = payload
    client.transaction.return_value = transaction
    transaction.get.side_effect = [iter([snapshot]), iter([]), iter([])]
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    now = datetime(2026, 8, 7, 0, 2, tzinfo=UTC)

    first = lease_next_command(
        client,
        app_session_id="session-1",
        lease_owner="device-a",
        now=now,
        duration=timedelta(seconds=30),
    )
    second = lease_next_command(
        client,
        app_session_id="session-1",
        lease_owner="device-b",
        now=now,
        duration=timedelta(seconds=30),
    )

    assert first is not None
    assert second is None
    transaction.update.assert_called_once()


@pytest.mark.parametrize(
    ("lease_owner", "app_session_id", "error"),
    [
        ("device-b", "session-1", "Lease owner mismatch"),
        ("device-a", "session-2", "App session mismatch"),
    ],
)
def test_command_completion_rejects_wrong_lease_owner_or_session(
    monkeypatch,
    lease_owner: str,
    app_session_id: str,
    error: str,
) -> None:
    client = Mock()
    transaction = Mock()
    document = client.collection.return_value.document.return_value
    client.transaction.return_value = transaction
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    payload.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a"})
    document.get.return_value = Mock(exists=True, to_dict=lambda: payload)
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    ).model_copy(update={"app_session_id": app_session_id})

    with pytest.raises(ValueError, match=error):
        validate_command_completion_binding(
            client,
            command_id="command-read-1",
            lease_owner=lease_owner,
            result=result,
        )


def test_command_result_hash_covers_the_complete_result_envelope() -> None:
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )
    expected = command_result_hash(result)

    assert len(expected) == 64
    assert command_result_hash(result.model_copy(update={"result": {"nodes": 43}})) != expected
    assert command_result_hash(result.model_copy(update={"app_session_id": "session-2"})) != expected


def test_command_completion_rejects_mismatched_result_hash(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = client.collection.return_value.document.return_value
    client.transaction.return_value = transaction
    payload = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    payload.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a"})
    document.get.return_value = Mock(exists=True, to_dict=lambda: payload)
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="Result hash mismatch"):
        validate_command_completion_binding(
            client,
            command_id="command-read-1",
            lease_owner="device-a",
            result=result,
        )


def test_same_command_result_resubmission_is_idempotent(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    command_collection = Mock()
    incident_collection = Mock()
    document = Mock()
    incident_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        command_collection
        if name == "commands"
        else incident_collection
    )
    command_collection.document.return_value = document
    incident_collection.document.return_value = incident_document
    command = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    command.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a", "lease_until": COMMAND_NOW + timedelta(seconds=15)})
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )
    result = result.model_copy(update={"result_hash": command_result_hash(result)})
    completed = {
        **command,
        "status": CommandStatus.COMPLETED.value,
        "result_hash": result.result_hash,
        "completion_evidence_revision": 6,
    }
    document.get.side_effect = [
        Mock(exists=True, to_dict=lambda: command),
        Mock(exists=True, to_dict=lambda: completed),
    ]
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "app_session_id": "session-1",
            "evidence_revision": 5,
            "pending_command_id": "command-read-1",
            "audit_sequence": 0, "audit_entry_hash": "0" * 64,
        },
    )
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)

    first = complete_command_once(
        client,
        command_id="command-read-1",
        lease_owner="device-a",
        result=result, now=COMMAND_NOW,
    )
    replay = complete_command_once(
        client,
        command_id="command-read-1",
        lease_owner="device-a",
        result=result, now=COMMAND_NOW,
    )

    assert first == (False, 6)
    assert replay == (True, 6)
    assert transaction.update.call_count == 2


def test_conflicting_command_result_is_rejected_without_overwrite(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    document = client.collection.return_value.document.return_value
    client.transaction.return_value = transaction
    command = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    command.update({"status": CommandStatus.COMPLETED.value, "lease_owner": "device-a"})
    original = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )
    original = original.model_copy(update={"result_hash": command_result_hash(original)})
    command["result_hash"] = original.result_hash
    conflicting = original.model_copy(update={"result": {"nodes": 43}})
    conflicting = conflicting.model_copy(
        update={"result_hash": command_result_hash(conflicting)}
    )
    document.get.return_value = Mock(exists=True, to_dict=lambda: command)
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)

    with pytest.raises(ValueError, match="Command result conflict"):
        complete_command_once(
            client,
            command_id="command-read-1",
            lease_owner="device-a",
            result=conflicting, now=COMMAND_NOW,
        )

    transaction.update.assert_not_called()


def test_source_lookup_completion_persists_source_code_evidence(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    command_collection = Mock()
    incident_collection = Mock()
    command_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        command_collection
        if name == "commands"
        else incident_collection
    )
    command_collection.document.return_value = command_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.return_value = evidence_document
    command = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    command.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a", "lease_until": COMMAND_NOW + timedelta(seconds=15), "tool": "source.lookup_binding"})
    command_document.get.return_value = Mock(exists=True, to_dict=lambda: command)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "app_session_id": "session-1",
            "evidence_revision": 5,
            "pending_command_id": "command-read-1",
            "audit_sequence": 0, "audit_entry_hash": "0" * 64,
        },
    )
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )
    result = result.model_copy(update={"result_hash": command_result_hash(result)})

    replay = complete_command_once(
        client,
        command_id="command-read-1",
        lease_owner="device-a",
        result=result, now=COMMAND_NOW,
    )

    assert replay == (False, 6)
    assert transaction.update.call_count == 2
    incident_update = transaction.update.call_args_list[1].args[1]
    assert incident_update["evidence_revision"] == 6
    assert incident_update["pending_command_id"] is None
    assert transaction.create.call_count == 2
    stored = next(call.args[1] for call in transaction.create.call_args_list if call.args[1].get("event_type") == "tool.result")
    assert stored["command_id"] == "command-read-1"
    assert stored["tool"] == "source.lookup_binding"
    assert stored["privacy_classification"] == "source_code"
    assert stored["evidence_hash"] == result.result_hash
    assert stored["result"] == result.model_dump(mode="json")


def test_successful_mutation_completion_marks_verification_pending(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    command_collection = Mock()
    incident_collection = Mock()
    command_document = Mock()
    incident_document = Mock()
    evidence_document = Mock()
    audit_document = Mock()
    client.transaction.return_value = transaction
    client.collection.side_effect = lambda name: (
        command_collection if name == "commands" else incident_collection
    )
    command_collection.document.return_value = command_document
    incident_collection.document.return_value = incident_document
    incident_document.collection.return_value.document.side_effect = (
        lambda name: audit_document if name == "12" else evidence_document
    )
    command = json.loads(
        (FIXTURES / "diagnostic-command-valid-mutation.json").read_text(encoding="utf-8")
    )
    command.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a", "lease_until": COMMAND_NOW + timedelta(seconds=15)})
    command_document.get.return_value = Mock(exists=True, to_dict=lambda: command)
    incident = {
        "app_session_id": "session-1",
        "evidence_revision": 5,
        "pending_command_id": command["command_id"],
            "state": "EXECUTING",
            "state_version": 8,
            "audit_sequence": 11,
            "audit_entry_hash": "b" * 64,
        }
    incident_document.get.return_value = Mock(exists=True, to_dict=lambda: incident)
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    ).model_copy(
        update={
            "command_id": command["command_id"],
            "result": {"status": "APPLIED", "before_state": True, "after_state": False},
        }
    )
    result = result.model_copy(update={"result_hash": command_result_hash(result)})

    complete_command_once(
        client,
        command_id=command["command_id"],
        lease_owner="device-a",
        result=result, now=COMMAND_NOW,
    )

    incident_updates = [call.args[1] for call in transaction.update.call_args_list]
    assert any(update.get("state") == "VERIFYING" for update in incident_updates)
    assert not any(update.get("state") == "MITIGATED" for update in incident_updates)
    audits = [call.args[1] for call in transaction.create.call_args_list if call.args[1].get("type")]
    assert [record["type"] for record in audits] == ["state.transition", "tool.result", "mutation.execution"]
    assert audits[1]["sequence"] == audits[0]["sequence"] + 1
    assert audits[1]["previous_entry_hash"] == audits[0]["entry_hash"]
    assert audits[2]["previous_entry_hash"] == audits[1]["entry_hash"]
    assert (audits[2]["arguments_hash"], audits[2]["result_hash"]) == (command["arguments_hash"], result.result_hash)
