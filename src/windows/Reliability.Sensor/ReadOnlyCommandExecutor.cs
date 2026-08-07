using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed class ReadOnlyCommandExecutor
{
    public Task<JsonElement> ExecuteAsync(
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return command.Tool switch
        {
            DiagnosticTool.HealthGetSnapshot
                or DiagnosticTool.BindingGetErrors
                or DiagnosticTool.BindingGetLiveCandidates
                or DiagnosticTool.ExceptionGetRecent
                or DiagnosticTool.UiGetSubtree
                or DiagnosticTool.UiGetElementDetails
                or DiagnosticTool.PerformanceSample
                or DiagnosticTool.StateCompareSnapshots => Task.FromException<JsonElement>(
                    new NotSupportedException("Read-only diagnostic tool is not implemented yet.")),
            _ => Task.FromException<JsonElement>(
                new InvalidOperationException("Command tool is not available to the read-only executor.")),
        };
    }
}
