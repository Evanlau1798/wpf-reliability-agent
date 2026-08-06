using System.Windows;

namespace Demo.BrokenWpfApp;

using System.Diagnostics;
using Reliability.Sensor;

/// <summary>
/// Interaction logic for App.xaml
/// </summary>
public partial class App : Application
{
    private readonly CancellationTokenSource _applicationStopping = new();
    private ReliabilitySensor? _sensor;

    protected override void OnStartup(StartupEventArgs e)
    {
        _sensor = ReliabilitySensor.Start(
            CreateSensorOptions(),
            _applicationStopping.Token,
            static diagnostic => Debug.WriteLine($"Reliability sensor: {diagnostic}"));
        _sensor.InstallBindingDiagnostics();
        base.OnStartup(e);
        MainWindow = new MainWindow(_sensor);
        MainWindow.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _sensor?.StopBindingDiagnostics();
        _applicationStopping.Cancel();
        try
        {
            _sensor?.DisposeAsync().AsTask().GetAwaiter().GetResult();
        }
        catch (Exception)
        {
            Debug.WriteLine("Reliability sensor shutdown failed.");
        }
        finally
        {
            _applicationStopping.Dispose();
            base.OnExit(e);
        }
    }

    private static ReliabilitySensorOptions CreateSensorOptions()
    {
        var configuredEndpoint = Environment.GetEnvironmentVariable("WPF_RELIABILITY_API_BASE_URI");
        var endpoint = Uri.TryCreate(configuredEndpoint ?? "https://localhost", UriKind.Absolute, out var parsed)
            ? parsed
            : new Uri("invalid", UriKind.Relative);

        return new ReliabilitySensorOptions
        {
            ApiBaseUri = endpoint,
            DeviceId = Environment.GetEnvironmentVariable("WPF_RELIABILITY_DEVICE_ID") ?? "demo-device",
            DeviceToken = Environment.GetEnvironmentVariable("WPF_RELIABILITY_DEVICE_TOKEN") ?? string.Empty,
            ApplicationId = "demo-broken-wpf-app",
            ApplicationVersion = "0.1.0",
        };
    }
}
