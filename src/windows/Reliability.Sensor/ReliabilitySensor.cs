using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading.Channels;
using Reliability.Contracts;

[assembly: InternalsVisibleTo("Reliability.Sensor.Tests")]

namespace Reliability.Sensor;

public sealed record ReliabilitySensorOptions
{
    public required Uri ApiBaseUri { get; init; }

    public required string DeviceId { get; init; }

    public required string DeviceToken { get; init; }

    public required string ApplicationId { get; init; }

    public required string ApplicationVersion { get; init; }

    public int EventChannelCapacity { get; init; } = 500;

    public int MaxEventBytes { get; init; } = 65_536;

    public TimeSpan ShutdownTimeout { get; init; } = TimeSpan.FromSeconds(5);

    public TimeSpan BindingAggregationWindow { get; init; } = TimeSpan.FromSeconds(10);

    public int BindingBurstThreshold { get; init; } = 10;

    public int MaxBindingFingerprints { get; init; } = 500;

    internal string? OutboxPath { get; init; }

    internal bool DisableBackgroundPersistence { get; init; }
}

public enum SensorDiagnostic
{
    Started,
    MissingDeviceToken,
    InitializationFailed,
    EventDropped,
    ShutdownTimedOut,
    OutboxPersistenceFailed,
    BindingAggregateQueued,
}

public sealed partial class ReliabilitySensor : IAsyncDisposable
{
    private const string RedactionProfile = "metadata-only-v1";
    private readonly CancellationTokenSource _lifetime;
    private readonly Channel<DiagnosticEnvelope> _events;
    private readonly Action<SensorDiagnostic>? _diagnosticLogger;
    private readonly ElementIdentityRegistry _elementIds;
    private readonly TimeSpan _shutdownTimeout;
    private readonly string _deviceId;
    private readonly string _applicationId;
    private readonly string _applicationVersion;
    private readonly int _maxEventBytes;
    private readonly object _bindingLifecycleLock = new();
    private readonly BindingDiagnosticAggregator? _bindingAggregator;
    private readonly TimeSpan _bindingMaintenanceInterval;
    private BindingTraceListener? _bindingTraceListener;
    private Task _bindingMaintenanceTask = Task.CompletedTask;
    private long _bindingAggregateCount;
    private long _droppedEventCount;
    private long _sequence;
    private int _disposed;

    private ReliabilitySensor(
        ReliabilitySensorOptions? options,
        string appSessionId,
        bool isEnabled,
        bool canUpload,
        CancellationTokenSource lifetime,
        Channel<DiagnosticEnvelope> events,
        Task completion,
        Action<SensorDiagnostic>? diagnosticLogger)
    {
        AppSessionId = appSessionId;
        IsEnabled = isEnabled;
        CanUpload = canUpload;
        _lifetime = lifetime;
        _events = events;
        Completion = completion;
        _diagnosticLogger = diagnosticLogger;
        _elementIds = new ElementIdentityRegistry(appSessionId);
        _shutdownTimeout = options?.ShutdownTimeout ?? TimeSpan.Zero;
        _maxEventBytes = options?.MaxEventBytes ?? 0;
        _deviceId = options?.DeviceId ?? string.Empty;
        _applicationId = options?.ApplicationId ?? string.Empty;
        _applicationVersion = options?.ApplicationVersion ?? string.Empty;
        _bindingAggregator = options is null
            ? null
            : new BindingDiagnosticAggregator(
                this,
                options.BindingAggregationWindow,
                options.BindingBurstThreshold,
                options.MaxBindingFingerprints);
        _bindingMaintenanceInterval = options is null
            ? TimeSpan.Zero
            : TimeSpan.FromMilliseconds(Math.Min(500, options.BindingAggregationWindow.TotalMilliseconds));
    }

    public string AppSessionId { get; }

    public bool IsEnabled { get; }

    public bool CanUpload { get; }

    public long BindingAggregateCount => Interlocked.Read(ref _bindingAggregateCount);

    internal ChannelReader<DiagnosticEnvelope> Events => _events.Reader;

    internal Task Completion { get; }

    internal long DroppedEventCount => Interlocked.Read(ref _droppedEventCount);

    internal string ApplicationVersion => _applicationVersion;

    public static ReliabilitySensor Start(
        ReliabilitySensorOptions? options,
        CancellationToken applicationStopping = default,
        Action<SensorDiagnostic>? diagnosticLogger = null)
    {
        var appSessionId = Guid.NewGuid().ToString("N");
        if (options is null)
        {
            Emit(diagnosticLogger, SensorDiagnostic.InitializationFailed);
            return Disabled(appSessionId, diagnosticLogger);
        }

        try
        {
            Validate(options);
            var canUpload = !string.IsNullOrWhiteSpace(options.DeviceToken);
            var lifetime = CancellationTokenSource.CreateLinkedTokenSource(applicationStopping);
            var events = Channel.CreateBounded<DiagnosticEnvelope>(new BoundedChannelOptions(options.EventChannelCapacity)
            {
                FullMode = BoundedChannelFullMode.Wait,
                SingleReader = true,
                SingleWriter = false,
                AllowSynchronousContinuations = false,
            });
            var sensor = new ReliabilitySensor(
                options,
                appSessionId,
                true,
                canUpload,
                lifetime,
                events,
                options.DisableBackgroundPersistence
                    ? WaitForCancellationAsync(lifetime.Token)
                    : PersistEventsAsync(
                        events.Reader,
                        options.OutboxPath ?? SqliteOutbox.GetDefaultPath(options.ApplicationId),
                        lifetime.Token,
                        diagnosticLogger),
                diagnosticLogger);
            if (!canUpload)
            {
                Emit(diagnosticLogger, SensorDiagnostic.MissingDeviceToken);
            }

            Emit(diagnosticLogger, SensorDiagnostic.Started);
            return sensor;
        }
        catch (Exception)
        {
            Emit(diagnosticLogger, SensorDiagnostic.InitializationFailed);
            return Disabled(appSessionId, diagnosticLogger);
        }
    }

    internal bool IsCurrentSession(string appSessionId) =>
        string.Equals(AppSessionId, appSessionId, StringComparison.Ordinal);

    internal string GetElementId(object element) => _elementIds.GetOrCreate(element);

    public void InstallBindingDiagnostics()
    {
        if (!IsEnabled || _bindingAggregator is null || _lifetime.IsCancellationRequested)
        {
            return;
        }

        lock (_bindingLifecycleLock)
        {
            _bindingTraceListener ??= new BindingTraceListener(_bindingAggregator.Accept);
            _bindingTraceListener.Install();
            if (_bindingMaintenanceTask.IsCompleted)
            {
                _bindingMaintenanceTask = RunBindingMaintenanceAsync(
                    _bindingAggregator,
                    _bindingMaintenanceInterval,
                    _lifetime.Token);
            }
        }
    }

    public void StopBindingDiagnostics()
    {
        lock (_bindingLifecycleLock)
        {
            _bindingTraceListener?.Uninstall();
        }
    }

    public bool ReportBindingFailure(
        string bindingPath,
        string targetProperty,
        string elementType,
        string? elementName = null)
    {
        if (!IsEnabled
            || _bindingAggregator is null
            || !IsValidMetadata(bindingPath, 512)
            || !IsValidMetadata(targetProperty, 256)
            || !IsValidMetadata(elementType, 256)
            || (elementName is not null && !IsValidMetadata(elementName, 256)))
        {
            return false;
        }

        _bindingAggregator.Accept(new BindingDiagnostic(
            DateTimeOffset.UtcNow,
            "PROPERTY_NOT_FOUND",
            bindingPath,
            targetProperty,
            elementType,
            elementName,
            WasTruncated: false));
        return true;
    }

    internal void RecordBindingAggregateQueued()
    {
        var count = Interlocked.Increment(ref _bindingAggregateCount);
        Debug.WriteLine($"Reliability sensor binding aggregate count: {count}");
        Emit(_diagnosticLogger, SensorDiagnostic.BindingAggregateQueued);
    }

    internal void RecordDroppedBindingDiagnostic() => RecordDrop();

    internal bool TryEnqueue(
        EventType eventType,
        Severity severity,
        JsonElement correlation,
        JsonElement payload,
        out DiagnosticEnvelope? envelope)
    {
        envelope = null;
        if (!IsEnabled || Volatile.Read(ref _disposed) != 0 || _lifetime.IsCancellationRequested)
        {
            return false;
        }

        try
        {
            envelope = CreateEnvelope(eventType, severity, correlation, payload);
            var serialized = JsonSerializer.SerializeToUtf8Bytes(
                envelope,
                ContractJsonContext.Default.DiagnosticEnvelope);
            if (serialized.Length > _maxEventBytes || !_events.Writer.TryWrite(envelope))
            {
                envelope = null;
                RecordDrop();
                return false;
            }

            return true;
        }
        catch (Exception exception) when (exception is ArgumentException or InvalidOperationException or JsonException)
        {
            envelope = null;
            RecordDrop();
            return false;
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        StopBindingDiagnostics();
        StopCollectors();
        _events.Writer.TryComplete();
        _lifetime.Cancel();
        try
        {
            await Task.WhenAll(Completion, _bindingMaintenanceTask)
                .WaitAsync(_shutdownTimeout)
                .ConfigureAwait(false);
        }
        catch (TimeoutException)
        {
            Emit(_diagnosticLogger, SensorDiagnostic.ShutdownTimedOut);
        }
        finally
        {
            _lifetime.Dispose();
        }
    }

    private DiagnosticEnvelope CreateEnvelope(
        EventType eventType,
        Severity severity,
        JsonElement correlation,
        JsonElement payload)
    {
        if (correlation.ValueKind is not JsonValueKind.Object || payload.ValueKind is not JsonValueKind.Object)
        {
            throw new ArgumentException("Correlation and payload must be JSON objects.");
        }

        var payloadCopy = payload.Clone();
        return new DiagnosticEnvelope(
            "1.0",
            Guid.NewGuid().ToString("N"),
            eventType,
            severity,
            DateTimeOffset.UtcNow,
            _deviceId,
            _applicationId,
            _applicationVersion,
            AppSessionId,
            Interlocked.Increment(ref _sequence),
            correlation.Clone(),
            payloadCopy,
            RedactionProfile,
            CanonicalJson.Hash(payloadCopy));
    }

    private void RecordDrop()
    {
        Interlocked.Increment(ref _droppedEventCount);
        Emit(_diagnosticLogger, SensorDiagnostic.EventDropped);
    }

    private static ReliabilitySensor Disabled(
        string appSessionId,
        Action<SensorDiagnostic>? diagnosticLogger)
    {
        var lifetime = new CancellationTokenSource();
        lifetime.Cancel();
        var events = Channel.CreateBounded<DiagnosticEnvelope>(1);
        events.Writer.TryComplete();
        return new ReliabilitySensor(
            null,
            appSessionId,
            false,
            false,
            lifetime,
            events,
            Task.CompletedTask,
            diagnosticLogger);
    }

    private static void Validate(ReliabilitySensorOptions options)
    {
        if (!options.ApiBaseUri.IsAbsoluteUri || options.ApiBaseUri.Scheme != Uri.UriSchemeHttps)
        {
            throw new ArgumentException("API base URI must be an absolute HTTPS URI.");
        }

        RequireIdentifier(options.DeviceId, nameof(options.DeviceId));
        RequireIdentifier(options.ApplicationId, nameof(options.ApplicationId));
        if (string.IsNullOrWhiteSpace(options.ApplicationVersion) || options.ApplicationVersion.Length > 64)
        {
            throw new ArgumentException("Application version must contain 1 to 64 characters.");
        }

        if (options.EventChannelCapacity is < 1 or > 500)
        {
            throw new ArgumentOutOfRangeException(nameof(options.EventChannelCapacity));
        }

        if (options.MaxEventBytes is < 512 or > 65_536)
        {
            throw new ArgumentOutOfRangeException(nameof(options.MaxEventBytes));
        }

        if (options.ShutdownTimeout <= TimeSpan.Zero || options.ShutdownTimeout > TimeSpan.FromSeconds(30))
        {
            throw new ArgumentOutOfRangeException(nameof(options.ShutdownTimeout));
        }

        if (options.BindingAggregationWindow < TimeSpan.FromMilliseconds(100)
            || options.BindingAggregationWindow > TimeSpan.FromMinutes(1))
        {
            throw new ArgumentOutOfRangeException(nameof(options.BindingAggregationWindow));
        }

        if (options.BindingBurstThreshold is < 1 or > 1_000)
        {
            throw new ArgumentOutOfRangeException(nameof(options.BindingBurstThreshold));
        }

        if (options.MaxBindingFingerprints is < 1 or > 500)
        {
            throw new ArgumentOutOfRangeException(nameof(options.MaxBindingFingerprints));
        }
    }

    private static void RequireIdentifier(string value, string name)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Length > 256)
        {
            throw new ArgumentException("Identifier must contain 1 to 256 characters.", name);
        }
    }

    private static bool IsValidMetadata(string value, int maxLength) =>
        !string.IsNullOrWhiteSpace(value) && value.Length <= maxLength;

    private static async Task WaitForCancellationAsync(CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
    }

    private static async Task RunBindingMaintenanceAsync(
        BindingDiagnosticAggregator aggregator,
        TimeSpan interval,
        CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(interval);
        try
        {
            while (await timer.WaitForNextTickAsync(cancellationToken).ConfigureAwait(false))
            {
                aggregator.FlushExpired(DateTimeOffset.UtcNow);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
    }

    private static void Emit(Action<SensorDiagnostic>? logger, SensorDiagnostic diagnostic)
    {
        try
        {
            logger?.Invoke(diagnostic);
        }
        catch (Exception)
        {
            // A local diagnostic sink must never affect the host application.
        }
    }

}
