import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from app import commands
from app.commands import (
    CommandStatus,
    expire_command_if_needed,
    pending_command_query,
    write_command,
    write_command_once,
)
from app.models import DiagnosticCommand


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


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
