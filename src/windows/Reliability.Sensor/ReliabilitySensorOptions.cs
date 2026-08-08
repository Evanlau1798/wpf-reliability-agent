using System.IO;
using System.Net.Http;

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

    internal HttpMessageHandler? TelemetryHandler { get; init; }

    internal TimeSpan RelayPollInterval { get; init; } = TimeSpan.FromSeconds(1);

    internal string SourceMapPath { get; init; } = Path.Combine(AppContext.BaseDirectory, "source-map.json");
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
