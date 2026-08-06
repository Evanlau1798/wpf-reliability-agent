using System.Runtime.ExceptionServices;
using System.Windows;
using System.Windows.Threading;
using Reliability.Sensor;

namespace Demo.BrokenWpfApp.Tests;

public sealed class BindingSignalIntegrationTests
{
    [Fact]
    public void BrokenGridAutomaticallyProducesALocalBindingAggregate()
    {
        Exception? failure = null;
        var completed = new ManualResetEventSlim();
        var thread = new Thread(() =>
        {
            try
            {
                RunBrokenGrid();
            }
            catch (Exception exception)
            {
                failure = exception;
            }
            finally
            {
                completed.Set();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();

        Assert.True(completed.Wait(TimeSpan.FromSeconds(20)), "The WPF binding signal smoke test timed out.");
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }
    }

    private static void RunBrokenGrid()
    {
        var application = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
        {
            ApiBaseUri = new Uri("https://localhost"),
            DeviceId = "demo-test-device",
            DeviceToken = string.Empty,
            ApplicationId = "demo-broken-wpf-app",
            ApplicationVersion = "0.1.0",
            BindingBurstThreshold = 1,
        });
        sensor.InstallBindingDiagnostics();
        var window = new MainWindow(sensor);

        try
        {
            window.Show();
            window.UpdateLayout();
            window.Dispatcher.Invoke(static () => { }, DispatcherPriority.ApplicationIdle);

            Assert.True(sensor.BindingAggregateCount > 0);
            Assert.False(sensor.CanUpload);
        }
        finally
        {
            window.Close();
            sensor.StopBindingDiagnostics();
            sensor.DisposeAsync().AsTask().GetAwaiter().GetResult();
            application.Shutdown();
        }
    }

}
