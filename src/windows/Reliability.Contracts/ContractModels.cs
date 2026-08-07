using System.Text.Json;
using System.Text.Json.Serialization;

namespace Reliability.Contracts;

[JsonConverter(typeof(EventTypeJsonConverter))]
public enum EventType
{
    BindingAggregate,
    ExceptionSummary,
    UiSnapshot,
    PerformanceSample,
    ToolResult,
    RecoveryResult,
}

public enum Severity
{
    INFO,
    WARNING,
    ERROR,
    CRITICAL,
}

[JsonConverter(typeof(DiagnosticToolJsonConverter))]
public enum DiagnosticTool
{
    HealthGetSnapshot,
    BindingGetErrors,
    BindingGetLiveCandidates,
    ExceptionGetRecent,
    UiGetSubtree,
    UiGetElementDetails,
    PerformanceSample,
    StateCompareSnapshots,
    RecoverySetFeatureFlag,
}

public enum RiskLevel
{
    LOW,
    MEDIUM,
    HIGH,
    BLOCKED,
}

public enum ResultStatus
{
    SUCCEEDED,
    FAILED,
    REJECTED,
    EXPIRED,
}

public enum DecisionType
{
    REQUEST_EVIDENCE,
    PROPOSE_ACTION,
    FINALIZE,
    NO_ACTION,
}

public enum Confidence
{
    LOW,
    MEDIUM,
    HIGH,
}

public enum ApprovalStatus
{
    PENDING,
    APPROVED,
    REJECTED,
    EXPIRED,
}

public enum IncidentStatus
{
    MITIGATED,
    RESOLVED,
    CLOSED,
    REJECTED,
    FAILED_SAFE,
}

public enum ClaimKind
{
    FACT,
    HYPOTHESIS,
}

public sealed record DiagnosticEnvelope(
    string SchemaVersion,
    string EventId,
    EventType EventType,
    Severity Severity,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset TimestampUtc,
    string DeviceId,
    string ApplicationId,
    string ApplicationVersion,
    string AppSessionId,
    long SequenceNo,
    JsonElement Correlation,
    JsonElement Payload,
    string RedactionProfile,
    string EvidenceHash);

public sealed record DiagnosticCommand(
    string SchemaVersion,
    string CommandId,
    string IncidentId,
    string TargetAppSessionId,
    DiagnosticTool Tool,
    JsonElement Arguments,
    string ArgumentsHash,
    RiskLevel RiskLevel,
    string? ApprovalId,
    string IdempotencyKey,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset IssuedAtUtc,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset ExpiresAtUtc,
    int TimeoutMs,
    int? ProposalVersion = null,
    string? ActionId = null);

public sealed record CommandError(string Code, string? Message);

public sealed record CommandResult(
    string SchemaVersion,
    string CommandId,
    string IncidentId,
    string AppSessionId,
    ResultStatus Status,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset StartedAtUtc,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset CompletedAtUtc,
    JsonElement? Result,
    string ResultHash,
    bool Truncated,
    CommandError? Error);

public sealed record Hypothesis(
    string Claim,
    Confidence Confidence,
    IReadOnlyList<string> EvidenceIds,
    IReadOnlyList<string> CounterEvidenceIds);

public sealed record NextCommand(DiagnosticTool Tool, JsonElement Arguments);

public sealed record ProposedAction(
    DiagnosticTool Tool,
    JsonElement Arguments,
    IReadOnlyList<string> EvidenceIds,
    string ExpectedEffect,
    string RollbackPlan);

public sealed record AgentDecision(
    string SchemaVersion,
    DecisionType Decision,
    IReadOnlyList<Hypothesis> Hypotheses,
    NextCommand? NextCommand,
    ProposedAction? ProposedAction,
    string? StopReason,
    IReadOnlyList<string> MissingEvidence);

public sealed record ApprovalRecord(
    string SchemaVersion,
    string ApprovalId,
    string IncidentId,
    int ProposalVersion,
    string EvidenceSnapshotHash,
    string ActionId,
    DiagnosticTool Tool,
    JsonElement CanonicalArguments,
    string CanonicalArgumentsHash,
    string TargetAppSessionId,
    string PolicyVersion,
    RiskLevel RiskLevel,
    string ExpectedEffect,
    string RollbackPlan,
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset ExpiresAtUtc,
    ApprovalStatus Status,
    string? ApprovedBy,
    [property: JsonConverter(typeof(NullableUtcDateTimeOffsetJsonConverter))] DateTimeOffset? ApprovedAtUtc);

public sealed record TimelineItem(
    [property: JsonConverter(typeof(UtcDateTimeOffsetJsonConverter))] DateTimeOffset TimestampUtc,
    string Kind,
    string Actor,
    string Reference);

public sealed record EvidenceItem(string EvidenceId, string Kind, string Summary);

public sealed record ReportClaim(
    string Text,
    ClaimKind FactOrHypothesis,
    Confidence Confidence,
    IReadOnlyList<string> EvidenceIds);

public sealed record TemporaryMitigation(string ActionId, DiagnosticTool Tool, string ApprovalId);

public sealed record PermanentRecommendation(string Summary, bool SourceFixVerified = false);

public sealed record VerificationMetric(
    string MetricName,
    double Before,
    double After,
    string Unit,
    IReadOnlyList<string> EvidenceIds);

public sealed record ReportMetadata(
    string ModelId,
    string PromptVersion,
    string SchemaVersion,
    string PolicyVersion,
    string ReuseRevision);

public sealed record IncidentReport(
    string SchemaVersion,
    string IncidentId,
    IncidentStatus Status,
    Severity Severity,
    Confidence Confidence,
    IReadOnlyList<TimelineItem> Timeline,
    IReadOnlyList<EvidenceItem> Evidence,
    IReadOnlyList<ReportClaim> Claims,
    TemporaryMitigation? TemporaryMitigation,
    PermanentRecommendation? PermanentRecommendation,
    IReadOnlyList<VerificationMetric> Verification,
    ReportMetadata Metadata);
