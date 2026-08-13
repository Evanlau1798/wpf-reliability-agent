from datetime import UTC, datetime

import pytest
from app.agent import validate_decision_proposed_action
from app.correlation import AgentCorrelationContext, NormalizedEvidenceSummary
from app.models import AgentDecision


def test_recovery_proposal_requires_pre_action_performance_sample() -> None:
    context = AgentCorrelationContext(
        evidence=[
            NormalizedEvidenceSummary(
                evidence_id="binding-1",
                kind="binding.aggregate",
                app_session_id="session-1",
                observed_at_utc=datetime(2026, 8, 13, tzinfo=UTC),
                summary="DisplayNmae binding failures are active.",
            )
        ],
        candidate_claims=[],
        tool_calls_remaining=4,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )
    decision = AgentDecision.model_validate({
        "schema_version": "1.0",
        "decision": "PROPOSE_ACTION",
        "hypotheses": [],
        "proposed_action": {
            "tool": "recovery.set_feature_flag",
            "arguments": {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            },
            "evidence_ids": ["binding-1"],
            "expected_effect": "Use the stable fallback people list.",
            "rollback_plan": "Re-enable ExperimentalPeopleGrid after a verified source fix.",
        },
        "missing_evidence": [],
    })

    with pytest.raises(ValueError, match="pre-action performance sample"):
        validate_decision_proposed_action(decision, context)
