from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MetricDelta:
    before: float
    after: float
    delta: float


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


def _payload(evidence: dict[str, object]) -> dict[str, object]:
    payload = evidence.get("payload")
    return payload if isinstance(payload, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None
