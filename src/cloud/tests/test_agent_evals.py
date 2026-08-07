import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from app.agent import READ_ONLY_DIAGNOSTIC_TOOLS, run_investigator_once
from app.correlation import AgentCorrelationContext, NormalizedEvidenceSummary
from app.models import DiagnosticTool


class _SessionService:
    async def create_session(self, **_kwargs) -> None:
        return None


class _Runner:
    app_name = "wpf_reliability_agent"

    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.session_service = _SessionService()

    def run_async(self, **_kwargs):
        output = self.outputs.pop(0)

        async def events():
            yield SimpleNamespace(
                is_final_response=lambda: True,
                output=output,
                content=None,
            )

        return events()


def _context(evidence: list[NormalizedEvidenceSummary]) -> AgentCorrelationContext:
    return AgentCorrelationContext(
        evidence=evidence,
        candidate_claims=[],
        tool_calls_remaining=6,
        max_context_bytes=65_536,
        max_context_tokens=32_768,
    )


def test_main_binding_performance_eval_selects_allowed_first_and_second_tools() -> None:
    observed_at = datetime(2026, 8, 8, tzinfo=UTC)
    evidence = [
        NormalizedEvidenceSummary(
            evidence_id="binding-1",
            kind="binding_aggregate",
            app_session_id="session-1",
            observed_at_utc=observed_at,
            summary="DisplayNmae binding failures are repeating.",
            element_id="people-grid",
            binding_path="DisplayNmae",
            occurrence_count=120,
            window_seconds=10,
        ),
        NormalizedEvidenceSummary(
            evidence_id="performance-1",
            kind="performance_sample",
            app_session_id="session-1",
            observed_at_utc=observed_at,
            summary="Frame p95 and visual count are degraded.",
            frame_p95_ms=45,
            visual_count=1_500,
        ),
    ]
    runner = _Runner(
        [
            {
                "schema_version": "1.0",
                "decision": "REQUEST_EVIDENCE",
                "hypotheses": [],
                "next_command": {
                    "tool": "ui.get_subtree",
                    "arguments": {"element_id": "people-grid", "max_depth": 4, "max_nodes": 300},
                },
                "missing_evidence": ["bounded UI subtree"],
            },
            {
                "schema_version": "1.0",
                "decision": "REQUEST_EVIDENCE",
                "hypotheses": [],
                "next_command": {
                    "tool": "performance.sample",
                    "arguments": {"element_id": "people-grid"},
                },
                "missing_evidence": ["focused performance sample"],
            },
        ]
    )

    decisions = [
        asyncio.run(
            run_investigator_once(
                runner,
                incident_id="incident-1",
                run_key=f"incident-1:{revision}:eval",
                context=_context(evidence),
            )
        )
        for revision in (1, 2)
    ]
    tools = [decision.next_command.tool for decision in decisions if decision.next_command]

    assert tools == [DiagnosticTool.UI_GET_SUBTREE, DiagnosticTool.PERFORMANCE_SAMPLE]
    assert all(tool in READ_ONLY_DIAGNOSTIC_TOOLS for tool in tools)
