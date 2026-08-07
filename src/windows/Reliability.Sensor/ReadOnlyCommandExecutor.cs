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
            DiagnosticTool.ExceptionGetRecent => JsonSerializer.SerializeToElement(new
            {
                summaries = _sensor.GetRecentExceptionSummaries(),
            }),
            DiagnosticTool.UiGetSubtree => await ExecuteUiGetSubtreeAsync(
                command.Arguments,
                cancellationToken).ConfigureAwait(false),
            DiagnosticTool.UiGetElementDetails
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

    private async Task<JsonElement> ExecuteUiGetSubtreeAsync(
        JsonElement arguments,
        CancellationToken cancellationToken)
    {
        var result = await _sensor.CaptureUiTreeByIdAsync(
            RequiredString(arguments, "element_id"),
            new UiTreeOptions
            {
                MaxDepth = BoundedInt(arguments, "max_depth", 4, 0, 4),
                MaxNodes = BoundedInt(arguments, "max_nodes", 300, 1, 300),
                MaxChildrenPerNode = BoundedInt(arguments, "max_children_per_node", 50, 1, 50),
            },
            cancellationToken).ConfigureAwait(false);
        return JsonSerializer.SerializeToElement(new
        {
            succeeded = result.Succeeded,
            nodes = result.Nodes.Select(node => new
            {
                element_id = node.ElementId,
                parent_id = node.ParentId,
                type = node.Type,
                name = node.Name,
                depth = node.Depth,
                child_count = node.ChildCount,
                is_visible = node.IsVisible,
                is_enabled = node.IsEnabled,
                has_binding_error = node.HasBindingError,
            }),
            truncated = result.Truncated,
            omitted_node_count = result.OmittedNodeCount,
            error = result.Error is null ? null : new { code = result.Error.Code, message = result.Error.Message },
        });
    }

    private static string RequiredString(JsonElement arguments, string name) =>
        OptionalString(arguments, name) is { Length: > 0 } value
            ? value
            : throw new InvalidOperationException("Command arguments are invalid.");

    private static int BoundedInt(JsonElement arguments, string name, int defaultValue, int minimum, int ceiling)
    {
        if (!arguments.TryGetProperty(name, out var value))
        {
            return defaultValue;
        }
        if (!value.TryGetInt32(out var requested) || requested < minimum)
        {
            throw new InvalidOperationException("Command arguments are invalid.");
        }
        return Math.Min(requested, ceiling);
    }
}
