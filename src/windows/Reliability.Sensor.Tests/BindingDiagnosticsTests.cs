using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class BindingDiagnosticsTests
{
    private const string PropertyNotFoundMessage =
        "System.Windows.Data Error: 40 : BindingExpression path error: 'DisplayNmae' property not found on 'object' ''PersonViewModel'. " +
        "BindingExpression:Path=DisplayNmae; target element is 'TextBlock' (Name='PersonName'); target property is 'Text' (type 'String')";

    [Fact]
    public void ListenerInstallIsIdempotentAndUninstallRestoresTheTraceLevel()
    {
        var source = PresentationTraceSources.DataBindingSource;
        var originalLevel = source.Switch.Level;
        using var listener = new BindingTraceListener(_ => { });

        try
        {
            listener.Install();
            listener.Install();

            Assert.Equal(SourceLevels.All, source.Switch.Level);
            Assert.Single(source.Listeners.Cast<TraceListener>(), current => ReferenceEquals(current, listener));

            listener.Uninstall();
            Assert.DoesNotContain(source.Listeners.Cast<TraceListener>(), current => ReferenceEquals(current, listener));
            Assert.Equal(originalLevel, source.Switch.Level);
        }
        finally
        {
            listener.Uninstall();
            source.Switch.Level = originalLevel;
        }
    }

    [Fact]
    public void ListenerIgnoresEmptyMessagesAndTruncatesUtf8AtFourKibibytes()
    {
        var captured = new List<BindingTraceMessage>();
        using var listener = new BindingTraceListener(captured.Add);

        listener.TraceEvent(null, "System.Windows.Data", TraceEventType.Error, 40, (string?)null);
        listener.TraceEvent(null, "System.Windows.Data", TraceEventType.Error, 40, string.Empty);
        listener.TraceEvent(null, "System.Windows.Data", TraceEventType.Error, 40, new string('\u754c', 2_000));

        var message = Assert.Single(captured);
        Assert.True(message.WasTruncated);
        Assert.InRange(Encoding.UTF8.GetByteCount(message.Message), 1, 4_096);
    }

    [Fact]
    public void DuplicateInstallDeliversOneTraceEvent()
    {
        var captured = new List<BindingTraceMessage>();
        var source = PresentationTraceSources.DataBindingSource;
        var originalLevel = source.Switch.Level;
        using var listener = new BindingTraceListener(captured.Add);

        try
        {
            listener.Install();
            listener.Install();
            source.TraceEvent(TraceEventType.Error, 40, PropertyNotFoundMessage);
            source.Flush();

            Assert.Single(captured);
        }
        finally
        {
            listener.Uninstall();
            source.Switch.Level = originalLevel;
        }
    }

    [Fact]
    public void ParserExtractsPropertyNotFoundFields()
    {
        var diagnostic = BindingDiagnosticParser.Parse(new BindingTraceMessage(
            DateTimeOffset.UtcNow,
            PropertyNotFoundMessage,
            WasTruncated: false));

        Assert.NotNull(diagnostic);
        Assert.Equal("PROPERTY_NOT_FOUND", diagnostic.Category);
        Assert.Equal("DisplayNmae", diagnostic.BindingPath);
        Assert.Equal("Text", diagnostic.TargetProperty);
        Assert.Equal("TextBlock", diagnostic.ElementType);
        Assert.Equal("PersonName", diagnostic.ElementName);
    }

    [Fact]
    public void ParserNormalizesAlternatePropertyNotFoundMessage()
    {
        var diagnostic = BindingDiagnosticParser.Parse(new BindingTraceMessage(
            DateTimeOffset.UtcNow,
            "System.Windows.Data Error: BindingExpression path error: property not found. " +
            "BindingExpression:Path=DisplayNmae; target property is 'Text'",
            WasTruncated: false));

        Assert.NotNull(diagnostic);
        Assert.Equal("PROPERTY_NOT_FOUND", diagnostic.Category);
        Assert.Equal("DisplayNmae", diagnostic.BindingPath);
    }

    [Fact]
    public void MalformedBindingMessageProducesAPartialDiagnostic()
    {
        var diagnostic = BindingDiagnosticParser.Parse(new BindingTraceMessage(
            DateTimeOffset.UtcNow,
            "System.Windows.Data Error: malformed binding trace",
            WasTruncated: false));

        Assert.NotNull(diagnostic);
        Assert.Equal("BINDING_ERROR", diagnostic.Category);
        Assert.Null(diagnostic.BindingPath);
        Assert.Null(diagnostic.TargetProperty);
        Assert.Null(diagnostic.ElementType);
        Assert.Null(diagnostic.ElementName);
    }

    [Fact]
    public void ValidationFailureIsExcluded()
    {
        var diagnostic = BindingDiagnosticParser.Parse(new BindingTraceMessage(
            DateTimeOffset.UtcNow,
            "System.Windows.Data Error: validation failed: Name is required",
            WasTruncated: false));

        Assert.Null(diagnostic);
    }

    [Fact]
    public void FingerprintTrimsButPreservesCaseSensitiveBindingPaths()
    {
        var first = Diagnostic(" DisplayName ");
        var trimmed = Diagnostic("DisplayName");
        var differentCase = Diagnostic("displayname");

        Assert.Equal(
            BindingDiagnosticAggregator.Fingerprint("0.1.0", first),
            BindingDiagnosticAggregator.Fingerprint("0.1.0", trimmed));
        Assert.NotEqual(
            BindingDiagnosticAggregator.Fingerprint("0.1.0", first),
            BindingDiagnosticAggregator.Fingerprint("0.1.0", differentCase));
    }

    [Fact]
    public void FingerprintForKnownFixtureIsStable()
    {
        Assert.Equal(
            "966417f05e1ced2ef2088d9960fc04b4a43be11e0411e3a542757488778b1dcd",
            BindingDiagnosticAggregator.Fingerprint("0.1.0", Diagnostic("DisplayName")));
    }

    [Fact]
    public async Task OneThousandDuplicateErrorsProduceAtMostTwoBoundedEvents()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var aggregator = new BindingDiagnosticAggregator(sensor, TimeSpan.FromSeconds(10), burstThreshold: 10);
        var start = DateTimeOffset.UtcNow;

        for (var index = 0; index < 1_000; index++)
        {
            aggregator.Accept(new BindingTraceMessage(start.AddMilliseconds(index), PropertyNotFoundMessage, false));
        }

        aggregator.FlushExpired(start.AddSeconds(11));
        var events = ReadAll(sensor);

        Assert.InRange(events.Count, 1, 2);
        Assert.All(events, item => Assert.True(ContractValidator.Validate(item)));
        Assert.All(events, item => Assert.InRange(JsonSerializer.SerializeToUtf8Bytes(
            item,
            ContractJsonContext.Default.DiagnosticEnvelope).Length, 1, 65_536));
    }

    [Fact]
    public async Task LowFrequencyErrorFlushesAtWindowEnd()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var aggregator = new BindingDiagnosticAggregator(sensor, TimeSpan.FromSeconds(10), burstThreshold: 10);
        var start = DateTimeOffset.UtcNow;

        aggregator.Accept(new BindingTraceMessage(start, PropertyNotFoundMessage, false));
        Assert.Empty(ReadAll(sensor));

        aggregator.FlushExpired(start.AddSeconds(10));
        var envelope = Assert.Single(ReadAll(sensor));
        Assert.Equal(1, envelope.Payload.GetProperty("occurrence_count").GetInt32());
        Assert.Equal(start, envelope.Payload.GetProperty("first_seen_utc").GetDateTimeOffset());
        Assert.Equal(start, envelope.Payload.GetProperty("last_seen_utc").GetDateTimeOffset());
    }

    [Fact]
    public async Task DifferentPathsDoNotMergeAndWindowRolloverCreatesANewAggregate()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var aggregator = new BindingDiagnosticAggregator(sensor, TimeSpan.FromSeconds(10), burstThreshold: 10);
        var start = DateTimeOffset.UtcNow;

        aggregator.Accept(Trace(start, "FirstMissing"));
        aggregator.Accept(Trace(start, "SecondMissing"));
        aggregator.FlushExpired(start.AddSeconds(10));
        var firstWindow = ReadAll(sensor);
        aggregator.Accept(Trace(start.AddSeconds(11), "FirstMissing"));
        aggregator.FlushExpired(start.AddSeconds(21));
        var secondWindow = ReadAll(sensor);

        Assert.Equal(2, firstWindow.Select(item => item.Payload.GetProperty("fingerprint").GetString()).Distinct().Count());
        Assert.Single(secondWindow);
    }

    [Fact]
    public async Task FullSensorChannelCountsBindingAggregateDrop()
    {
        await using var sensor = ReliabilitySensor.Start(Options() with
        {
            EventChannelCapacity = 1,
            BindingBurstThreshold = 1,
        });
        var aggregator = new BindingDiagnosticAggregator(sensor, TimeSpan.FromSeconds(10), burstThreshold: 1);
        var start = DateTimeOffset.UtcNow;

        aggregator.Accept(Trace(start, "FirstMissing"));
        aggregator.Accept(Trace(start, "SecondMissing"));

        Assert.Equal(1, sensor.DroppedEventCount);
        Assert.Equal(1, sensor.BindingAggregateCount);
    }

    [Fact]
    public async Task InstalledSensorListenerQueuesBindingAggregateWithoutNetworkIo()
    {
        var diagnostics = new List<SensorDiagnostic>();
        await using var sensor = ReliabilitySensor.Start(
            Options() with { BindingBurstThreshold = 1 },
            diagnosticLogger: diagnostics.Add);

        sensor.InstallBindingDiagnostics();
        PresentationTraceSources.DataBindingSource.TraceEvent(
            TraceEventType.Error,
            40,
            PropertyNotFoundMessage);
        PresentationTraceSources.DataBindingSource.Flush();

        var envelope = Assert.Single(ReadAll(sensor));
        Assert.Equal(EventType.BindingAggregate, envelope.EventType);
        Assert.Contains(SensorDiagnostic.BindingAggregateQueued, diagnostics);
        sensor.StopBindingDiagnostics();
    }

    [Fact]
    public async Task InstalledSensorFlushesLowFrequencyTraceAfterItsWindow()
    {
        await using var sensor = ReliabilitySensor.Start(Options() with
        {
            BindingAggregationWindow = TimeSpan.FromMilliseconds(100),
        });
        sensor.InstallBindingDiagnostics();

        PresentationTraceSources.DataBindingSource.TraceEvent(
            TraceEventType.Error,
            40,
            PropertyNotFoundMessage);
        PresentationTraceSources.DataBindingSource.Flush();

        await AssertEventuallyAsync(() => sensor.BindingAggregateCount == 1);
        sensor.StopBindingDiagnostics();
    }

    [Fact]
    public async Task TypedBindingFailureRejectsInvalidMetadataAndQueuesValidMetadata()
    {
        await using var sensor = ReliabilitySensor.Start(Options() with { BindingBurstThreshold = 1 });

        Assert.False(sensor.ReportBindingFailure(" ", "Text", "TextBlock"));
        Assert.False(sensor.ReportBindingFailure(new string('x', 513), "Text", "TextBlock"));
        Assert.True(sensor.ReportBindingFailure("DisplayNmae", "Text", "TextBlock"));
        Assert.Single(ReadAll(sensor));
    }

    private static BindingDiagnostic Diagnostic(string path) => new(
        DateTimeOffset.UtcNow,
        "PROPERTY_NOT_FOUND",
        path,
        "Text",
        "TextBlock",
        "PersonName",
        WasTruncated: false);

    private static BindingTraceMessage Trace(DateTimeOffset timestamp, string path) => new(
        timestamp,
        PropertyNotFoundMessage.Replace("DisplayNmae", path, StringComparison.Ordinal),
        WasTruncated: false);

    private static List<DiagnosticEnvelope> ReadAll(ReliabilitySensor sensor)
    {
        var events = new List<DiagnosticEnvelope>();
        while (sensor.Events.TryRead(out var envelope))
        {
            events.Add(envelope);
        }

        return events;
    }

    private static async Task AssertEventuallyAsync(Func<bool> condition)
    {
        var timeout = DateTime.UtcNow.AddSeconds(2);
        while (!condition() && DateTime.UtcNow < timeout)
        {
            await Task.Delay(25);
        }

        Assert.True(condition());
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
