def test_reporter_instruction_forbids_side_effects_and_new_evidence() -> None:
    from app.reporting import REPORTER_INSTRUCTION

    instruction = REPORTER_INSTRUCTION.lower()

    assert "do not call tools" in instruction
    assert "do not request new evidence" in instruction
    assert "do not change the incident ledger" in instruction
    assert "finalized evidence" in instruction


def test_reporter_input_contains_only_compact_finalized_records() -> None:
    import pytest
    from pydantic import ValidationError

    from app.reporting import ReporterInput

    record = {
        "reference": "evidence-1",
        "kind": "binding.error.aggregate",
        "summary": "Binding errors dropped after mitigation.",
        "payload_hash": "a" * 64,
        "related_ids": ["command-1"],
        "timestamp_utc": "2026-08-08T06:00:00Z",
    }
    reporter_input = ReporterInput.model_validate(
        {"evidence": [record], "tools": [], "approvals": [], "verification": []}
    )

    assert set(ReporterInput.model_fields) == {"evidence", "tools", "approvals", "verification"}
    assert reporter_input.evidence[0].reference == "evidence-1"
    with pytest.raises(ValidationError):
        ReporterInput.model_validate(
            {"evidence": [], "tools": [], "approvals": [], "verification": [], "raw_events": []}
        )
