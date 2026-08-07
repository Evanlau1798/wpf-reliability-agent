from datetime import datetime, timedelta, timezone

from app.correlation import (
    EvidenceEdgeType,
    NormalizedEvidenceSummary,
    binding_errors_per_second,
    build_performance_amplifier_edge,
    same_session_frame_p95,
    same_session_visual_metrics,
)


def _evidence(**updates: object) -> NormalizedEvidenceSummary:
    values: dict[str, object] = {
        "evidence_id": "evidence-1",
        "kind": "binding.aggregate",
        "app_session_id": "session-1",
        "observed_at_utc": datetime(2026, 8, 7, tzinfo=timezone.utc),
        "summary": "Performance correlation evidence.",
    }
    values.update(updates)
    return NormalizedEvidenceSummary.model_validate(values)


def test_binding_errors_per_second_uses_occurrence_window() -> None:
    evidence = _evidence(occurrence_count=25, window_seconds=10.0)

    assert binding_errors_per_second(evidence) == 2.5


def test_frame_p95_correlates_only_with_same_app_session() -> None:
    binding = _evidence(evidence_id="binding-1")
    same_session = _evidence(
        evidence_id="perf-1",
        kind="performance.sample",
        frame_p95_ms=41.5,
    )
    other_session = same_session.model_copy(update={"app_session_id": "session-2"})

    assert same_session_frame_p95(binding, same_session) == 41.5
    assert same_session_frame_p95(binding, other_session) is None


def test_visual_metrics_preserve_missing_values_without_guessing() -> None:
    binding = _evidence(evidence_id="binding-1")
    ui = _evidence(
        evidence_id="ui-1",
        kind="ui.snapshot",
        visual_count=1_500,
    )

    assert same_session_visual_metrics(binding, ui) == (1_500, None)


def test_performance_amplifier_edge_keeps_root_cause_and_symptom_separate() -> None:
    binding = _evidence(
        evidence_id="binding-1",
        occurrence_count=30,
        window_seconds=10.0,
    )
    performance = _evidence(
        evidence_id="perf-1",
        kind="performance.sample",
        observed_at_utc=binding.observed_at_utc + timedelta(seconds=5),
        frame_p95_ms=48.0,
    )

    edge = build_performance_amplifier_edge(binding, performance)

    assert edge is not None
    assert edge.source_evidence_id == "binding-1"
    assert edge.target_evidence_id == "perf-1"
    assert edge.edge_type is EvidenceEdgeType.PERFORMANCE_AMPLIFIER
