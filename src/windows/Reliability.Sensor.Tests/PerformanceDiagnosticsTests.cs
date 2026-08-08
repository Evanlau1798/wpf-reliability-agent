using System.Runtime.ExceptionServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class PerformanceDiagnosticsTests
{
    [Fact]
    public void CircularBufferRemainsBoundedAcrossManyWraps()
    {
        var buffer = new FixedCircularBuffer<int>(120);

        for (var value = 1; value <= 100_000; value++)
        {
            buffer.Add(value);
        }

        Assert.Equal(120, buffer.Count);
        Assert.Equal(99_881, buffer.Snapshot()[0]);
        Assert.Equal(100_000, buffer.Snapshot()[^1]);
    }

    [Fact]
    public void StatisticsHandleEmptySingleAndKnownSamples()
    {
        var empty = PerformanceStatisticsCalculator.Calculate([]);
        var single = PerformanceStatisticsCalculator.Calculate([25]);
        var known = PerformanceStatisticsCalculator.Calculate([10, 20, 30, 40, 60]);

        Assert.Equal(0, empty.SampleCount);
        Assert.Equal(25, single.AverageMilliseconds);
        Assert.Equal(32, known.AverageMilliseconds);
        Assert.Equal(30, known.P50Milliseconds);
        Assert.Equal(60, known.P95Milliseconds);
        Assert.Equal(60, known.MaxMilliseconds);
        Assert.Equal(4, known.Over16Point7Milliseconds);
        Assert.Equal(2, known.Over33Point3Milliseconds);
        Assert.Equal(1, known.Over50Milliseconds);
    }

    [Fact]
    public void CollectorIgnoresFirstAndNonPositiveFrameIntervals()
    {
        using var collector = new PerformanceDiagnosticCollector(
            Dispatcher.CurrentDispatcher,
            new PerformanceOptions());

        collector.RecordFrame(TimeSpan.FromMilliseconds(100));
        collector.RecordFrame(TimeSpan.FromMilliseconds(90));
        collector.RecordFrame(TimeSpan.FromMilliseconds(110));

        var statistics = collector.GetFrameStatistics();
        Assert.Equal(1, statistics.SampleCount);
        Assert.Equal(10, statistics.AverageMilliseconds);
        Assert.Equal(10, collector.SampleDuration.TotalMilliseconds);
    }

    [Fact]
    public void PerformanceWindowUsesOnlyFramesRecordedAfterBaseline()
    {
        using var collector = new PerformanceDiagnosticCollector(
            Dispatcher.CurrentDispatcher,
            new PerformanceOptions());
        collector.RecordFrame(TimeSpan.Zero);
        collector.RecordFrame(TimeSpan.FromMilliseconds(10));
        var baseline = collector.FrameSampleCount;
        var renderingTime = TimeSpan.FromMilliseconds(10);
        for (var index = 0; index < 35; index++)
        {
            renderingTime += TimeSpan.FromMilliseconds(20);
            collector.RecordFrame(renderingTime);
        }

        var window = collector.CaptureFrameWindowSince(baseline);

        Assert.Equal(35, window.FrameStatistics.SampleCount);
        Assert.Equal(20, window.FrameStatistics.P95Milliseconds);
        Assert.Equal(700, window.SampleDurationMilliseconds);
        Assert.Equal(Confidence.MEDIUM, window.Confidence);
    }

    [Fact]
    public void InstallIsIdempotentAndHeartbeatDelayIsReported()
    {
        RunSta(() =>
        {
            using var collector = new PerformanceDiagnosticCollector(
                Dispatcher.CurrentDispatcher,
                new PerformanceOptions());
            collector.Install();
            collector.Install();
            collector.RecordHeartbeat(TimeSpan.FromMilliseconds(42));

            Assert.Equal(1, collector.RegistrationCount);
            Assert.Equal(42, collector.LastHeartbeatDelay.TotalMilliseconds);
        });
    }

    [Fact]
    public void VisualCountStopsAtItsHardCeiling()
    {
        var result = RunSta(() =>
        {
            var root = new StackPanel();
            for (var index = 0; index < 20; index++)
            {
                root.Children.Add(new TextBlock());
            }

            return PerformanceVisualCounter.Count(root, maxNodes: 10);
        });

        Assert.Equal(10, result.Count);
        Assert.True(result.Truncated);
    }

    [Fact]
    public async Task InvalidPerformanceOptionsAreRejected()
    {
        var result = RunSta(() =>
        {
            var sensor = ReliabilitySensor.Start(Options());
            var accepted = sensor.InstallPerformanceDiagnostics(
                Dispatcher.CurrentDispatcher,
                new PerformanceOptions { MaxVisualNodes = 501 });
            return (accepted, sensor);
        });

        Assert.False(result.accepted);
        await result.sensor.DisposeAsync();
    }

    [Fact]
    public async Task PassiveSampleIsBoundedSchemaValidAndCooldownProtected()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var ready = new TaskCompletionSource<FrameworkElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel();
            root.Children.Add(new TextBlock());
            sensor.InstallPerformanceDiagnostics(
                Dispatcher.CurrentDispatcher,
                new PerformanceOptions { SampleCooldown = TimeSpan.FromSeconds(1) });
            ready.SetResult(root);
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var root = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var first = await sensor.CapturePerformanceSampleAsync(root);
            var second = await sensor.CapturePerformanceSampleAsync(root);

            Assert.True(first.Succeeded);
            Assert.Equal(2, first.VisualCount);
            Assert.Equal(Confidence.LOW, first.Confidence);
            Assert.Equal("PERFORMANCE_SAMPLE_COOLDOWN", second.Error?.Code);
            Assert.True(sensor.Events.TryRead(out var envelope));
            Assert.Equal(EventType.PerformanceSample, envelope.EventType);
            Assert.Equal(sensor.GetElementId(root), envelope.Payload.GetProperty("visual_scope_id").GetString());
            Assert.True(ContractValidator.Validate(envelope));
            Assert.InRange(
                JsonSerializer.SerializeToUtf8Bytes(envelope, ContractJsonContext.Default.DiagnosticEnvelope).Length,
                1,
                65_536);
        }
        finally
        {
            sensor.StopPerformanceDiagnostics();
            root.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    private static void RunSta(Action action)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                action();
            }
            catch (Exception exception)
            {
                failure = exception;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }
    }

    private static T RunSta<T>(Func<T> action)
    {
        T? result = default;
        RunSta((Action)(() =>
        {
            result = action();
        }));
        return result!;
    }

    private static ReliabilitySensorOptions Options() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
        DisableBackgroundPersistence = true,
    };
}
