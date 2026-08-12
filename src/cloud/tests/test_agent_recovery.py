import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.agent import build_root_agent, run_investigator_once
from app.correlation import AgentCorrelationContext, NormalizedEvidenceSummary


class Runner:
    app_name = "wpf_reliability_agent"

    def __init__(self, outputs: list[object]) -> None:
        self.outputs = outputs
        self.session_service = SimpleNamespace(create_session=self.create_session)

    async def create_session(self, **_kwargs) -> None:
        return None

    def run_async(self, **_kwargs):
        output = self.outputs.pop(0)

        async def events():
            yield SimpleNamespace(is_final_response=lambda: True, output=output, content=None)

        return events()


def evidence(kind: str, ancestor: str | None = None) -> NormalizedEvidenceSummary:
    return NormalizedEvidenceSummary(
        evidence_id=f"{kind}-1", kind=kind, app_session_id="session-1",
        observed_at_utc=datetime(2026, 8, 13, tzinfo=UTC), summary="Exact demo fault evidence",
        binding_path="DisplayNmae", target_property="Text", nearest_named_ancestor=ancestor,
    )


def context(items: list[NormalizedEvidenceSummary]) -> AgentCorrelationContext:
    return AgentCorrelationContext(
        evidence=items, candidate_claims=[], tool_calls_remaining=6,
        max_context_bytes=65_536, max_context_tokens=32_768,
    )


def invalid_proposal(evidence_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0", "decision": "PROPOSE_ACTION", "hypotheses": [],
        "proposed_action": {
            "tool": "recovery.set_feature_flag",
            "arguments": {"feature": "UnknownFeature", "enabled": False, "expected_current_value": True},
            "evidence_ids": [evidence_id], "expected_effect": "Fallback.", "rollback_plan": "Restore.",
        },
        "missing_evidence": [],
    }


def run(outputs: list[object], items: list[NormalizedEvidenceSummary]):
    return asyncio.run(run_investigator_once(
        Runner(outputs), incident_id="incident-1", run_key="incident-1:1:eval", context=context(items),
    ))


def test_investigator_generation_is_deterministic() -> None:
    assert build_root_agent("gemini-test").generate_content_config.temperature == 0


def test_invalid_recovery_proposal_is_canonicalized_for_exact_demo_source() -> None:
    binding = evidence("binding.aggregate")
    source = evidence("source.lookup_binding", "ExperimentalPeopleGrid")
    decision = run([invalid_proposal(binding.evidence_id)], [binding, source])

    assert decision.proposed_action is not None
    assert decision.proposed_action.arguments == {
        "feature": "ExperimentalPeopleGrid", "enabled": False, "expected_current_value": True,
    }


def test_invalid_recovery_proposal_without_exact_source_is_rejected() -> None:
    binding = evidence("binding.aggregate")
    invalid = invalid_proposal(binding.evidence_id)

    with pytest.raises(ValueError, match="ExperimentalPeopleGrid"):
        run([invalid.copy(), invalid.copy()], [binding])
