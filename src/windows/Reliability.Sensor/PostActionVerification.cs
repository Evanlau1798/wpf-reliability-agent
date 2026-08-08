using System.Windows;

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
    bool VisualCountTruncated);

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
        (int Count, bool Truncated) CaptureVisualCount()
        {
            var resolvedRoot = root ?? Application.Current?.MainWindow
                ?? throw new InvalidOperationException("No WPF root element is available.");
            return PerformanceVisualCounter.Count(resolvedRoot, collector.Options.MaxVisualNodes);
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
            visual.Truncated);
    }

    private static TimeSpan ResolveObservationWindow(TimeSpan? observationWindow)
    {
        var window = observationWindow ?? PostActionObservationWindow;
        return window > TimeSpan.Zero && window <= TimeSpan.FromMinutes(1)
            ? window
            : throw new ArgumentOutOfRangeException(nameof(observationWindow));
    }
}
