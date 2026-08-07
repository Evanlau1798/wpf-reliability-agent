using Reliability.Contracts;

namespace Reliability.Sensor;

internal static class MutationCommandVerifier
{
    internal static bool HasRequiredRisk(DiagnosticCommand command) =>
        command.Tool is DiagnosticTool.RecoverySetFeatureFlag
        && command.RiskLevel is RiskLevel.HIGH;
}
