using Reliability.Contracts;

namespace Reliability.Sensor;

internal static class MutationCommandVerifier
{
    private const string RecoveryToolName = "recovery.set_feature_flag";

    internal static bool HasRequiredRisk(DiagnosticCommand command) =>
        command.Tool is DiagnosticTool.RecoverySetFeatureFlag
        && command.RiskLevel is RiskLevel.HIGH;

    internal static bool HasApprovalReference(DiagnosticCommand command) =>
        !string.IsNullOrWhiteSpace(command.ApprovalId);

    internal static bool HasExactBindingReferences(DiagnosticCommand command)
    {
        if (command.Tool is not DiagnosticTool.RecoverySetFeatureFlag
            || command.ProposalVersion is not int proposalVersion
            || proposalVersion < 1
            || string.IsNullOrWhiteSpace(command.ActionId)
            || !string.Equals(
                CanonicalJson.Hash(command.Arguments),
                command.ArgumentsHash,
                StringComparison.Ordinal))
        {
            return false;
        }

        var commandIdentityKey = CanonicalJson.Hash(new Dictionary<string, object>
        {
            ["incident_id"] = command.IncidentId,
            ["proposal_version"] = proposalVersion,
            ["tool"] = RecoveryToolName,
            ["arguments_hash"] = command.ArgumentsHash,
        });
        var mutationExecutionKey = CanonicalJson.Hash(new Dictionary<string, object>
        {
            ["incident_id"] = command.IncidentId,
            ["action_id"] = command.ActionId,
            ["arguments_hash"] = command.ArgumentsHash,
        });

        return string.Equals(
                command.CommandId,
                $"cmd-{commandIdentityKey}",
                StringComparison.Ordinal)
            && string.Equals(
                command.IdempotencyKey,
                mutationExecutionKey,
                StringComparison.Ordinal);
    }
}
