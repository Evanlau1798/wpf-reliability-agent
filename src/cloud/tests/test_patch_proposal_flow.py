import pytest

from app import models
from app.agent import INVESTIGATOR_INSTRUCTION, validate_decision_evidence_ids
from app.correlation import AgentCorrelationContext, NormalizedEvidenceSummary
from app.policy import risk_for_tool
from app.reporting import REPORTER_INSTRUCTION


PATCH = {
    "target_file": "src/windows/Demo.BrokenWpfApp/MainWindow.xaml",
    "target_file_sha256": "a" * 64,
    "target_line": 42,
    "unified_diff": "- Text=\"{Binding DisplayNmae}\"\n+ Text=\"{Binding DisplayName}\"",
    "evidence_ids": ["source-command-1"],
}


def _context() -> AgentCorrelationContext:
    return AgentCorrelationContext(
        evidence=[
            NormalizedEvidenceSummary(
                evidence_id="source-command-1",
                kind="source.lookup_binding",
                app_session_id="session-1",
                observed_at_utc="2026-08-09T00:00:00Z",
                summary="Exact source-map attribution for DisplayNmae.",
            )
        ],
        candidate_claims=[],
        tool_calls_remaining=0,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )


def test_investigator_patch_proposal_is_evidence_bound_artifact_only() -> None:
    decision = models.AgentDecision.model_validate(
        {
            "schema_version": "1.0",
            "decision": "FINALIZE",
            "hypotheses": [],
            "patch_proposal": PATCH,
            "stop_reason": "Permanent fix can be proposed without execution.",
            "missing_evidence": [],
        }
    )

    validated = validate_decision_evidence_ids(decision, _context())

    assert validated.patch_proposal.unified_diff.endswith('Text="{Binding DisplayName}"')
    assert validated.next_command is None
    assert validated.proposed_action is None


def test_investigator_rejects_patch_proposal_with_unknown_evidence() -> None:
    decision = models.AgentDecision.model_validate(
        {
            "schema_version": "1.0",
            "decision": "FINALIZE",
            "hypotheses": [],
            "patch_proposal": {**PATCH, "evidence_ids": ["missing-source-evidence"]},
            "missing_evidence": [],
        }
    )

    with pytest.raises(ValueError, match="Unknown evidence ID"):
        validate_decision_evidence_ids(decision, _context())


def test_reporter_permanent_recommendation_can_carry_same_patch_artifact() -> None:
    recommendation = models.PermanentRecommendation.model_validate(
        {
            "summary": "Correct DisplayNmae to DisplayName.",
            "patch_proposal": PATCH,
        }
    )

    assert recommendation.patch_proposal.target_line == 42
    assert recommendation.patch_proposal.evidence_ids == ["source-command-1"]


def test_model_instructions_keep_patch_proposals_non_executing() -> None:
    assert "patch proposal" in INVESTIGATOR_INSTRUCTION.lower()
    assert "never a command" in INVESTIGATOR_INSTRUCTION.lower()
    assert "patch proposal" in REPORTER_INSTRUCTION.lower()
    assert "non-executed artifact" in REPORTER_INSTRUCTION.lower()


def test_patch_proposal_is_not_an_executable_diagnostic_tool() -> None:
    with pytest.raises(ValueError):
        models.DiagnosticTool("patch.propose")
    assert risk_for_tool("patch.propose") is models.RiskLevel.BLOCKED
