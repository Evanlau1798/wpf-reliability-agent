using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class ExceptionDiagnosticsTests
{
    [Fact]
    public void DiagnosticRedactsUserPathsAndSecretLikeTokens()
    {
        var exception = CaptureThrownException(
            "Failed at C:\\Users\\Alice\\private\\data.txt with api_key=abc123 and Bearer ey.secret");

        var diagnostic = ExceptionDiagnosticFactory.Create(exception, isTerminating: false, isUnhandled: true);
        var serialized = JsonSerializer.Serialize(diagnostic);

        Assert.DoesNotContain("Alice", serialized, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("abc123", serialized, StringComparison.Ordinal);
        Assert.DoesNotContain("ey.secret", serialized, StringComparison.Ordinal);
        Assert.Contains("[REDACTED]", serialized, StringComparison.Ordinal);
    }

    [Fact]
    public void DiagnosticKeepsOnlyMetadataAndAtLeastOneApplicationFrame()
    {
        var diagnostic = ExceptionDiagnosticFactory.Create(
            CaptureThrownException("Stable failure 4815162342"),
            isTerminating: true,
            isUnhandled: true);

        Assert.Equal(typeof(InvalidOperationException).FullName, diagnostic.ExceptionType);
        Assert.Contains(diagnostic.AppFrames, frame => frame.Contains(nameof(CaptureThrownException), StringComparison.Ordinal));
        Assert.DoesNotContain(diagnostic.AppFrames, frame => frame.StartsWith("System.", StringComparison.Ordinal));
        Assert.True(diagnostic.IsTerminating);
    }

    [Fact]
    public void ExceptionFingerprintForKnownFixtureIsStable()
    {
        var diagnostic = new ExceptionDiagnostic(
            "Demo.SampleException",
            "Stable failure #",
            ["Demo.App.Run"],
            IsTerminating: false,
            IsUnhandled: true);

        Assert.Equal(
            "a89b5633da36dab1d191b7cdc35b3eefb71f3bf8697fc6f98f1dc66f66e15aaa",
            ExceptionDiagnosticFactory.Fingerprint(diagnostic));
    }

    [Fact]
    public async Task UnobservedTaskHandlerDoesNotChangeObservedStateAndQueuesSummary()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var collector = new ExceptionDiagnosticCollector(sensor);
        var args = new UnobservedTaskExceptionEventArgs(
            new AggregateException(new InvalidOperationException("background failed")));

        collector.OnUnobservedTaskException(null, args);

        Assert.False(args.Observed);
        var envelope = Assert.Single(ReadAll(sensor));
        Assert.Equal(EventType.ExceptionSummary, envelope.EventType);
        Assert.True(ContractValidator.Validate(envelope));
    }

    [Fact]
    public async Task AppDomainHandlerPreservesTerminatingFlag()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var collector = new ExceptionDiagnosticCollector(sensor);

        collector.OnUnhandledException(
            null,
            new UnhandledExceptionEventArgs(new InvalidOperationException("fatal"), isTerminating: true));

        var envelope = Assert.Single(ReadAll(sensor));
        Assert.True(envelope.Payload.GetProperty("is_terminating").GetBoolean());
    }

    private static Exception CaptureThrownException(string message)
    {
        try
        {
            throw new InvalidOperationException(message);
        }
        catch (Exception exception)
        {
            return exception;
        }
    }

    private static List<DiagnosticEnvelope> ReadAll(ReliabilitySensor sensor)
    {
        var events = new List<DiagnosticEnvelope>();
        while (sensor.Events.TryRead(out var envelope))
        {
            events.Add(envelope);
        }

        return events;
    }

    private static ReliabilitySensorOptions Options() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
    };
}
