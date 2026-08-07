import json
from datetime import datetime, timedelta, timezone

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


def test_context_budget_prioritizes_high_confidence_then_material_then_recent() -> None:
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    high = NormalizedEvidenceSummary(
        evidence_id="high-1",
        kind="binding.aggregate",
        app_session_id="session-1",
        observed_at_utc=now - timedelta(minutes=5),
        summary="x" * 80,
        material=False,
    )
    material = high.model_copy(
        update={
            "evidence_id": "material-1",
            "observed_at_utc": now - timedelta(minutes=1),
            "material": True,
        }
    )
    recent = high.model_copy(
        update={"evidence_id": "recent-1", "observed_at_utc": now}
    )
    claim = CandidateClaim(
        candidate="ExperimentalPeopleGrid",
        summary="x" * 80,
        supporting_evidence_ids=["high-1"],
        confidence=Confidence.HIGH,
    )

    context = build_agent_context(
        [recent, material, high],
        [claim],
        tool_calls_remaining=4,
        max_context_bytes=800,
        max_context_tokens=800,
    )
    encoded = json.dumps(
        context.model_dump(mode="json"),
        separators=(",", ":"),
    ).encode("utf-8")

    assert [item.evidence_id for item in context.evidence] == ["high-1"]
    assert len(encoded) <= context.max_context_bytes
