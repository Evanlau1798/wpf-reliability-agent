from datetime import datetime, timezone

from app.correlation import (
    NormalizedEvidenceSummary,
    binding_errors_per_second,
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
