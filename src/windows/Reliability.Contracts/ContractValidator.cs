using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Reliability.Contracts;

public static partial class ContractValidator
{
    public static bool ValidateFixture(string path)
    {
        try
        {
            var json = File.ReadAllText(path);
            var name = Path.GetFileName(path);
            return name switch
            {
                _ when name.StartsWith("diagnostic-envelope-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.DiagnosticEnvelope), json, 65_536),
                _ when name.StartsWith("diagnostic-command-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.DiagnosticCommand), json, 32_768),
                _ when name.StartsWith("command-result-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.CommandResult), json, 131_072),
                _ when name.StartsWith("agent-decision-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.AgentDecision), json, 65_536),
                _ when name.StartsWith("approval-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.ApprovalRecord), json, 32_768),
                _ when name.StartsWith("incident-report-", StringComparison.Ordinal) =>
                    Validate(Deserialize(json, ContractJsonContext.Default.IncidentReport), json, 524_288),
                _ => false,
            };
        }
        catch (Exception exception) when (
            exception is JsonException or InvalidOperationException or ArgumentException)
        {
            return false;
        }
    }

    public static bool Validate(DiagnosticEnvelope value, string json = "", int budget = 65_536) =>
        WithinBudget(json, budget)
        && Common(value.SchemaVersion, value.EventId, value.TimestampUtc, value.EvidenceHash)
        && NonEmpty(value.DeviceId, value.ApplicationId, value.ApplicationVersion, value.AppSessionId, value.RedactionProfile)
        && value.SequenceNo >= 0
        && value.Correlation.ValueKind is JsonValueKind.Object
        && value.Payload.ValueKind is JsonValueKind.Object;

    public static bool Validate(DiagnosticCommand value, string json = "", int budget = 32_768)
    {
        if (!WithinBudget(json, budget)
            || !Common(value.SchemaVersion, value.CommandId, value.IssuedAtUtc, value.ArgumentsHash)
            || !NonEmpty(value.IncidentId, value.TargetAppSessionId, value.IdempotencyKey)
            || value.ExpiresAtUtc <= value.IssuedAtUtc
            || value.ExpiresAtUtc.Offset != TimeSpan.Zero
            || value.TimeoutMs is < 100 or > 120_000
            || value.Arguments.ValueKind is not JsonValueKind.Object
            || value.RiskLevel is RiskLevel.HIGH && string.IsNullOrWhiteSpace(value.ApprovalId))
        {
            return false;
        }

        if (value.Tool is DiagnosticTool.RecoverySetFeatureFlag)
        {
            return value.RiskLevel is RiskLevel.HIGH
                && value.ProposalVersion is >= 1
                && !string.IsNullOrWhiteSpace(value.ActionId)
                && ExactFeatureArguments(value.Arguments);
        }

        if (value.Tool is DiagnosticTool.UiGetSubtree)
        {
            return WithinOptionalCeiling(value.Arguments, "max_depth", 4)
                && WithinOptionalCeiling(value.Arguments, "max_nodes", 300)
                && WithinOptionalCeiling(value.Arguments, "max_children_per_node", 50);
        }

        return true;
    }

    public static bool Validate(CommandResult value, string json = "", int budget = 131_072) =>
        WithinBudget(json, budget)
        && Common(value.SchemaVersion, value.CommandId, value.StartedAtUtc, value.ResultHash)
        && NonEmpty(value.IncidentId, value.AppSessionId)
        && value.CompletedAtUtc.Offset == TimeSpan.Zero
        && value.CompletedAtUtc >= value.StartedAtUtc
        && (value.Status is ResultStatus.SUCCEEDED || value.Error is not null && NonEmpty(value.Error.Code));

    public static bool Validate(AgentDecision value, string json = "", int budget = 65_536)
    {
        if (!WithinBudget(json, budget)
            || value.SchemaVersion != "1.0"
            || value.Hypotheses is null
            || value.MissingEvidence is null
            || value.Hypotheses.Count > 10
            || value.MissingEvidence.Count > 10)
        {
            return false;
        }

        return value.Decision switch
        {
            DecisionType.REQUEST_EVIDENCE => value.NextCommand is not null && value.ProposedAction is null,
            DecisionType.PROPOSE_ACTION => value.ProposedAction is not null
                && value.NextCommand is null
                && value.ProposedAction.Tool is DiagnosticTool.RecoverySetFeatureFlag
                && value.ProposedAction.EvidenceIds.Count > 0
                && NonEmpty(value.ProposedAction.ExpectedEffect, value.ProposedAction.RollbackPlan),
            DecisionType.FINALIZE or DecisionType.NO_ACTION =>
                value.NextCommand is null && value.ProposedAction is null,
            _ => false,
        };
    }

    public static bool Validate(ApprovalRecord value, string json = "", int budget = 32_768) =>
        WithinBudget(json, budget)
        && value.SchemaVersion == "1.0"
        && NonEmpty(
            value.ApprovalId,
            value.IncidentId,
            value.ActionId,
            value.TargetAppSessionId,
            value.PolicyVersion,
            value.ExpectedEffect,
            value.RollbackPlan)
        && value.ProposalVersion >= 1
        && HashPattern().IsMatch(value.EvidenceSnapshotHash ?? string.Empty)
        && HashPattern().IsMatch(value.CanonicalArgumentsHash ?? string.Empty)
        && value.Tool is DiagnosticTool.RecoverySetFeatureFlag
        && value.RiskLevel is RiskLevel.HIGH
        && value.ExpiresAtUtc.Offset == TimeSpan.Zero
        && (value.Status is not ApprovalStatus.APPROVED
            || NonEmpty(value.ApprovedBy) && value.ApprovedAtUtc?.Offset == TimeSpan.Zero);

    public static bool Validate(IncidentReport value, string json = "", int budget = 524_288)
    {
        if (!WithinBudget(json, budget)
            || value.SchemaVersion != "1.0"
            || !NonEmpty(value.IncidentId)
            || value.Timeline is null
            || value.Evidence is null
            || value.Claims is null
            || value.Verification is null
            || value.Metadata is null
            || !Hash40Pattern().IsMatch(value.Metadata.ReuseRevision ?? string.Empty))
        {
            return false;
        }

        var ids = value.Evidence.Select(item => item.EvidenceId).ToList();
        if (ids.Any(string.IsNullOrWhiteSpace) || ids.Count != ids.Distinct(StringComparer.Ordinal).Count())
        {
            return false;
        }

        var known = ids.ToHashSet(StringComparer.Ordinal);
        var referenced = value.Claims.SelectMany(item => item.EvidenceIds)
            .Concat(value.Verification.SelectMany(item => item.EvidenceIds));
        if (referenced.Any(item => !known.Contains(item)))
        {
            return false;
        }

        if (!value.Timeline.SequenceEqual(value.Timeline.OrderBy(item => item.TimestampUtc)))
        {
            return false;
        }

        if (value.Status is IncidentStatus.RESOLVED
            && (value.TemporaryMitigation is not null
                || value.PermanentRecommendation is null
                || !value.PermanentRecommendation.SourceFixVerified))
        {
            return false;
        }

        return value.Status is not IncidentStatus.MITIGATED || value.Verification.Count > 0;
    }

    private static T Deserialize<T>(string json, System.Text.Json.Serialization.Metadata.JsonTypeInfo<T> typeInfo) =>
        JsonSerializer.Deserialize(json, typeInfo)
        ?? throw new JsonException($"Unable to deserialize {typeof(T).Name}.");

    private static bool Common(string schemaVersion, string id, DateTimeOffset timestamp, string hash) =>
        schemaVersion == "1.0"
        && NonEmpty(id)
        && timestamp.Offset == TimeSpan.Zero
        && HashPattern().IsMatch(hash ?? string.Empty);

    private static bool NonEmpty(params string?[] values) =>
        values.All(value => !string.IsNullOrWhiteSpace(value));

    private static bool WithinBudget(string json, int maximum) =>
        string.IsNullOrEmpty(json) || Encoding.UTF8.GetByteCount(json) <= maximum;

    private static bool WithinOptionalCeiling(JsonElement arguments, string name, int ceiling) =>
        !arguments.TryGetProperty(name, out var value)
        || value.ValueKind is JsonValueKind.Number
            && value.TryGetInt32(out var number)
            && number is >= 1
            && number <= ceiling;

    private static bool ExactFeatureArguments(JsonElement arguments)
    {
        var names = arguments.EnumerateObject().Select(property => property.Name).ToHashSet(StringComparer.Ordinal);
        return names.SetEquals(["feature", "enabled", "expected_current_value"])
            && arguments.GetProperty("feature").GetString() == "ExperimentalPeopleGrid"
            && arguments.GetProperty("enabled").ValueKind is JsonValueKind.False
            && arguments.GetProperty("expected_current_value").ValueKind is JsonValueKind.True;
    }

    [GeneratedRegex("^[0-9a-f]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex HashPattern();

    [GeneratedRegex("^[0-9a-f]{40}$", RegexOptions.CultureInvariant)]
    private static partial Regex Hash40Pattern();
}
