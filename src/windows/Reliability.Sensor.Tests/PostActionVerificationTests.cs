namespace Reliability.Sensor.Tests;

public sealed class PostActionVerificationTests
{
    [Fact]
    public async Task PostBindingWindowCountsOnlyFailuresObservedDuringWindow()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        Assert.True(sensor.ReportBindingFailure("BeforeWindow", "Text", "TextBlock"));

        var capture = sensor.CapturePostBindingWindowAsync(
            CancellationToken.None,
            TimeSpan.FromMilliseconds(100));
        await Task.Delay(20);
        Assert.True(sensor.ReportBindingFailure("DuringWindow", "Text", "TextBlock"));

        var snapshot = await capture;

        Assert.Equal(1, snapshot.OccurrenceCount);
        Assert.Equal(10, snapshot.ErrorsPerSecond, precision: 6);
        Assert.True(snapshot.CompletedAtUtc > snapshot.StartedAtUtc);
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
