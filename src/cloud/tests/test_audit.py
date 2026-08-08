import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audit import (
    ZERO_HASH,
    AuditEvent,
    build_approval_decision_audit,
    build_audit_record,
    build_mutation_execution_audit,
    build_mutation_verification_audit,
    build_state_transition_audit,
    build_tool_request_audit,
    build_tool_result_audit,
    verify_audit_chain,
)
from app.contracts import sha256_canonical


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_audit_event_shape_requires_chain_fields() -> None:
    event = AuditEvent.model_validate(
        {
            "sequence": 1,
            "type": "state.transition",
            "actor_type": "SYSTEM",
            "actor_id": "reliability-worker",
            "payload_hash": "1" * 64,
            "previous_entry_hash": "0" * 64,
            "entry_hash": "2" * 64,
            "timestamp_utc": datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc),
        }
    )

    assert event.sequence == 1
    assert event.type == "state.transition"
    assert event.actor_type == "SYSTEM"
    assert event.actor_id == "reliability-worker"
    assert event.payload_hash == "1" * 64
    assert event.previous_entry_hash == "0" * 64
    assert event.entry_hash == "2" * 64

    with pytest.raises(ValidationError):
        AuditEvent.model_validate(
            {
                "sequence": 1,
                "type": "state.transition",
                "actor_type": "SYSTEM",
                "actor_id": "reliability-worker",
                "payload_hash": "1" * 64,
                "entry_hash": "2" * 64,
                "timestamp_utc": "2026-08-08T06:00:00Z",
            }
        )


def test_audit_payload_hash_uses_canonical_json_golden_fixture() -> None:
    fixture = json.loads((FIXTURES / "hash-reordered.json").read_text(encoding="utf-8"))

    assert sha256_canonical(fixture["input"]) == fixture["sha256"]


def test_entry_hash_chain_detects_changed_payload() -> None:
    first = build_audit_record(
        sequence=1,
        event_type="state.transition",
        actor_type="SYSTEM",
        actor_id="reliability-worker",
        payload={"from_state": "VERIFYING", "to_state": "MITIGATED"},
        previous_entry_hash="0" * 64,
        timestamp_utc=datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc),
    )
    second = build_audit_record(
        sequence=2,
        event_type="verification.completed",
        actor_type="SYSTEM",
        actor_id="reliability-worker",
        payload={"outcome": "MITIGATED", "command_id": "command-1"},
        previous_entry_hash=first["entry_hash"],
        timestamp_utc=datetime(2026, 8, 8, 6, 0, 1, tzinfo=timezone.utc),
    )

    assert verify_audit_chain([first, second])
    changed = {**second, "outcome": "FAILED_SAFE"}
    assert not verify_audit_chain([first, changed])


def test_complete_audit_ledger_chain_passes_and_rejects_tampering() -> None:
    first = build_state_transition_audit(
        {"audit_sequence": 0, "audit_entry_hash": ZERO_HASH},
        sequence=1,
        from_state="NEW",
        to_state="TRIAGING",
        state_version=2,
    )
    request = build_tool_request_audit(
        {"audit_sequence": 1, "audit_entry_hash": first["entry_hash"]},
        tool="ui.get_subtree",
        request_hash="1" * 64,
    )
    result = build_tool_result_audit(
        {"audit_sequence": 2, "audit_entry_hash": request["entry_hash"]},
        tool="ui.get_subtree",
        command_id="command-1",
        result_hash="2" * 64,
        actor_id="device-1",
    )
    approval = build_approval_decision_audit(
        {"audit_sequence": 3, "audit_entry_hash": result["entry_hash"]},
        approval_id="approval-1",
        actor="demo-operator",
        status="APPROVED",
        timestamp_utc=datetime(2026, 8, 8, 6, 1, tzinfo=timezone.utc),
    )
    execution = build_mutation_execution_audit(
        {"audit_sequence": 4, "audit_entry_hash": approval["entry_hash"]},
        command_id="command-2",
        action_id="action-1",
        arguments_hash="3" * 64,
        result_hash="4" * 64,
        status="SUCCEEDED",
        actor_id="device-1",
    )
    verification = build_mutation_verification_audit(
        {"audit_sequence": 5, "audit_entry_hash": execution["entry_hash"]},
        command_id="command-2",
        action_id="action-1",
        arguments_hash="3" * 64,
        result_hash="4" * 64,
        verification={"outcome": "MITIGATED", "post_evidence_id": "post-1"},
    )
    records = [first, request, result, approval, execution, verification]

    assert verify_audit_chain(records)
    tampered = [*records[:-1], {**verification, "result_hash": "5" * 64}]
    assert not verify_audit_chain(tampered)
