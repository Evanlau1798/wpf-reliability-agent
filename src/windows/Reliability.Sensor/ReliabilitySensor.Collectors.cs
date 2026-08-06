using System.Windows;

namespace Reliability.Sensor;

public sealed partial class ReliabilitySensor
{
    private readonly object _collectorLifecycleLock = new();
    private ExceptionDiagnosticCollector? _exceptionCollector;

    public void InstallExceptionDiagnostics(Application application)
    {
        if (!IsEnabled || Volatile.Read(ref _disposed) != 0)
        {
            return;
        }

        lock (_collectorLifecycleLock)
        {
            _exceptionCollector ??= new ExceptionDiagnosticCollector(this);
            _exceptionCollector.Install(application);
        }
    }

    public void StopExceptionDiagnostics()
    {
        lock (_collectorLifecycleLock)
        {
            _exceptionCollector?.Uninstall();
        }
    }

    private void StopCollectors()
    {
        StopExceptionDiagnostics();
        StopPerformanceDiagnostics();
    }
}
