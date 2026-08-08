from dataclasses import asdict, dataclass
from datetime import datetime
import math


@dataclass(frozen=True)
class MetricDelta:
    before: float
    after: float
    delta: float


@dataclass(frozen=True)
class PerformanceDelta:
    p95: MetricDelta
    before_sample_count: int
    after_sample_count: int
    before_duration_ms: float
    after_duration_ms: float
    before_confidence: str
    after_confidence: str


@dataclass(frozen=True)
class MitigationThresholds:
    max_binding_after_errors_per_second: float
    min_binding_reduction_ratio: float
    min_frame_p95_reduction_ratio: float
    min_visual_count_reduction_ratio: float
    min_performance_sample_count: int
    min_performance_confidence: str


@dataclass(frozen=True)
class PostActionVerification:
    binding: MetricDelta
    performance: PerformanceDelta
    visual: MetricDelta
    before_binding_evidence_id: str
    before_performance_evidence_id: str
    post_evidence_id: str
    command_id: str
    action_id: str


DEFAULT_MITIGATION_THRESHOLDS = MitigationThresholds(
    max_binding_after_errors_per_second=0.5,
    min_binding_reduction_ratio=0.9,
    min_frame_p95_reduction_ratio=0.2,
    min_visual_count_reduction_ratio=0.25,
    min_performance_sample_count=30,
    min_performance_confidence="MEDIUM",
)


def binding_rate_delta(
    before_evidence: dict[str, object],
    after_evidence: dict[str, object],
) -> MetricDelta | None:
    before_payload = _payload(before_evidence)
    after_payload = _payload(after_evidence)
    before_count = _number(before_payload.get("occurrence_count"))
    before_window_ms = _number(before_payload.get("aggregation_window_ms"))
    after_count = _number(after_payload.get("binding_occurrence_count"))
    after_window_ms = _number(after_payload.get("observation_window_ms"))
    reported_after_rate = _number(after_payload.get("binding_errors_per_second"))
    if None in (before_count, before_window_ms, after_count, after_window_ms, reported_after_rate):
        return None
    if before_window_ms <= 0 or after_window_ms <= 0:
        return None

    before_rate = before_count * 1000.0 / before_window_ms
    after_rate = after_count * 1000.0 / after_window_ms
    if not math.isclose(after_rate, reported_after_rate, rel_tol=1e-9, abs_tol=1e-9):
        return None
    return MetricDelta(before_rate, after_rate, after_rate - before_rate)


def frame_p95_delta(
    before_evidence: dict[str, object],
    after_evidence: dict[str, object],
) -> PerformanceDelta | None:
    before_payload = _payload(before_evidence)
    after_payload = _payload(after_evidence)
    before_stats = _object(before_payload.get("frame_statistics"))
    after_stats = _object(after_payload.get("frame_statistics"))
    before_p95 = _number(_first(before_stats, "p95_milliseconds", "P95Milliseconds"))
    after_p95 = _number(_first(after_stats, "p95_milliseconds", "P95Milliseconds"))
    before_count = _integer(_first(before_stats, "sample_count", "SampleCount"))
    after_count = _integer(_first(after_stats, "sample_count", "SampleCount"))
    before_duration = _number(before_payload.get("sample_duration_ms"))
    after_duration = _number(after_payload.get("performance_sample_duration_ms"))
    before_confidence = _confidence(before_payload.get("confidence"))
    after_confidence = _confidence(after_payload.get("performance_confidence"))
    if None in (
        before_p95,
        after_p95,
        before_count,
        after_count,
        before_duration,
        after_duration,
        before_confidence,
        after_confidence,
    ):
        return None
    if before_p95 < 0 or after_p95 < 0 or before_duration < 0 or after_duration < 0:
        return None
    return PerformanceDelta(
        MetricDelta(before_p95, after_p95, after_p95 - before_p95),
        before_count,
        after_count,
        before_duration,
        after_duration,
        before_confidence,
        after_confidence,
    )


def visual_count_delta(
    before_evidence: dict[str, object],
    after_evidence: dict[str, object],
) -> MetricDelta | None:
    before_session = before_evidence.get("app_session_id")
    after_session = after_evidence.get("app_session_id")
    if not isinstance(before_session, str) or before_session != after_session:
        return None
    before_payload = _payload(before_evidence)
    after_payload = _payload(after_evidence)
    before_scope = before_payload.get("visual_scope_id")
    after_scope = after_payload.get("visual_scope_id")
    if not isinstance(before_scope, str) or not before_scope or before_scope != after_scope:
        return None
    if before_payload.get("visual_count_truncated") is not False:
        return None
    if after_payload.get("visual_count_truncated") is not False:
        return None
    before_count = _integer(before_payload.get("visual_count"))
    after_count = _integer(after_payload.get("visual_count"))
    if before_count is None or after_count is None:
        return None
    return MetricDelta(float(before_count), float(after_count), float(after_count - before_count))


def meets_mitigation_thresholds(
    binding: MetricDelta,
    performance: PerformanceDelta,
    visual: MetricDelta,
    thresholds: MitigationThresholds = DEFAULT_MITIGATION_THRESHOLDS,
) -> bool:
    binding_reduction = _reduction_ratio(binding)
    frame_reduction = _reduction_ratio(performance.p95)
    visual_reduction = _reduction_ratio(visual)
    minimum_confidence = _confidence_rank(thresholds.min_performance_confidence)
    before_confidence = _confidence_rank(performance.before_confidence)
    after_confidence = _confidence_rank(performance.after_confidence)
    if None in (
        binding_reduction,
        frame_reduction,
        visual_reduction,
        minimum_confidence,
        before_confidence,
        after_confidence,
    ):
        return False
    if performance.before_sample_count < thresholds.min_performance_sample_count:
        return False
    if performance.after_sample_count < thresholds.min_performance_sample_count:
        return False
    if before_confidence < minimum_confidence:
        return False
    if after_confidence < minimum_confidence:
        return False
    return (
        binding.after <= thresholds.max_binding_after_errors_per_second
        and binding_reduction >= thresholds.min_binding_reduction_ratio
        and frame_reduction >= thresholds.min_frame_p95_reduction_ratio
        and visual_reduction >= thresholds.min_visual_count_reduction_ratio
    )


def evaluate_post_action_verification(
    evidence: list[dict[str, object]],
    post_evidence_id: str,
) -> PostActionVerification | None:
    post = _find_evidence(evidence, post_evidence_id)
    if post is None or post.get("event_type") != "recovery.result":
        return None
    session_id = post.get("app_session_id")
    correlation = _object(post.get("correlation"))
    command_id = correlation.get("command_id")
    action_id = correlation.get("action_id")
    if not all(isinstance(value, str) and value for value in (session_id, command_id, action_id)):
        return None

    command_evidence = _find_evidence(evidence, command_id)
    if command_evidence is None or command_evidence.get("event_type") != "tool.result":
        return None
    if command_evidence.get("tool") != "recovery.set_feature_flag":
        return None
    if command_evidence.get("app_session_id") != session_id:
        return None
    command_result = _object(command_evidence.get("result"))
    action_result = _object(command_result.get("result"))
    if command_result.get("status") != "SUCCEEDED":
        return None
    if action_result.get("status") not in {"APPLIED", "ALREADY_APPLIED"}:
        return None
    action_started = _timestamp(command_result.get("started_at_utc"))
    if action_started is None:
        return None

    binding_candidates: list[tuple[datetime, dict[str, object]]] = []
    performance_candidates: list[tuple[datetime, dict[str, object]]] = []
    for item in evidence:
        if item.get("app_session_id") != session_id:
            continue
        if item.get("event_type") == "binding.aggregate":
            observed_at = _timestamp(_payload(item).get("last_seen_utc"))
            if observed_at is not None and observed_at <= action_started:
                binding_candidates.append((observed_at, item))
        elif item.get("event_type") == "performance.sample":
            observed_at = _timestamp(item.get("timestamp_utc"))
            if observed_at is not None and observed_at <= action_started:
                performance_candidates.append((observed_at, item))
    if not binding_candidates or not performance_candidates:
        return None
    before_binding = max(binding_candidates, key=lambda item: item[0])[1]
    before_performance = max(performance_candidates, key=lambda item: item[0])[1]
    binding = binding_rate_delta(before_binding, post)
    performance = frame_p95_delta(before_performance, post)
    visual = visual_count_delta(before_performance, post)
    before_binding_id = _evidence_id(before_binding)
    before_performance_id = _evidence_id(before_performance)
    if None in (binding, performance, visual, before_binding_id, before_performance_id):
        return None
    return PostActionVerification(
        binding,
        performance,
        visual,
        before_binding_id,
        before_performance_id,
        post_evidence_id,
        command_id,
        action_id,
    )


def build_verification_audit(
    verification: PostActionVerification,
    outcome: str,
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "command_id": verification.command_id,
        "action_id": verification.action_id,
        "post_evidence_id": verification.post_evidence_id,
        "evidence_ids": [
            verification.before_binding_evidence_id,
            verification.before_performance_evidence_id,
            verification.post_evidence_id,
            verification.command_id,
        ],
        "metrics": {
            "binding_errors_per_second": _metric_audit(verification.binding, "errors_per_second"),
            "frame_p95_ms": {
                **_metric_audit(verification.performance.p95, "milliseconds"),
                "before_sample_count": verification.performance.before_sample_count,
                "after_sample_count": verification.performance.after_sample_count,
                "before_duration_ms": verification.performance.before_duration_ms,
                "after_duration_ms": verification.performance.after_duration_ms,
                "before_confidence": verification.performance.before_confidence,
                "after_confidence": verification.performance.after_confidence,
            },
            "visual_count": _metric_audit(verification.visual, "nodes"),
        },
        "thresholds": asdict(DEFAULT_MITIGATION_THRESHOLDS),
    }


def recovery_evidence_binding(
    evidence: list[dict[str, object]],
    post_evidence_id: str,
) -> tuple[str, str] | None:
    post = _find_evidence(evidence, post_evidence_id)
    if post is None or post.get("event_type") != "recovery.result":
        return None
    correlation = _object(post.get("correlation"))
    command_id = correlation.get("command_id")
    action_id = correlation.get("action_id")
    if not all(isinstance(value, str) and value for value in (command_id, action_id)):
        return None
    return command_id, action_id


def build_inconclusive_verification_audit(
    post_evidence_id: str,
    command_id: str,
    action_id: str,
    verification: PostActionVerification | None = None,
) -> dict[str, object]:
    if verification is not None:
        audit = build_verification_audit(verification, "INCONCLUSIVE")
        audit["reason"] = "thresholds_not_met"
        return audit
    return {
        "outcome": "INCONCLUSIVE",
        "reason": "insufficient_evidence",
        "command_id": command_id,
        "action_id": action_id,
        "post_evidence_id": post_evidence_id,
        "evidence_ids": [post_evidence_id, command_id],
        "metrics": {},
    }


def _payload(evidence: dict[str, object]) -> dict[str, object]:
    payload = evidence.get("payload")
    return payload if isinstance(payload, dict) else {}


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _first(values: dict[str, object], *names: str) -> object:
    return next((values[name] for name in names if name in values), None)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _confidence(value: object) -> str | None:
    return value if value in {"LOW", "MEDIUM", "HIGH"} else None


def _confidence_rank(value: str) -> int | None:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(value)


def _reduction_ratio(delta: MetricDelta) -> float | None:
    if delta.before <= 0:
        return None
    return (delta.before - delta.after) / delta.before


def _find_evidence(
    evidence: list[dict[str, object]],
    evidence_id: str,
) -> dict[str, object] | None:
    return next((item for item in evidence if _evidence_id(item) == evidence_id), None)


def _evidence_id(evidence: dict[str, object]) -> str | None:
    value = evidence.get("evidence_id")
    return value if isinstance(value, str) and value else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _metric_audit(metric: MetricDelta, unit: str) -> dict[str, object]:
    return {
        "before": metric.before,
        "after": metric.after,
        "delta": metric.delta,
        "unit": unit,
    }
