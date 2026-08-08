import json
from dataclasses import asdict
from pathlib import Path

from app.verification import (
    DEFAULT_MITIGATION_THRESHOLDS,
    MetricDelta,
    PerformanceDelta,
    binding_rate_delta,
    frame_p95_delta,
    meets_mitigation_thresholds,
    visual_count_delta,
)


FIXTURES = Path(__file__).parents[3] / "contracts" / "fixtures"


def test_binding_rate_delta_normalizes_before_and_after_to_errors_per_second() -> None:
    before = {
        "payload": {
            "occurrence_count": 50,
            "aggregation_window_ms": 10_000,
        }
    }
    after = {
        "payload": {
            "binding_occurrence_count": 2,
            "observation_window_ms": 10_000,
            "binding_errors_per_second": 0.2,
        }
    }

    delta = binding_rate_delta(before, after)

    assert delta is not None
    assert delta.before == 5.0
    assert delta.after == 0.2
    assert delta.delta == -4.8


def test_binding_rate_delta_rejects_inconsistent_post_rate() -> None:
    before = {"payload": {"occurrence_count": 10, "aggregation_window_ms": 10_000}}
    after = {
        "payload": {
            "binding_occurrence_count": 1,
            "observation_window_ms": 10_000,
            "binding_errors_per_second": 9.0,
        }
    }

    assert binding_rate_delta(before, after) is None


def test_frame_p95_delta_preserves_before_and_after_sample_confidence() -> None:
    before = {
        "payload": {
            "frame_statistics": {"sample_count": 120, "p95_milliseconds": 48.0},
            "sample_duration_ms": 2_000.0,
            "confidence": "HIGH",
        }
    }
    after = {
        "payload": {
            "frame_statistics": {"sample_count": 90, "p95_milliseconds": 18.0},
            "performance_sample_duration_ms": 1_500.0,
            "performance_confidence": "HIGH",
        }
    }

    delta = frame_p95_delta(before, after)

    assert delta is not None
    assert delta.p95.before == 48.0
    assert delta.p95.after == 18.0
    assert delta.p95.delta == -30.0
    assert delta.before_sample_count == 120
    assert delta.after_sample_count == 90
    assert delta.before_duration_ms == 2_000.0
    assert delta.after_duration_ms == 1_500.0
    assert delta.before_confidence == "HIGH"
    assert delta.after_confidence == "HIGH"


def test_visual_count_delta_requires_same_exact_scope() -> None:
    before = {
        "app_session_id": "session-1",
        "payload": {
            "visual_count": 1_500,
            "visual_count_truncated": False,
            "visual_scope_id": "element-session-1-7",
        },
    }
    after = {
        "app_session_id": "session-1",
        "payload": {
            "visual_count": 420,
            "visual_count_truncated": False,
            "visual_scope_id": "element-session-1-7",
        },
    }

    delta = visual_count_delta(before, after)

    assert delta is not None
    assert delta.before == 1_500.0
    assert delta.after == 420.0
    assert delta.delta == -1_080.0
    assert visual_count_delta(
        before,
        {**after, "payload": {**after["payload"], "visual_scope_id": "element-session-1-8"}},
    ) is None


def test_mitigation_thresholds_are_fixture_locked_and_deterministic() -> None:
    fixture = json.loads((FIXTURES / "mitigation-thresholds.json").read_text(encoding="utf-8"))
    metrics = fixture["passing_metrics"]
    binding = MetricDelta(
        metrics["binding"]["before"],
        metrics["binding"]["after"],
        metrics["binding"]["after"] - metrics["binding"]["before"],
    )
    frame = metrics["frame_p95"]
    performance = PerformanceDelta(
        MetricDelta(frame["before"], frame["after"], frame["after"] - frame["before"]),
        frame["before_sample_count"],
        frame["after_sample_count"],
        frame["before_duration_ms"],
        frame["after_duration_ms"],
        frame["before_confidence"],
        frame["after_confidence"],
    )
    visual = MetricDelta(
        metrics["visual_count"]["before"],
        metrics["visual_count"]["after"],
        metrics["visual_count"]["after"] - metrics["visual_count"]["before"],
    )

    assert asdict(DEFAULT_MITIGATION_THRESHOLDS) == fixture["thresholds"]
    assert meets_mitigation_thresholds(binding, performance, visual)
    assert not meets_mitigation_thresholds(
        MetricDelta(binding.before, 0.6, 0.6 - binding.before),
        performance,
        visual,
    )
