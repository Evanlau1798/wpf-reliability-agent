using System.Net.Http;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed partial class ReliabilitySensor
{
    internal static async Task RunCommandPollerAsync(
        TelemetryApiClient client,
        string deviceId,
        string appSessionId,
        Func<DiagnosticCommand, CancellationToken, Task> handleCommand,
        CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                var command = await client.LeaseCommandAsync(
                    deviceId,
                    appSessionId,
                    cancellationToken).ConfigureAwait(false);
                if (command is not null
                    && string.Equals(
                        command.TargetAppSessionId,
                        appSessionId,
                        StringComparison.Ordinal)
                    && command.ExpiresAtUtc > DateTimeOffset.UtcNow
                    && IsReadOnlyTool(command.Tool))
                {
                    await handleCommand(command, cancellationToken).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception) when (exception is HttpRequestException or JsonException)
            {
                await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken).ConfigureAwait(false);
            }
        }
    }

    private static bool IsReadOnlyTool(DiagnosticTool tool) => tool is
        DiagnosticTool.HealthGetSnapshot
        or DiagnosticTool.BindingGetErrors
        or DiagnosticTool.BindingGetLiveCandidates
        or DiagnosticTool.ExceptionGetRecent
        or DiagnosticTool.UiGetSubtree
        or DiagnosticTool.UiGetElementDetails
        or DiagnosticTool.PerformanceSample
        or DiagnosticTool.StateCompareSnapshots;
}
