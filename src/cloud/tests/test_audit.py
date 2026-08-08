import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audit import AuditEvent
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
