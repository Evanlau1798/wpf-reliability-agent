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


class NormalizedEvidenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Identifier
    kind: Identifier
    app_session_id: Identifier
    observed_at_utc: UtcDateTime
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    element_id: Identifier | None = None
    binding_path: Identifier | None = None


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
