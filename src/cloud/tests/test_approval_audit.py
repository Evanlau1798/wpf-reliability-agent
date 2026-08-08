from datetime import UTC, datetime

from app.audit import AUDIT_FIELDS, build_approval_decision_audit


def test_approval_decision_audit_traces_human_actor_and_chain() -> None:
    record = build_approval_decision_audit(
        {"audit_sequence": 4, "audit_entry_hash": "a" * 64},
        approval_id="approval-1",
        actor="demo-operator",
        status="APPROVED",
        timestamp_utc=datetime(2026, 8, 8, 5, tzinfo=UTC),
    )

    assert record["sequence"] == 5
    assert record["type"] == "approval.decision"
    assert record["actor_type"] == "HUMAN"
    assert record["actor_id"] == "demo-operator"
    assert record["previous_entry_hash"] == "a" * 64
    assert record["approval_id"] == "approval-1"
    assert record["status"] == "APPROVED"
    assert record["timestamp_utc"] == "2026-08-08T05:00:00Z"
    assert set(record) - AUDIT_FIELDS == {"approval_id", "status"}
