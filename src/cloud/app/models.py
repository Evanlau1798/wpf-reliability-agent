from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be UTC")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(_utc)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    BINDING_AGGREGATE = "binding.aggregate"
    EXCEPTION_SUMMARY = "exception.summary"
    UI_SNAPSHOT = "ui.snapshot"
    PERFORMANCE_SAMPLE = "performance.sample"
    TOOL_RESULT = "tool.result"
    RECOVERY_RESULT = "recovery.result"


class DiagnosticTool(str, Enum):
    HEALTH_GET_SNAPSHOT = "health.get_snapshot"
    BINDING_GET_ERRORS = "binding.get_errors"
    BINDING_GET_LIVE_CANDIDATES = "binding.get_live_candidates"
    EXCEPTION_GET_RECENT = "exception.get_recent"
    UI_GET_SUBTREE = "ui.get_subtree"
    UI_GET_ELEMENT_DETAILS = "ui.get_element_details"
    PERFORMANCE_SAMPLE = "performance.sample"
    STATE_COMPARE_SNAPSHOTS = "state.compare_snapshots"
    SOURCE_LOOKUP_BINDING = "source.lookup_binding"
    RECOVERY_SET_FEATURE_FLAG = "recovery.set_feature_flag"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


class ResultStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class DecisionType(str, Enum):
    REQUEST_EVIDENCE = "REQUEST_EVIDENCE"
    PROPOSE_ACTION = "PROPOSE_ACTION"
    FINALIZE = "FINALIZE"
    NO_ACTION = "NO_ACTION"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class IncidentStatus(str, Enum):
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    FAILED_SAFE = "FAILED_SAFE"


class ClaimKind(str, Enum):
    FACT = "FACT"
    HYPOTHESIS = "HYPOTHESIS"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal["1.0"]

    def check_budget(self, maximum: int) -> None:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > maximum:
            raise ValueError(f"serialized contract exceeds {maximum} bytes")


class DiagnosticEnvelope(ContractModel):
    event_id: Identifier
    event_type: EventType
    severity: Severity
    timestamp_utc: UtcDateTime
    device_id: Identifier
    application_id: Identifier
    application_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    app_session_id: Identifier
    sequence_no: int = Field(ge=0)
    correlation: dict[str, Any]
    payload: dict[str, Any]
    redaction_profile: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    evidence_hash: Hash

    @model_validator(mode="after")
    def validate_contract(self) -> DiagnosticEnvelope:
        self.check_budget(65_536)
        return self


class DiagnosticCommand(ContractModel):
    command_id: Identifier
    incident_id: Identifier
    target_app_session_id: Identifier
    tool: DiagnosticTool
    arguments: dict[str, Any]
    arguments_hash: Hash
    risk_level: RiskLevel
    approval_id: Identifier | None = None
    idempotency_key: Identifier
    proposal_version: int | None = Field(default=None, ge=1)
    action_id: Identifier | None = None
    issued_at_utc: UtcDateTime
    expires_at_utc: UtcDateTime
    timeout_ms: int = Field(ge=100, le=120_000)

    @model_validator(mode="after")
    def validate_contract(self) -> DiagnosticCommand:
        if self.expires_at_utc <= self.issued_at_utc:
            raise ValueError("command expiry must be after issue time")
        if self.risk_level is RiskLevel.HIGH and self.approval_id is None:
            raise ValueError("HIGH command requires approval")
        if self.tool is DiagnosticTool.RECOVERY_SET_FEATURE_FLAG:
            if self.proposal_version is None or self.action_id is None:
                raise ValueError("feature recovery command requires binding references")
            expected = {
                "feature": "ExperimentalPeopleGrid",
                "enabled": False,
                "expected_current_value": True,
            }
            if self.risk_level is not RiskLevel.HIGH or self.arguments != expected:
                raise ValueError("feature recovery command is not exact or HIGH risk")
        if self.tool is DiagnosticTool.UI_GET_SUBTREE:
            ceilings = {"max_depth": 4, "max_nodes": 300, "max_children_per_node": 50}
            for key, ceiling in ceilings.items():
                value = self.arguments.get(key)
                if value is not None and (not isinstance(value, int) or value < 1 or value > ceiling):
                    raise ValueError(f"{key} exceeds local ceiling")
        self.check_budget(32_768)
        return self


class CommandError(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: Identifier
    message: Annotated[str, StringConstraints(max_length=1024)] | None = None


class CommandResult(ContractModel):
    command_id: Identifier
    incident_id: Identifier
    app_session_id: Identifier
    status: ResultStatus
    started_at_utc: UtcDateTime
    completed_at_utc: UtcDateTime
    result: dict[str, Any] | None = None
    result_hash: Hash
    truncated: bool
    error: CommandError | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> CommandResult:
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed timestamp cannot precede started timestamp")
        if self.status is not ResultStatus.SUCCEEDED and self.error is None:
            raise ValueError("non-success result requires an error")
        self.check_budget(131_072)
        return self


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claim: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    confidence: Confidence
    evidence_ids: list[Identifier]
    counter_evidence_ids: list[Identifier]


class NextCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool: DiagnosticTool
    arguments: dict[str, Any]


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool: Literal[DiagnosticTool.RECOVERY_SET_FEATURE_FLAG]
    arguments: dict[str, Any]
    evidence_ids: list[Identifier] = Field(min_length=1)
    expected_effect: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    rollback_plan: Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class PatchProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    target_file: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
    target_file_sha256: Hash
    target_line: int = Field(ge=1)
    unified_diff: Annotated[str, StringConstraints(min_length=1, max_length=32_768)]
    evidence_ids: list[Identifier] = Field(min_length=1, max_length=20)


class AgentDecision(ContractModel):
    decision: DecisionType
    hypotheses: list[Hypothesis] = Field(max_length=10)
    next_command: NextCommand | None = None
    proposed_action: ProposedAction | None = None
    patch_proposal: PatchProposal | None = None
    stop_reason: Annotated[str, StringConstraints(max_length=1024)] | None = None
    missing_evidence: list[Annotated[str, StringConstraints(min_length=1, max_length=256)]] = Field(
        max_length=10
    )

    @model_validator(mode="after")
    def validate_contract(self) -> AgentDecision:
        if self.decision is DecisionType.REQUEST_EVIDENCE:
            valid = self.next_command is not None and self.proposed_action is None
        elif self.decision is DecisionType.PROPOSE_ACTION:
            valid = self.proposed_action is not None and self.next_command is None
        else:
            valid = self.next_command is None and self.proposed_action is None
        if not valid:
            raise ValueError("decision must contain exactly one matching next step")
        self.check_budget(65_536)
        return self


class ApprovalRecord(ContractModel):
    approval_id: Identifier
    incident_id: Identifier
    proposal_version: int = Field(ge=1)
    evidence_snapshot_hash: Hash
    action_id: Identifier
    tool: Literal[DiagnosticTool.RECOVERY_SET_FEATURE_FLAG]
    canonical_arguments: dict[str, Any]
    canonical_arguments_hash: Hash
    target_app_session_id: Identifier
    policy_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    risk_level: Literal[RiskLevel.HIGH]
    expected_effect: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    rollback_plan: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    expires_at_utc: UtcDateTime
    status: ApprovalStatus
    approved_by: Identifier | None = None
    approved_at_utc: UtcDateTime | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> ApprovalRecord:
        if self.status is ApprovalStatus.APPROVED and (
            self.approved_by is None or self.approved_at_utc is None
        ):
            raise ValueError("approved record requires actor and timestamp")
        self.check_budget(32_768)
        return self


class TimelineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    timestamp_utc: UtcDateTime
    kind: Identifier
    actor: Identifier
    reference: Identifier


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    evidence_id: Identifier
    kind: Identifier
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ReportClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    fact_or_hypothesis: ClaimKind
    confidence: Confidence
    evidence_ids: list[Identifier] = Field(min_length=1)


class TemporaryMitigation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action_id: Identifier
    tool: Literal[DiagnosticTool.RECOVERY_SET_FEATURE_FLAG]
    approval_id: Identifier


class PermanentRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    source_fix_verified: bool = False
    patch_proposal: PatchProposal | None = None


class VerificationMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")
    metric_name: Identifier
    before: float
    after: float
    unit: Identifier
    evidence_ids: list[Identifier] = Field(min_length=1)


class ReportMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model_id: Identifier
    prompt_version: Identifier
    schema_version: Literal["1.0"]
    policy_version: Identifier
    reuse_revision: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class IncidentReport(ContractModel):
    incident_id: Identifier
    status: IncidentStatus
    severity: Severity
    confidence: Confidence
    timeline: list[TimelineItem]
    evidence: list[EvidenceItem]
    claims: list[ReportClaim]
    temporary_mitigation: TemporaryMitigation | None = None
    permanent_recommendation: PermanentRecommendation | None = None
    verification: list[VerificationMetric]
    metadata: ReportMetadata

    @model_validator(mode="after")
    def validate_contract(self) -> IncidentReport:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        known = set(evidence_ids)
        referenced = {item for claim in self.claims for item in claim.evidence_ids}
        referenced.update(item for metric in self.verification for item in metric.evidence_ids)
        if self.permanent_recommendation is not None and self.permanent_recommendation.patch_proposal is not None:
            referenced.update(self.permanent_recommendation.patch_proposal.evidence_ids)
        if not referenced.issubset(known):
            raise ValueError("report references unknown evidence IDs")
        if self.timeline != sorted(self.timeline, key=lambda item: item.timestamp_utc):
            raise ValueError("timeline must be ordered")
        if self.status is IncidentStatus.RESOLVED and (
            self.temporary_mitigation is not None
            or self.permanent_recommendation is None
            or not self.permanent_recommendation.source_fix_verified
        ):
            raise ValueError("temporary feature rollback must remain MITIGATED")
        if self.status is IncidentStatus.MITIGATED and not self.verification:
            raise ValueError("MITIGATED report requires post-action verification")
        self.check_budget(524_288)
        return self
