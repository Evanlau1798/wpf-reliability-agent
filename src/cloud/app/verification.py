from dataclasses import dataclass
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
