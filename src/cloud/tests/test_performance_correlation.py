from datetime import datetime, timezone

from app.correlation import NormalizedEvidenceSummary, binding_errors_per_second


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
