using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed class ReadOnlyCommandExecutor
{
    private readonly ReliabilitySensor _sensor;

    public ReadOnlyCommandExecutor(ReliabilitySensor sensor)
    {
        _sensor = sensor;
    }

    public Task<JsonElement> ExecuteAsync(
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return command.Tool switch
        {
            DiagnosticTool.HealthGetSnapshot => Task.FromResult(JsonSerializer.SerializeToElement(new
            {
                application_id = _sensor.ApplicationId,
                application_version = _sensor.ApplicationVersion,
                app_session_id = _sensor.AppSessionId,
                sensor_enabled = _sensor.IsEnabled,
                can_upload = _sensor.CanUpload,
                queued_event_count = _sensor.QueuedEventCount,
                dropped_event_count = _sensor.DroppedEventCount,
            })),
            DiagnosticTool.BindingGetErrors => Task.FromResult(JsonSerializer.SerializeToElement(new
            {
                aggregates = _sensor.GetRecentBindingAggregates(),
            })),
            DiagnosticTool.BindingGetLiveCandidates
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
