using System.Windows.Controls;
using System.Windows.Threading;

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
        Assert.True(sensor.ReportBindingFailure("DuringWindow", "Text", "TextBlock"));

        var snapshot = await capture;

        Assert.Equal(1, snapshot.OccurrenceCount);
        Assert.Equal(10, snapshot.ErrorsPerSecond, precision: 6);
        Assert.True(snapshot.CompletedAtUtc > snapshot.StartedAtUtc);
    }

    [Fact]
    public async Task PostPerformanceWindowCollectsBoundedVisualCount()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var ready = new TaskCompletionSource<(Dispatcher Dispatcher, StackPanel Root)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel();
            for (var index = 0; index < 20; index++)
            {
                root.Children.Add(new TextBlock());
            }
            sensor.InstallPerformanceDiagnostics(
                Dispatcher.CurrentDispatcher,
                new PerformanceOptions { MaxVisualNodes = 10 });
            ready.TrySetResult((Dispatcher.CurrentDispatcher, root));
            Dispatcher.Run();
        }) { IsBackground = true };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var (dispatcher, root) = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var snapshot = await sensor.CapturePostPerformanceWindowAsync(
                CancellationToken.None,
                TimeSpan.FromMilliseconds(10),
                root);

            Assert.Equal(10, snapshot.VisualCount);
            Assert.True(snapshot.VisualCountTruncated);
        }
        finally
        {
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(1)));
        }
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
