import json
from dataclasses import asdict
from pathlib import Path

from app.verification import (
    DEFAULT_MITIGATION_THRESHOLDS,
    DEFAULT_REGRESSION_THRESHOLDS,
    MetricDelta,
    PostActionVerification,
    PerformanceDelta,
    binding_rate_delta,
    build_inconclusive_verification_audit,
    build_regression_verification_audit,
    build_verification_audit,
    evaluate_post_action_verification,
    frame_p95_delta,
    is_regression,
    meets_mitigation_thresholds,
    recovery_evidence_binding,
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


def test_frame_p95_delta_accepts_live_tool_and_recovery_wire_names() -> None:
    before = {
        "event_type": "tool.result",
        "result": {
            "result": {
                "frame_statistics": {"sample_count": 120, "p95_ms": 48.0},
                "sample_duration_ms": 2_000.0,
                "confidence": "HIGH",
            }
        },
    }
    after = {
        "payload": {
            "frame_statistics": {"sample_count": 90, "p95_milliseconds": 18.0},
            "performance_sample_duration_ms": 1_500.0,
            "performance_confidence": 2,
        }
    }

    delta = frame_p95_delta(before, after)

    assert delta is not None
    assert delta.p95 == MetricDelta(48.0, 18.0, -30.0)
    assert delta.before_confidence == delta.after_confidence == "HIGH"


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


def test_visual_count_delta_accepts_a_truncated_before_lower_bound() -> None:
    before = {
        "app_session_id": "session-1",
        "payload": {
            "visual_count": 500,
            "visual_count_truncated": True,
            "visual_scope_id": "element-session-1-7",
        },
    }
    after = {
        "app_session_id": "session-1",
        "payload": {
            "visual_count": 52,
            "visual_count_truncated": False,
            "visual_scope_id": "element-session-1-7",
        },
    }

    delta = visual_count_delta(before, after)

    assert delta == MetricDelta(500.0, 52.0, -448.0)


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


def test_main_before_after_fixture_verifies_mitigation() -> None:
    fixture = json.loads((FIXTURES / "post-action-mitigation.json").read_text(encoding="utf-8"))
    evidence = fixture["evidence"]

    result = evaluate_post_action_verification(evidence, "post-1")

    assert result is not None
    assert result.binding.before == 5.0
    assert result.binding.after == 0.2
    assert result.performance.p95.delta == -30.0
    assert result.visual.delta == -1_080.0
    assert result.before_binding_evidence_id == "binding-before"
    assert result.before_performance_evidence_id == "performance-before"
    assert meets_mitigation_thresholds(result.binding, result.performance, result.visual)
    audit = build_verification_audit(result, "MITIGATED")
    assert audit["outcome"] == fixture["expected_outcome"] == "MITIGATED"
    assert audit["post_evidence_id"] == "post-1"
    assert audit["metrics"]["binding_errors_per_second"]["delta"] == -4.8


def test_performance_tool_result_is_used_as_pre_action_baseline() -> None:
    fixture = json.loads((FIXTURES / "post-action-mitigation.json").read_text(encoding="utf-8"))
    evidence = fixture["evidence"]
    baseline = next(item for item in evidence if item["evidence_id"] == "performance-before")
    baseline["event_type"] = "tool.result"
    baseline["tool"] = "performance.sample"
    baseline["result"] = {
        "status": "SUCCEEDED",
        "completed_at_utc": baseline.pop("timestamp_utc"),
        "result": baseline.pop("payload"),
    }

    result = evaluate_post_action_verification(evidence, "post-1")

    assert result is not None
    assert result.before_performance_evidence_id == "performance-before"
    assert meets_mitigation_thresholds(result.binding, result.performance, result.visual)


def test_inconclusive_audit_keeps_post_and_action_binding_without_success_metrics() -> None:
    evidence = [
        {
            "evidence_id": "post-1",
            "event_type": "recovery.result",
            "correlation": {
                "incident_id": "incident-1",
                "command_id": "command-1",
                "action_id": "action-1",
            },
        }
    ]

    binding = recovery_evidence_binding(evidence, "post-1")

    assert binding == ("command-1", "action-1")
    assert build_inconclusive_verification_audit("post-1", *binding) == {
        "outcome": "INCONCLUSIVE",
        "reason": "insufficient_evidence",
        "command_id": "command-1",
        "action_id": "action-1",
        "post_evidence_id": "post-1",
        "evidence_ids": ["post-1", "command-1"],
        "metrics": {},
    }


def test_regression_thresholds_are_fixture_locked_and_keep_rollback_guidance() -> None:
    fixture = json.loads((FIXTURES / "regression-thresholds.json").read_text(encoding="utf-8"))
    metrics = fixture["regression_metrics"]
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

    assert asdict(DEFAULT_REGRESSION_THRESHOLDS) == fixture["thresholds"]
    assert is_regression(binding, performance, visual)
    verification = PostActionVerification(
        binding,
        performance,
        visual,
        "binding-before",
        "performance-before",
        "post-1",
        "command-1",
        "action-1",
    )
    audit = build_regression_verification_audit(verification, "Re-enable the feature.")
    assert audit["outcome"] == "FAILED_SAFE"
    assert audit["rollback_guidance"] == "Re-enable the feature."


def test_inconclusive_performance_sample_does_not_claim_improvement() -> None:
    fixture = json.loads(
        (FIXTURES / "post-action-inconclusive-performance.json").read_text(encoding="utf-8")
    )

    verification = evaluate_post_action_verification(fixture["evidence"], "post-1")

    assert verification is not None
    assert verification.performance.after_sample_count == fixture["after_sample_count"]
    assert not meets_mitigation_thresholds(
        verification.binding,
        verification.performance,
        verification.visual,
    )
    assert not is_regression(
        verification.binding,
        verification.performance,
        verification.visual,
    )
    audit = build_inconclusive_verification_audit(
        "post-1",
        verification.command_id,
        verification.action_id,
        verification,
    )
    assert audit["outcome"] == fixture["expected_outcome"] == "INCONCLUSIVE"
    assert audit["metrics"]["frame_p95_ms"]["after_sample_count"] == fixture["after_sample_count"]
