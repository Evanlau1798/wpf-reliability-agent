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


class CandidateClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: Identifier
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    supporting_evidence_ids: list[Identifier] = Field(min_length=1, max_length=20)
    counter_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    confidence: Confidence
