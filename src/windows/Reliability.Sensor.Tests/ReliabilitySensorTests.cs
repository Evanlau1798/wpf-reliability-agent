using System.Runtime.CompilerServices;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class ReliabilitySensorTests
{
    [Fact]
    public async Task StartReturnsWithoutWaitingForApplicationShutdown()
    {
        using var applicationStopping = new CancellationTokenSource();

        var start = Task.Run(() => ReliabilitySensor.Start(ValidOptions(), applicationStopping.Token));
        var sensor = await start.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.True(sensor.IsEnabled);
        Assert.False(sensor.Completion.IsCompleted);
        await sensor.DisposeAsync();
        Assert.True(sensor.Completion.IsCompletedSuccessfully);
    }

    [Fact]
    public async Task ApplicationShutdownCancelsTheLinkedSensorLifetime()
    {
        using var applicationStopping = new CancellationTokenSource();
        await using var sensor = ReliabilitySensor.Start(ValidOptions(), applicationStopping.Token);

        applicationStopping.Cancel();

        await sensor.Completion.WaitAsync(TimeSpan.FromSeconds(1));
        Assert.True(sensor.Completion.IsCompletedSuccessfully);
    }

    [Fact]
    public async Task InvalidEndpointFailsWithoutEscapingStartup()
    {
        var diagnostics = new List<SensorDiagnostic>();
        var options = ValidOptions() with
        {
            ApiBaseUri = new Uri("relative", UriKind.Relative),
            DeviceToken = "must-not-appear-in-diagnostics",
        };

        await using var sensor = ReliabilitySensor.Start(options, diagnosticLogger: diagnostics.Add);

        Assert.False(sensor.IsEnabled);
        Assert.Equal([SensorDiagnostic.InitializationFailed], diagnostics);
    }

    [Fact]
    public async Task MissingTokenFailsClosedBeforeEventsCanBeQueued()
    {
        var options = ValidOptions() with { DeviceToken = " " };
        await using var sensor = ReliabilitySensor.Start(options);

        var queued = sensor.TryEnqueue(
            EventType.BindingAggregate,
            Severity.ERROR,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { count = 1 }),
            out _);

        Assert.False(sensor.IsEnabled);
        Assert.False(queued);
        Assert.False(sensor.Events.TryRead(out _));
    }

    [Fact]
    public async Task ACompleteLifecycleCanBeStartedAgain()
    {
        var first = ReliabilitySensor.Start(ValidOptions());
        await first.DisposeAsync();
        var second = ReliabilitySensor.Start(ValidOptions());

        Assert.NotEqual(first.AppSessionId, second.AppSessionId);

        await second.DisposeAsync();
    }

    [Fact]
    public async Task SessionAndSequenceIdentityAreScopedToOneStart()
    {
        await using var sensor = ReliabilitySensor.Start(ValidOptions());

        Assert.True(sensor.TryEnqueue(
            EventType.BindingAggregate,
            Severity.ERROR,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { count = 1 }),
            out var first));
        Assert.True(sensor.Events.TryRead(out _));
        Assert.True(sensor.TryEnqueue(
            EventType.PerformanceSample,
            Severity.WARNING,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { p95_ms = 30 }),
            out var second));

        Assert.Equal(first!.SequenceNo + 1, second!.SequenceNo);
        Assert.Equal(sensor.AppSessionId, first.AppSessionId);
        Assert.True(sensor.IsCurrentSession(sensor.AppSessionId));
        Assert.False(sensor.IsCurrentSession($"stale-{sensor.AppSessionId}"));
    }

    [Fact]
    public async Task ElementIdentityIsStableWithoutKeepingTheElementAlive()
    {
        await using var sensor = ReliabilitySensor.Start(ValidOptions());
        var element = new object();

        Assert.Equal(sensor.GetElementId(element), sensor.GetElementId(element));

        var weakElement = CreateRegisteredElement(sensor);
        for (var attempt = 0; attempt < 5 && weakElement.IsAlive; attempt++)
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
        }

        Assert.False(weakElement.IsAlive);
    }

    [Fact]
    public async Task EnvelopeIsSchemaValidUtcAndHasPayloadEvidenceHash()
    {
        await using var sensor = ReliabilitySensor.Start(ValidOptions());
        var payload = JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae", count = 5 });

        Assert.True(sensor.TryEnqueue(
            EventType.BindingAggregate,
            Severity.ERROR,
            JsonSerializer.SerializeToElement(new { window_type = "MainWindow" }),
            payload,
            out var envelope));

        var json = JsonSerializer.Serialize(envelope!, ContractJsonContext.Default.DiagnosticEnvelope);
        using var document = JsonDocument.Parse(json);
        Assert.True(ContractValidator.Validate(envelope!, json));
        Assert.Equal(TimeSpan.Zero, envelope!.TimestampUtc.Offset);
        Assert.EndsWith("Z", document.RootElement.GetProperty("timestamp_utc").GetString(), StringComparison.Ordinal);
        Assert.Equal(CanonicalJson.Hash(payload), envelope.EvidenceHash);
    }

    [Fact]
    public async Task FullChannelRejectsNewestEventAndCountsTheDrop()
    {
        await using var sensor = ReliabilitySensor.Start(ValidOptions() with { EventChannelCapacity = 1 });

        Assert.True(TryQueueSmallEvent(sensor, 1));
        Assert.False(TryQueueSmallEvent(sensor, 2));
        Assert.Equal(1, sensor.DroppedEventCount);
    }

    [Fact]
    public async Task OversizedEnvelopeIsRejectedBeforeEnqueue()
    {
        await using var sensor = ReliabilitySensor.Start(ValidOptions() with { MaxEventBytes = 1_024 });

        var queued = sensor.TryEnqueue(
            EventType.BindingAggregate,
            Severity.ERROR,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { value = new string('x', 4_096) }),
            out var envelope);

        Assert.False(queued);
        Assert.Null(envelope);
        Assert.Equal(1, sensor.DroppedEventCount);
        Assert.False(sensor.Events.TryRead(out _));
    }

    private static bool TryQueueSmallEvent(ReliabilitySensor sensor, int count) =>
        sensor.TryEnqueue(
            EventType.BindingAggregate,
            Severity.ERROR,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { count }),
            out _);

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static WeakReference CreateRegisteredElement(ReliabilitySensor sensor)
    {
        var element = new object();
        _ = sensor.GetElementId(element);
        return new WeakReference(element);
    }

    private static ReliabilitySensorOptions ValidOptions() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
    };
}
