def test_reporter_instruction_forbids_side_effects_and_new_evidence() -> None:
    from app.reporting import REPORTER_INSTRUCTION

    instruction = REPORTER_INSTRUCTION.lower()

    assert "do not call tools" in instruction
    assert "do not request new evidence" in instruction
    assert "do not change the incident ledger" in instruction
    assert "finalized evidence" in instruction
