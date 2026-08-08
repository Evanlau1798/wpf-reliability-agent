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
        ValidateArguments(command.Tool, command.Arguments);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromMilliseconds(command.TimeoutMs));
        try
        {
            return await ExecuteCoreAsync(command, timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (
            !cancellationToken.IsCancellationRequested && timeout.IsCancellationRequested)
        {
            return JsonSerializer.SerializeToElement(new
            {
                succeeded = false,
                error = new
                {
                    code = "TIMEOUT",
                    message = "Command execution timed out.",
                },
            });
        }
    }

    private async Task<JsonElement> ExecuteCoreAsync(
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
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
            DiagnosticTool.UiGetElementDetails => await ExecuteUiGetElementDetailsAsync(
                command.Arguments,
                cancellationToken).ConfigureAwait(false),
            DiagnosticTool.PerformanceSample => await ExecutePerformanceSampleAsync(
                command.Arguments,
                cancellationToken).ConfigureAwait(false),
            DiagnosticTool.StateCompareSnapshots => StateSnapshotComparer.Compare(
                RequiredObject(command.Arguments, "before"),
                RequiredObject(command.Arguments, "after")),
            DiagnosticTool.SourceLookupBinding => ExecuteSourceLookupBinding(command.Arguments),
            _ => throw new InvalidOperationException(
                "Command tool is not available to the read-only executor."),
        };
    }

    private static string? OptionalString(JsonElement arguments, string name) =>
        arguments.TryGetProperty(name, out var value) && value.ValueKind is JsonValueKind.String
            ? value.GetString()
            : null;

    private static void ValidateArguments(DiagnosticTool tool, JsonElement arguments)
    {
        IReadOnlyCollection<string> allowed = tool switch
        {
            DiagnosticTool.HealthGetSnapshot => [],
            DiagnosticTool.BindingGetErrors => [],
            DiagnosticTool.BindingGetLiveCandidates => ["element_id"],
            DiagnosticTool.ExceptionGetRecent => [],
            DiagnosticTool.UiGetSubtree => ["element_id", "max_depth", "max_nodes", "max_children_per_node"],
            DiagnosticTool.UiGetElementDetails => ["element_id", "fields"],
            DiagnosticTool.PerformanceSample => ["element_id"],
            DiagnosticTool.StateCompareSnapshots => ["before", "after"],
            DiagnosticTool.SourceLookupBinding => ["key", "binding_path", "target_property"],
            _ => throw new InvalidOperationException(
                "Command tool is not available to the read-only executor."),
        };
        if (arguments.EnumerateObject().Any(property => !allowed.Contains(property.Name, StringComparer.Ordinal)))
        {
            throw new InvalidOperationException("Command arguments are invalid.");
        }
    }

    private JsonElement ExecuteSourceLookupBinding(JsonElement arguments)
    {
        var hasKey = arguments.TryGetProperty("key", out _);
        var hasBindingPath = arguments.TryGetProperty("binding_path", out _);
        var hasTargetProperty = arguments.TryGetProperty("target_property", out _);
        string? key = null;
        string? bindingPath = null;
        string? targetProperty = null;
        if (hasKey && !hasBindingPath && !hasTargetProperty)
        {
            key = RequiredString(arguments, "key");
        }
        else if (!hasKey && hasBindingPath && hasTargetProperty)
        {
            bindingPath = RequiredString(arguments, "binding_path");
            targetProperty = RequiredString(arguments, "target_property");
        }
        else
        {
            throw new InvalidOperationException("Command arguments are invalid.");
        }

        return JsonSerializer.SerializeToElement(new
        {
            matches = _sensor.LookupSourceBindings(key, bindingPath, targetProperty).Select(entry => new
            {
                key = entry.Key,
                file = entry.File,
                line = entry.Line,
                column = entry.Column,
                window_type = entry.WindowType,
                named_ancestors = entry.NamedAncestors,
                element_type = entry.ElementType,
                element_name = entry.ElementName,
                target_property = entry.TargetProperty,
                binding_path = entry.BindingPath,
                unsupported_reason = entry.UnsupportedReason,
                file_sha256 = entry.FileSha256,
                build_commit = entry.BuildCommit,
            }),
        });
    }

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

    private async Task<JsonElement> ExecuteUiGetElementDetailsAsync(
        JsonElement arguments,
        CancellationToken cancellationToken)
    {
        var result = await _sensor.GetUiElementDetailsAsync(
            RequiredString(arguments, "element_id"),
            OptionalStringArray(arguments, "fields"),
            cancellationToken).ConfigureAwait(false);
        return JsonSerializer.SerializeToElement(new
        {
            succeeded = result.Succeeded,
            details = result.Details is null ? null : new
            {
                element_id = result.Details.ElementId,
                type = result.Details.Type,
                name = result.Details.Name,
                is_visible = result.Details.IsVisible,
                is_enabled = result.Details.IsEnabled,
                layout = new
                {
                    actual_width = result.Details.Layout.ActualWidth,
                    actual_height = result.Details.Layout.ActualHeight,
                },
                binding_summary = new
                {
                    has_known_error = result.Details.BindingSummary.HasKnownError,
                    error_path = result.Details.BindingSummary.ErrorPath,
                },
            },
            error = result.Error is null ? null : new { code = result.Error.Code, message = result.Error.Message },
        });
    }

    private static IReadOnlyCollection<string>? OptionalStringArray(JsonElement arguments, string name)
    {
        if (!arguments.TryGetProperty(name, out var value))
        {
            return null;
        }
        if (value.ValueKind is not JsonValueKind.Array
            || value.EnumerateArray().Any(item => item.ValueKind is not JsonValueKind.String))
        {
            throw new InvalidOperationException("Command arguments are invalid.");
        }
        return value.EnumerateArray().Select(item => item.GetString()!).ToArray();
    }

    private static JsonElement RequiredObject(JsonElement arguments, string name)
    {
        if (!arguments.TryGetProperty(name, out var value) || value.ValueKind is not JsonValueKind.Object)
        {
            throw new InvalidOperationException("Command arguments are invalid.");
        }
        return value;
    }

    private async Task<JsonElement> ExecutePerformanceSampleAsync(
        JsonElement arguments,
        CancellationToken cancellationToken)
    {
        var result = await _sensor.CapturePerformanceSampleByIdAsync(
            OptionalString(arguments, "element_id"),
            cancellationToken).ConfigureAwait(false);
        return JsonSerializer.SerializeToElement(new
        {
            succeeded = result.Succeeded,
            frame_statistics = new
            {
                sample_count = result.FrameStatistics.SampleCount,
                average_ms = result.FrameStatistics.AverageMilliseconds,
                p50_ms = result.FrameStatistics.P50Milliseconds,
                p95_ms = result.FrameStatistics.P95Milliseconds,
                max_ms = result.FrameStatistics.MaxMilliseconds,
                over_16_7_ms = result.FrameStatistics.Over16Point7Milliseconds,
                over_33_3_ms = result.FrameStatistics.Over33Point3Milliseconds,
                over_50_ms = result.FrameStatistics.Over50Milliseconds,
            },
            sample_duration_ms = result.SampleDurationMilliseconds,
            confidence = result.Confidence.ToString(),
            heartbeat_delay_ms = result.HeartbeatDelayMilliseconds,
            visual_count = result.VisualCount,
            visual_count_truncated = result.VisualCountTruncated,
            visual_scope_id = result.VisualScopeId,
            error = result.Error is null ? null : new { code = result.Error.Code, message = result.Error.Message },
        });
    }
}
