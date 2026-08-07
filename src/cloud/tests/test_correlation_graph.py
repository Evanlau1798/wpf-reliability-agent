from datetime import datetime, timedelta, timezone

from app.correlation import (
    BindingCandidate,
    NormalizedEvidenceSummary,
    correlate_binding_incident,
)
from app.models import Confidence


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _evidence(evidence_id: str, kind: str, **updates: object) -> NormalizedEvidenceSummary:
    values: dict[str, object] = {
        "evidence_id": evidence_id,
        "kind": kind,
        "app_session_id": "session-1",
        "observed_at_utc": NOW,
        "summary": f"{kind} evidence.",
    }
    values.update(updates)
    return NormalizedEvidenceSummary.model_validate(values)


def test_main_demo_correlation_points_to_experimental_people_grid() -> None:
    binding = _evidence(
        "binding-1",
        "binding.aggregate",
        binding_path="DisplayNmae",
        nearest_named_ancestor="ExperimentalPeopleGrid",
        occurrence_count=30,
        window_seconds=10.0,
    )
    ui = _evidence(
        "ui-1",
        "ui.snapshot",
        observed_at_utc=NOW + timedelta(seconds=2),
        binding_path="DisplayNmae",
        nearest_named_ancestor="ExperimentalPeopleGrid",
        visual_count=1_500,
    )
    performance = _evidence(
        "perf-1",
        "performance.sample",
        observed_at_utc=NOW + timedelta(seconds=4),
        frame_p95_ms=48.0,
    )
    live_candidate = BindingCandidate(
        element_id="person-name-42",
        binding_path="DisplayNmae",
        target_property="Text",
        element_type="TextBlock",
        element_name="PersonName",
    )

    graph = correlate_binding_incident(binding, [ui, performance], [live_candidate])

    assert len(graph.candidate_claims) == 1
    assert graph.candidate_claims[0].candidate == "ExperimentalPeopleGrid"
    assert graph.candidate_claims[0].confidence is Confidence.HIGH
    assert set(graph.candidate_claims[0].supporting_evidence_ids) == {
        "binding-1",
        "ui-1",
        "perf-1",
    }
