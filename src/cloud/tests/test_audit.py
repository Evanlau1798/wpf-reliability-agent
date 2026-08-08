import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audit import AuditEvent, build_audit_record, verify_audit_chain
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
