using System.Text.Json;
using System.Windows;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed record PostBindingWindowSnapshot(
    DateTimeOffset StartedAtUtc,
    DateTimeOffset CompletedAtUtc,
    long OccurrenceCount,
    double ErrorsPerSecond);

internal sealed record PostPerformanceWindowSnapshot(
    DateTimeOffset StartedAtUtc,
    DateTimeOffset CompletedAtUtc,
    PerformanceFrameWindow Window,
    int VisualCount,
    bool VisualCountTruncated,
    string VisualScopeId);

public sealed partial class ReliabilitySensor
{
    internal static readonly TimeSpan PostActionObservationWindow = TimeSpan.FromSeconds(10);

    internal async Task<PostBindingWindowSnapshot> CapturePostBindingWindowAsync(
        CancellationToken cancellationToken,
        TimeSpan? observationWindow = null)
    {
        var aggregator = _bindingAggregator
            ?? throw new InvalidOperationException("Binding diagnostics are not configured.");
        var window = ResolveObservationWindow(observationWindow);

        var startedAt = DateTimeOffset.UtcNow;
        var beforeCount = aggregator.AcceptedCount;
        await Task.Delay(window, cancellationToken).ConfigureAwait(false);
        var completedAt = DateTimeOffset.UtcNow;
        var occurrenceCount = aggregator.AcceptedCount - beforeCount;
        return new PostBindingWindowSnapshot(
            startedAt,
            completedAt,
            occurrenceCount,
            occurrenceCount / window.TotalSeconds);
    }

    internal async Task<PostPerformanceWindowSnapshot> CapturePostPerformanceWindowAsync(
        CancellationToken cancellationToken,
        TimeSpan? observationWindow = null,
        DependencyObject? root = null)
    {
        PerformanceDiagnosticCollector collector;
        lock (_collectorLifecycleLock)
        {
            collector = _performanceCollector
                ?? throw new InvalidOperationException("Performance diagnostics are not installed.");
        }
        var window = ResolveObservationWindow(observationWindow);
        var startedAt = DateTimeOffset.UtcNow;
        var baseline = collector.FrameSampleCount;
        await Task.Delay(window, cancellationToken).ConfigureAwait(false);
        (int Count, bool Truncated, string ScopeId) CaptureVisualCount()
        {
            var resolvedRoot = root ?? Application.Current?.MainWindow
                ?? throw new InvalidOperationException("No WPF root element is available.");
            var visual = PerformanceVisualCounter.Count(resolvedRoot, collector.Options.MaxVisualNodes);
            return (visual.Count, visual.Truncated, _elementIds.GetOrCreate(resolvedRoot));
        }
        var visual = collector.Dispatcher.CheckAccess()
            ? CaptureVisualCount()
            : await collector.Dispatcher.InvokeAsync(CaptureVisualCount).Task
                .WaitAsync(cancellationToken).ConfigureAwait(false);
        return new PostPerformanceWindowSnapshot(
            startedAt,
            DateTimeOffset.UtcNow,
            collector.CaptureFrameWindowSince(baseline),
            visual.Count,
            visual.Truncated,
            visual.ScopeId);
    }

    internal async Task<string> CaptureAndQueuePostActionSnapshotAsync(
        DiagnosticCommand command,
        DependencyObject? root,
        CancellationToken cancellationToken,
        TimeSpan? observationWindow = null)
    {
        ArgumentNullException.ThrowIfNull(command);
        if (_bindingAggregator is null)
        {
            throw new InvalidOperationException("Binding diagnostics are not configured.");
        }
        lock (_collectorLifecycleLock)
        {
            if (_performanceCollector is null)
            {
                throw new InvalidOperationException("Performance diagnostics are not installed.");
            }
        }
        var window = ResolveObservationWindow(observationWindow);
        var bindingTask = CapturePostBindingWindowAsync(cancellationToken, window);
        var performanceTask = CapturePostPerformanceWindowAsync(cancellationToken, window, root);
        await Task.WhenAll(bindingTask, performanceTask).ConfigureAwait(false);
        var binding = await bindingTask.ConfigureAwait(false);
        var performance = await performanceTask.ConfigureAwait(false);
        var frames = performance.Window.FrameStatistics;
        var correlation = JsonSerializer.SerializeToElement(new
        {
            incident_id = command.IncidentId,
            command_id = command.CommandId,
            action_id = command.ActionId,
        });
        var payload = JsonSerializer.SerializeToElement(new
        {
            observation_window_ms = window.TotalMilliseconds,
            binding_occurrence_count = binding.OccurrenceCount,
            binding_errors_per_second = binding.ErrorsPerSecond,
            frame_statistics = new
            {
                sample_count = frames.SampleCount,
                average_milliseconds = frames.AverageMilliseconds,
                p50_milliseconds = frames.P50Milliseconds,
                p95_milliseconds = frames.P95Milliseconds,
                max_milliseconds = frames.MaxMilliseconds,
                over16_point7_milliseconds = frames.Over16Point7Milliseconds,
                over33_point3_milliseconds = frames.Over33Point3Milliseconds,
                over50_milliseconds = frames.Over50Milliseconds,
            },
            performance_sample_duration_ms = performance.Window.SampleDurationMilliseconds,
            performance_confidence = performance.Window.Confidence,
            visual_count = performance.VisualCount,
            visual_count_truncated = performance.VisualCountTruncated,
            visual_scope_id = performance.VisualScopeId,
        });
        if (!TryEnqueue(EventType.RecoveryResult, Severity.INFO, correlation, payload, out var envelope)
            || envelope is null)
        {
            throw new InvalidOperationException("Post-action snapshot could not be queued.");
        }
        return envelope.EventId;
    }

    private static TimeSpan ResolveObservationWindow(TimeSpan? observationWindow)
    {
        var window = observationWindow ?? PostActionObservationWindow;
        return window > TimeSpan.Zero && window <= TimeSpan.FromMinutes(1)
            ? window
            : throw new ArgumentOutOfRangeException(nameof(observationWindow));
    }
}
