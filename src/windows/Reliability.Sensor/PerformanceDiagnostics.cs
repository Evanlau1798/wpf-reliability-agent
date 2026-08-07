using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed record PerformanceOptions
{
    public TimeSpan HeartbeatInterval { get; init; } = TimeSpan.FromSeconds(1);

    public TimeSpan SampleCooldown { get; init; } = TimeSpan.FromSeconds(1);

    public int MaxVisualNodes { get; init; } = 500;
}

public sealed record FrameStatistics(
    int SampleCount,
    double AverageMilliseconds,
    double P50Milliseconds,
    double P95Milliseconds,
    double MaxMilliseconds,
    int Over16Point7Milliseconds,
    int Over33Point3Milliseconds,
    int Over50Milliseconds);

public sealed record PerformanceDiagnosticError(string Code, string Message);

public sealed record PerformanceSampleResult(
    bool Succeeded,
    FrameStatistics FrameStatistics,
    double SampleDurationMilliseconds,
    Confidence Confidence,
    double HeartbeatDelayMilliseconds,
    int VisualCount,
    bool VisualCountTruncated,
    PerformanceDiagnosticError? Error);

internal sealed class FixedCircularBuffer<T>
{
    private readonly object _gate = new();
    private readonly T[] _items;
    private int _next;

    public FixedCircularBuffer(int capacity)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(capacity);
        _items = new T[capacity];
    }

    public int Count { get; private set; }

    public void Add(T item)
    {
        lock (_gate)
        {
            _items[_next] = item;
            _next = (_next + 1) % _items.Length;
            Count = Math.Min(Count + 1, _items.Length);
        }
    }

    public IReadOnlyList<T> Snapshot()
    {
        lock (_gate)
        {
            var snapshot = new T[Count];
            var first = (_next - Count + _items.Length) % _items.Length;
            for (var index = 0; index < Count; index++)
            {
                snapshot[index] = _items[(first + index) % _items.Length];
            }

            return snapshot;
        }
    }
}

internal static class PerformanceStatisticsCalculator
{
    public static FrameStatistics Calculate(IReadOnlyList<double> samples)
    {
        if (samples.Count == 0)
        {
            return new FrameStatistics(0, 0, 0, 0, 0, 0, 0, 0);
        }

        var sorted = samples.Order().ToArray();
        return new FrameStatistics(
            sorted.Length,
            sorted.Average(),
            Percentile(sorted, 0.50),
            Percentile(sorted, 0.95),
            sorted[^1],
            sorted.Count(value => value > 16.7),
            sorted.Count(value => value > 33.3),
            sorted.Count(value => value > 50));
    }

    private static double Percentile(IReadOnlyList<double> sorted, double percentile) =>
        sorted[Math.Max(0, (int)Math.Ceiling(percentile * sorted.Count) - 1)];
}

internal sealed class PerformanceDiagnosticCollector : IDisposable
{
    private const int FrameCapacity = 120;
    private readonly object _metricGate = new();
    private readonly FixedCircularBuffer<double> _frameIntervals = new(FrameCapacity);
    private readonly Dispatcher _dispatcher;
    private readonly PerformanceOptions _options;
    private readonly DispatcherTimer _heartbeatTimer;
    private readonly Stopwatch _heartbeatClock = Stopwatch.StartNew();
    private TimeSpan _nextHeartbeat;
    private TimeSpan? _firstRenderingTime;
    private TimeSpan? _lastRenderingTime;
    private TimeSpan _lastHeartbeatDelay;
    private long _lastSampleTimestamp;
    private bool _installed;

    public PerformanceDiagnosticCollector(Dispatcher dispatcher, PerformanceOptions options)
    {
        _dispatcher = dispatcher;
        _options = options;
        _heartbeatTimer = new DispatcherTimer(DispatcherPriority.Background, dispatcher)
        {
            Interval = options.HeartbeatInterval,
        };
        _heartbeatTimer.Tick += OnHeartbeat;
    }

    public int RegistrationCount { get; private set; }

    public TimeSpan LastHeartbeatDelay
    {
        get
        {
            lock (_metricGate)
            {
                return _lastHeartbeatDelay;
            }
        }
    }

    public TimeSpan SampleDuration
    {
        get
        {
            lock (_metricGate)
            {
                return _firstRenderingTime is not null && _lastRenderingTime is not null
                    ? _lastRenderingTime.Value - _firstRenderingTime.Value
                    : TimeSpan.Zero;
            }
        }
    }

    public Dispatcher Dispatcher => _dispatcher;

    public PerformanceOptions Options => _options;

    public void Install()
    {
        if (_installed)
        {
            return;
        }

        CompositionTarget.Rendering += OnRendering;
        _nextHeartbeat = _heartbeatClock.Elapsed + _options.HeartbeatInterval;
        _heartbeatTimer.Start();
        _installed = true;
        RegistrationCount++;
    }

    public void Uninstall()
    {
        if (!_installed)
        {
            return;
        }

        CompositionTarget.Rendering -= OnRendering;
        _heartbeatTimer.Stop();
        _installed = false;
    }

    public void RecordFrame(TimeSpan renderingTime)
    {
        lock (_metricGate)
        {
            if (_lastRenderingTime is null)
            {
                _firstRenderingTime = renderingTime;
                _lastRenderingTime = renderingTime;
                return;
            }

            var interval = renderingTime - _lastRenderingTime.Value;
            if (interval <= TimeSpan.Zero)
            {
                return;
            }

            _frameIntervals.Add(interval.TotalMilliseconds);
            _lastRenderingTime = renderingTime;
        }
    }

    public void RecordHeartbeat(TimeSpan delay)
    {
        lock (_metricGate)
        {
            _lastHeartbeatDelay = delay < TimeSpan.Zero ? TimeSpan.Zero : delay;
        }
    }

    public FrameStatistics GetFrameStatistics() =>
        PerformanceStatisticsCalculator.Calculate(_frameIntervals.Snapshot());

    public bool TryBeginSample()
    {
        lock (_metricGate)
        {
            var now = Stopwatch.GetTimestamp();
            if (_lastSampleTimestamp != 0
                && Stopwatch.GetElapsedTime(_lastSampleTimestamp, now) < _options.SampleCooldown)
            {
                return false;
            }

            _lastSampleTimestamp = now;
            return true;
        }
    }

    public void Dispose() => Uninstall();

    private void OnRendering(object? sender, EventArgs args)
    {
        if (args is RenderingEventArgs rendering)
        {
            RecordFrame(rendering.RenderingTime);
        }
    }

    private void OnHeartbeat(object? sender, EventArgs args)
    {
        var now = _heartbeatClock.Elapsed;
        RecordHeartbeat(now - _nextHeartbeat);
        _nextHeartbeat = now + _options.HeartbeatInterval;
    }
}

internal static class PerformanceVisualCounter
{
    public static (int Count, bool Truncated) Count(DependencyObject? root, int maxNodes)
    {
        if (root is null || maxNodes < 1)
        {
            return (0, false);
        }

        var pending = new Stack<DependencyObject>();
        pending.Push(root);
        var count = 0;
        while (pending.Count > 0 && count < maxNodes)
        {
            var current = pending.Pop();
            count++;
            for (var index = VisualTreeHelper.GetChildrenCount(current) - 1; index >= 0; index--)
            {
                pending.Push(VisualTreeHelper.GetChild(current, index));
            }
        }

        return (count, pending.Count > 0);
    }
}

public sealed partial class ReliabilitySensor
{
    private PerformanceDiagnosticCollector? _performanceCollector;

    public bool InstallPerformanceDiagnostics(Dispatcher dispatcher, PerformanceOptions? options = null)
    {
        options ??= new PerformanceOptions();
        if (!IsEnabled || Volatile.Read(ref _disposed) != 0 || !IsValid(options))
        {
            return false;
        }

        lock (_collectorLifecycleLock)
        {
            _performanceCollector ??= new PerformanceDiagnosticCollector(dispatcher, options);
            _performanceCollector.Install();
            return true;
        }
    }

    public void StopPerformanceDiagnostics()
    {
        lock (_collectorLifecycleLock)
        {
            _performanceCollector?.Uninstall();
        }
    }

    public async Task<PerformanceSampleResult> CapturePerformanceSampleAsync(
        DependencyObject? root = null,
        CancellationToken cancellationToken = default)
    {
        PerformanceDiagnosticCollector? collector;
        lock (_collectorLifecycleLock)
        {
            collector = _performanceCollector;
        }

        if (collector is null)
        {
            return Error("PERFORMANCE_NOT_INSTALLED", "Performance diagnostics are not installed.");
        }

        if (!collector.TryBeginSample())
        {
            return Error("PERFORMANCE_SAMPLE_COOLDOWN", "Performance sampling is cooling down.");
        }

        PerformanceSampleResult Capture()
        {
            var resolvedRoot = root ?? Application.Current?.MainWindow;
            if (resolvedRoot is null)
            {
                return Error("UI_ROOT_NOT_FOUND", "No WPF root element is available.");
            }

            var visual = PerformanceVisualCounter.Count(resolvedRoot, collector.Options.MaxVisualNodes);
            var frames = collector.GetFrameStatistics();
            var confidence = frames.SampleCount >= 90
                ? Confidence.HIGH
                : frames.SampleCount >= 30 ? Confidence.MEDIUM : Confidence.LOW;
            return new PerformanceSampleResult(
                true,
                frames,
                collector.SampleDuration.TotalMilliseconds,
                confidence,
                collector.LastHeartbeatDelay.TotalMilliseconds,
                visual.Count,
                visual.Truncated,
                null);
        }

        var result = collector.Dispatcher.CheckAccess()
            ? Capture()
            : await collector.Dispatcher.InvokeAsync(Capture).Task.WaitAsync(cancellationToken).ConfigureAwait(false);
        if (result.Succeeded)
        {
            TryEnqueue(
                EventType.PerformanceSample,
                Severity.INFO,
                JsonSerializer.SerializeToElement(new { app_session_id = AppSessionId }),
                JsonSerializer.SerializeToElement(new
                {
                    frame_statistics = result.FrameStatistics,
                    sample_duration_ms = result.SampleDurationMilliseconds,
                    confidence = result.Confidence,
                    heartbeat_delay_ms = result.HeartbeatDelayMilliseconds,
                    visual_count = result.VisualCount,
                    visual_count_truncated = result.VisualCountTruncated,
                }),
                out _);
        }

        return result;
    }

    internal Task<PerformanceSampleResult> CapturePerformanceSampleByIdAsync(
        string? elementId,
        CancellationToken cancellationToken = default)
    {
        if (elementId is null)
        {
            return CapturePerformanceSampleAsync(null, cancellationToken);
        }
        if (string.IsNullOrWhiteSpace(elementId)
            || !_elementIds.TryResolve<DependencyObject>(elementId, out var root))
        {
            return Task.FromResult(Error(
                "ELEMENT_NOT_FOUND",
                "The element does not exist in the current application session."));
        }
        return CapturePerformanceSampleAsync(root, cancellationToken);
    }

    private static bool IsValid(PerformanceOptions options) =>
        options.HeartbeatInterval >= TimeSpan.FromMilliseconds(100)
        && options.HeartbeatInterval <= TimeSpan.FromSeconds(10)
        && options.SampleCooldown >= TimeSpan.FromMilliseconds(100)
        && options.SampleCooldown <= TimeSpan.FromMinutes(1)
        && options.MaxVisualNodes is >= 1 and <= 500;

    private static PerformanceSampleResult Error(string code, string message) =>
        new(
            false,
            PerformanceStatisticsCalculator.Calculate([]),
            0,
            Confidence.LOW,
            0,
            0,
            false,
            new PerformanceDiagnosticError(code, message));
}
