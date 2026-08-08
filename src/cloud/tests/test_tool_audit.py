import json
from pathlib import Path
from unittest.mock import Mock

from app import commands, workflow_state
from app.audit import AUDIT_FIELDS
from app.commands import CommandStatus, command_result_hash, complete_command_once
from app.models import CommandResult


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_read_only_tool_request_writes_hash_only_audit(monkeypatch) -> None:
    client = Mock()
    transaction = Mock()
    incident_document = Mock()
    audit_document = Mock()
    snapshot = Mock(
        exists=True,
        to_dict=lambda: {
            "read_only_tool_call_count": 0,
            "read_only_tool_request_keys": [],
            "audit_sequence": 3,
            "audit_entry_hash": "a" * 64,
        },
    )
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value = incident_document
    incident_document.get.return_value = snapshot
    incident_document.collection.return_value.document.return_value = audit_document
    monkeypatch.setattr(workflow_state.firestore, "transactional", lambda callback: callback)

    arguments = {"element_id": "element-1", "private_text": "do-not-audit"}
    request_hash = workflow_state.claim_read_only_tool_request(
        client,
        incident_id="incident-1",
        tool="ui.get_subtree",
        arguments=arguments,
    )

    transaction.create.assert_called_once()
    stored = transaction.create.call_args.args[1]
    assert stored["sequence"] == 4
    assert stored["type"] == "tool.request"
    assert stored["actor_type"] == "AGENT"
    assert stored["actor_id"] == "investigator"
    assert stored["previous_entry_hash"] == "a" * 64
    assert stored["tool"] == "ui.get_subtree"
    assert stored["request_hash"] == request_hash
    assert "arguments" not in stored
    assert "private_text" not in stored
    assert set(stored) - AUDIT_FIELDS == {"tool", "request_hash"}
    incident_update = transaction.update.call_args.args[1]
    assert incident_update["audit_sequence"] == 4
    assert incident_update["audit_entry_hash"] == stored["entry_hash"]


def test_tool_result_writes_hash_and_command_reference_only(monkeypatch) -> None:
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
    incident_document.collection.side_effect = lambda name: (
        Mock(document=lambda _key: evidence_document)
        if name == "evidence"
        else Mock(document=lambda _key: audit_document)
    )
    command = json.loads(
        (FIXTURES / "diagnostic-command-valid-read.json").read_text(encoding="utf-8")
    )
    command.update({"status": CommandStatus.LEASED.value, "lease_owner": "device-a"})
    command_document.get.return_value = Mock(exists=True, to_dict=lambda: command)
    incident_document.get.return_value = Mock(
        exists=True,
        to_dict=lambda: {
            "app_session_id": "session-1",
            "evidence_revision": 5,
            "pending_command_id": "command-read-1",
            "audit_sequence": 8,
            "audit_entry_hash": "b" * 64,
        },
    )
    monkeypatch.setattr(commands.firestore, "transactional", lambda callback: callback)
    result = CommandResult.model_validate_json(
        (FIXTURES / "command-result-success.json").read_text(encoding="utf-8")
    )
    result = result.model_copy(update={"result_hash": command_result_hash(result)})

    assert complete_command_once(
        client,
        command_id="command-read-1",
        lease_owner="device-a",
        result=result,
    ) == (False, 6)

    audit_records = [
        call.args[1]
        for call in transaction.create.call_args_list
        if isinstance(call.args[1], dict) and call.args[1].get("type") == "tool.result"
    ]
    assert len(audit_records) == 1
    stored = audit_records[0]
    assert stored["sequence"] == 9
    assert stored["actor_type"] == "DEVICE"
    assert stored["actor_id"] == "device-a"
    assert stored["previous_entry_hash"] == "b" * 64
    assert stored["command_id"] == "command-read-1"
    assert stored["tool"] == "ui.get_subtree"
    assert stored["result_hash"] == result.result_hash
    assert "result" not in stored
    assert "nodes" not in stored
    assert set(stored) - AUDIT_FIELDS == {"command_id", "tool", "result_hash"}
    incident_updates = [call.args[1] for call in transaction.update.call_args_list]
    assert incident_updates[-1]["audit_sequence"] == 9
    assert incident_updates[-1]["audit_entry_hash"] == stored["entry_hash"]
