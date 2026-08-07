from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import Confidence, Identifier, UtcDateTime


class EvidenceEdgeType(StrEnum):
    SAME_ELEMENT = "same_element"
    SAME_BINDING_PATH = "same_binding_path"
    SAME_TIME_WINDOW = "same_time_window"
    PERFORMANCE_AMPLIFIER = "performance_amplifier"
    VERIFIES = "verifies"
    CONTRADICTS = "contradicts"


class EvidenceEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_evidence_id: Identifier
    target_evidence_id: Identifier
    edge_type: EvidenceEdgeType


class NormalizedEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    kind: Identifier
    app_session_id: Identifier
    observed_at_utc: UtcDateTime
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    element_id: Identifier | None = None
    binding_path: Identifier | None = None
    element_name: Identifier | None = None
    element_type: Identifier | None = None
    nearest_named_ancestor: Identifier | None = None
    occurrence_count: int | None = Field(default=None, ge=0)
    window_seconds: float | None = Field(default=None, gt=0)
    frame_p95_ms: float | None = Field(default=None, ge=0)
    visual_count: int | None = Field(default=None, ge=0)
    subtree_node_count: int | None = Field(default=None, ge=0)


class CandidateClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: Identifier
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    supporting_evidence_ids: list[Identifier] = Field(min_length=1, max_length=20)
    counter_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    confidence: Confidence


class BindingCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: Identifier
    binding_path: Identifier
    target_property: Identifier
    element_type: Identifier
    element_name: Identifier | None = None


class AgentCorrelationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: list[NormalizedEvidenceSummary] = Field(max_length=50)
    candidate_claims: list[CandidateClaim] = Field(max_length=20)
    tool_calls_remaining: int = Field(ge=0, le=6)
    max_context_bytes: int = Field(gt=0, le=65_536)
    max_context_tokens: int = Field(gt=0, le=32_768)


def match_exact_element_id(
    left: NormalizedEvidenceSummary,
    right: NormalizedEvidenceSummary,
) -> Confidence | None:
    if left.element_id and left.element_id == right.element_id:
        return Confidence.HIGH
    return None


def match_unique_live_candidate(
    candidates: list[BindingCandidate],
) -> tuple[BindingCandidate, Confidence] | None:
    if len(candidates) == 1:
        return candidates[0], Confidence.HIGH
    return None


def match_normalized_binding_path(
    left: NormalizedEvidenceSummary,
    right: NormalizedEvidenceSummary,
) -> bool:
    if left.binding_path is None or right.binding_path is None:
        return False
    left_path = left.binding_path.strip()
    right_path = right.binding_path.strip()
    return bool(left_path) and left_path == right_path


def match_element_name_and_type(
    left: NormalizedEvidenceSummary,
    right: NormalizedEvidenceSummary,
) -> bool:
    return bool(
        left.element_name
        and left.element_type
        and left.element_name == right.element_name
        and left.element_type == right.element_type
    )


def match_nearest_named_ancestor(
    left: NormalizedEvidenceSummary,
    right: NormalizedEvidenceSummary,
) -> Confidence | None:
    if (
        left.nearest_named_ancestor
        and left.nearest_named_ancestor == right.nearest_named_ancestor
    ):
        return Confidence.MEDIUM
    return None


def match_time_window(
    left: NormalizedEvidenceSummary,
    right: NormalizedEvidenceSummary,
    max_delta_seconds: float = 10.0,
) -> bool:
    if left.app_session_id != right.app_session_id or max_delta_seconds < 0:
        return False
    delta_seconds = abs((left.observed_at_utc - right.observed_at_utc).total_seconds())
    return delta_seconds <= max_delta_seconds


def binding_errors_per_second(evidence: NormalizedEvidenceSummary) -> float | None:
    if evidence.occurrence_count is None or evidence.window_seconds is None:
        return None
    return evidence.occurrence_count / evidence.window_seconds


def same_session_frame_p95(
    binding: NormalizedEvidenceSummary,
    performance: NormalizedEvidenceSummary,
) -> float | None:
    if binding.app_session_id != performance.app_session_id:
        return None
    return performance.frame_p95_ms


def same_session_visual_metrics(
    binding: NormalizedEvidenceSummary,
    ui: NormalizedEvidenceSummary,
) -> tuple[int | None, int | None] | None:
    if binding.app_session_id != ui.app_session_id:
        return None
    return ui.visual_count, ui.subtree_node_count


def build_performance_amplifier_edge(
    binding: NormalizedEvidenceSummary,
    performance: NormalizedEvidenceSummary,
) -> EvidenceEdge | None:
    rate = binding_errors_per_second(binding)
    has_performance_metric = any(
        value is not None
        for value in (
            performance.frame_p95_ms,
            performance.visual_count,
            performance.subtree_node_count,
        )
    )
    if rate is None or rate <= 0 or not has_performance_metric:
        return None
    if not match_time_window(binding, performance):
        return None
    return EvidenceEdge(
        source_evidence_id=binding.evidence_id,
        target_evidence_id=performance.evidence_id,
        edge_type=EvidenceEdgeType.PERFORMANCE_AMPLIFIER,
    )


def map_correlation_confidence(
    *,
    exact_element: bool = False,
    unique_live_candidate: bool = False,
    independent_evidence_matches: int = 0,
    binding_path: bool = False,
    named_ancestor: bool = False,
    time_window: bool = False,
) -> Confidence:
    if exact_element or unique_live_candidate or independent_evidence_matches >= 2:
        return Confidence.HIGH
    if binding_path and named_ancestor and time_window:
        return Confidence.MEDIUM
    return Confidence.LOW


def can_propose_mutation(claim: CandidateClaim) -> bool:
    return claim.confidence is not Confidence.LOW


def build_agent_context(
    evidence: list[NormalizedEvidenceSummary],
    candidate_claims: list[CandidateClaim],
    *,
    tool_calls_remaining: int,
    max_context_bytes: int,
    max_context_tokens: int,
) -> AgentCorrelationContext:
    return AgentCorrelationContext(
        evidence=evidence,
        candidate_claims=candidate_claims,
        tool_calls_remaining=tool_calls_remaining,
        max_context_bytes=max_context_bytes,
        max_context_tokens=max_context_tokens,
    )
