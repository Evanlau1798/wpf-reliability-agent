from app.agent import INVESTIGATOR_INSTRUCTION


def test_investigator_instruction_enforces_core_safety_rules() -> None:
    instruction = INVESTIGATOR_INSTRUCTION.lower()

    assert "untrusted data" in instruction
    assert "provided tool allowlist" in instruction
    assert "one next step" in instruction
    assert "only existing evidence ids" in instruction
    assert "never invent" in instruction
    assert "post-action verification" in instruction
