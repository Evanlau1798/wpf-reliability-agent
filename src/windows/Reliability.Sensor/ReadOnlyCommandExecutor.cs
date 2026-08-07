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

    public async Task<JsonElement> ExecuteAsync(
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return command.Tool switch
        {
            DiagnosticTool.HealthGetSnapshot => JsonSerializer.SerializeToElement(new
            {
                application_id = _sensor.ApplicationId,
                application_version = _sensor.ApplicationVersion,
                app_session_id = _sensor.AppSessionId,
                sensor_enabled = _sensor.IsEnabled,
                can_upload = _sensor.CanUpload,
                queued_event_count = _sensor.QueuedEventCount,
                dropped_event_count = _sensor.DroppedEventCount,
            }),
            DiagnosticTool.BindingGetErrors => JsonSerializer.SerializeToElement(new
            {
                aggregates = _sensor.GetRecentBindingAggregates(),
            }),
            DiagnosticTool.BindingGetLiveCandidates => JsonSerializer.SerializeToElement(new
            {
                candidates = (await _sensor.GetBindingLiveCandidatesAsync(
                    OptionalString(command.Arguments, "element_id"),
                    cancellationToken).ConfigureAwait(false))
                    .Select(candidate => new
                    {
                        element_id = candidate.ElementId,
                        binding_path = candidate.BindingPath,
                        target_property = candidate.TargetProperty,
                        element_type = candidate.ElementType,
                        element_name = candidate.ElementName,
                    }),
            }),
            DiagnosticTool.ExceptionGetRecent
                or DiagnosticTool.UiGetSubtree
                or DiagnosticTool.UiGetElementDetails
                or DiagnosticTool.PerformanceSample
                or DiagnosticTool.StateCompareSnapshots => throw new NotSupportedException(
                    "Read-only diagnostic tool is not implemented yet."),
            _ => throw new InvalidOperationException(
                "Command tool is not available to the read-only executor."),
        };
    }

    private static string? OptionalString(JsonElement arguments, string name) =>
        arguments.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.String
            ? value.GetString()
            : null;
}
