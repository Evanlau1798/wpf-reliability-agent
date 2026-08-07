import json
from pathlib import Path
from unittest.mock import Mock

from app import commands
from app.commands import CommandStatus, write_command
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
