from datetime import datetime, timezone

from app.correlation import (
    AgentCorrelationContext,
    CandidateClaim,
    NormalizedEvidenceSummary,
    build_agent_context,
)
from app.models import Confidence


def test_agent_context_contains_only_compact_correlation_inputs_and_budgets() -> None:
    evidence = NormalizedEvidenceSummary(
        evidence_id="binding-1",
        kind="binding.aggregate",
        app_session_id="session-1",
        observed_at_utc=datetime(2026, 8, 7, tzinfo=timezone.utc),
        summary="Repeated DisplayNmae binding failure.",
        binding_path="DisplayNmae",
    )
    claim = CandidateClaim(
        candidate="ExperimentalPeopleGrid",
        summary="Binding evidence points to the experimental grid.",
        supporting_evidence_ids=["binding-1"],
        confidence=Confidence.MEDIUM,
    )

    context = build_agent_context(
        [evidence],
        [claim],
        tool_calls_remaining=4,
        max_context_bytes=16_384,
        max_context_tokens=4_096,
    )

    assert isinstance(context, AgentCorrelationContext)
    assert context.tool_calls_remaining == 4
    assert context.max_context_bytes == 16_384
    assert context.max_context_tokens == 4_096
    assert context.evidence[0].evidence_id == "binding-1"
    assert context.candidate_claims[0].candidate == "ExperimentalPeopleGrid"
    assert "payload" not in context.model_dump(mode="json")["evidence"][0]
