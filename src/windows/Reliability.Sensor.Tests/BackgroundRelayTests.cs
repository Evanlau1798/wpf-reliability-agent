using System.Diagnostics;
using System.Net;
using System.Text;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class BackgroundRelayTests
{
    [Fact]
    public async Task ChannelConsumerPersistsEventsToSqlite()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await using var sensor = ReliabilitySensor.Start(ValidOptions(path));
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
                JsonSerializer.SerializeToElement(new { count = 1 }),
                out var envelope));

            var stored = await WaitForEventAsync(inspection, envelope!.EventId);

            Assert.Equal(envelope.EventId, stored?.Envelope.EventId);
            Assert.Equal(envelope.EvidenceHash, stored?.Envelope.EvidenceHash);
            Assert.Equal(envelope.Payload.GetRawText(), stored?.Envelope.Payload.GetRawText());
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public async Task UploadLoopSendsDueEventsAndMarksAcceptedRowsSent()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new AcceptingHandler();
        var diagnostics = new List<SensorDiagnostic>();

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await inspection.TryAddEventAsync(
                Envelope("future-event"),
                DateTimeOffset.UtcNow.AddMinutes(1));
            await using var sensor = ReliabilitySensor.Start(
                ValidOptions(path) with
                {
                    TelemetryHandler = handler,
                    RelayPollInterval = TimeSpan.FromMilliseconds(10),
                },
                diagnosticLogger: diagnostics.Add);
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
                JsonSerializer.SerializeToElement(new { count = 1 }),
                out var envelope));

            var stored = await WaitForEventAsync(
                inspection,
                envelope!.EventId,
                item => item.SentAtUtc is not null);

            Assert.True(
                handler.EventIds.Count > 0,
                $"No upload was observed. Diagnostics: {string.Join(',', diagnostics)}");
            Assert.NotNull(stored?.SentAtUtc);
            Assert.Equal([envelope.EventId], handler.EventIds);
            Assert.Null((await inspection.GetEventAsync("future-event"))?.SentAtUtc);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public async Task UploadLoopAppliesPerEventServerResultsToOutboxRows()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new MixedResultHandler();

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await inspection.TryAddEventAsync(Envelope("accepted-event"));
            await inspection.TryAddEventAsync(Envelope("duplicate-event"));
            await inspection.TryAddEventAsync(Envelope("rejected-event"));
            await using var sensor = ReliabilitySensor.Start(ValidOptions(path) with
            {
                TelemetryHandler = handler,
                RelayPollInterval = TimeSpan.FromMilliseconds(10),
            });

            var accepted = await WaitForEventAsync(
                inspection,
                "accepted-event",
                item => item.SentAtUtc is not null);
            var duplicate = await WaitForEventAsync(
                inspection,
                "duplicate-event",
                item => item.SentAtUtc is not null);
            var rejected = await WaitForEventRemovalAsync(inspection, "rejected-event");

            Assert.NotNull(accepted?.SentAtUtc);
            Assert.NotNull(duplicate?.SentAtUtc);
            Assert.True(rejected);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public async Task CancellationDrainsQueuedEventsAndCompletesRelay()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new BlockingHandler();
        using var applicationStopping = new CancellationTokenSource();

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await using var sensor = ReliabilitySensor.Start(
                ValidOptions(path) with
                {
                    TelemetryHandler = handler,
                    RelayPollInterval = TimeSpan.FromMilliseconds(10),
                },
                applicationStopping.Token);
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "First" }),
                JsonSerializer.SerializeToElement(new { count = 1 }),
                out _));
            await handler.Started.WaitAsync(TimeSpan.FromSeconds(1));
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "Second" }),
                JsonSerializer.SerializeToElement(new { count = 2 }),
                out var queuedDuringUpload));

            applicationStopping.Cancel();
            await sensor.Completion.WaitAsync(TimeSpan.FromSeconds(1));

            Assert.True(sensor.Completion.IsCompletedSuccessfully);
            Assert.NotNull(await inspection.GetEventAsync(queuedDuringUpload!.EventId));
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public async Task OfflineUploadBacksOffWithoutBlockingCollection()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new OfflineHandler();

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await using var sensor = ReliabilitySensor.Start(ValidOptions(path) with
            {
                TelemetryHandler = handler,
                RelayPollInterval = TimeSpan.FromMilliseconds(10),
            });
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "First" }),
                JsonSerializer.SerializeToElement(new { count = 1 }),
                out var first));
            await handler.Started.WaitAsync(TimeSpan.FromSeconds(1));

            var enqueueTime = Stopwatch.StartNew();
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "Second" }),
                JsonSerializer.SerializeToElement(new { count = 2 }),
                out _));
            enqueueTime.Stop();
            var failureReleasedAt = DateTimeOffset.UtcNow;
            handler.FailRequests();

            var retried = await WaitForEventAsync(
                inspection,
                first!.EventId,
                item => item.AttemptCount == 1);

            Assert.True(enqueueTime.Elapsed < TimeSpan.FromMilliseconds(250));
            Assert.Equal(1, retried?.AttemptCount);
            Assert.True(retried?.NextAttemptAtUtc >= failureReleasedAt.AddMilliseconds(900));
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private static async Task<OutboxEvent?> WaitForEventAsync(
        SqliteOutbox outbox,
        string eventId,
        Func<OutboxEvent, bool>? predicate = null)
    {
        for (var attempt = 0; attempt < 100; attempt++)
        {
            var stored = await outbox.GetEventAsync(eventId);
            if (stored is not null && (predicate is null || predicate(stored)))
            {
                return stored;
            }

            await Task.Delay(10);
        }

        return null;
    }

    private static async Task<bool> WaitForEventRemovalAsync(SqliteOutbox outbox, string eventId)
    {
        for (var attempt = 0; attempt < 100; attempt++)
        {
            if (await outbox.GetEventAsync(eventId) is null)
            {
                return true;
            }

            await Task.Delay(10);
        }

        return false;
    }

    private static ReliabilitySensorOptions ValidOptions(string outboxPath) => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
        OutboxPath = outboxPath,
    };

    private static DiagnosticEnvelope Envelope(string eventId)
    {
        var payload = JsonSerializer.SerializeToElement(new { count = 1 });
        return new DiagnosticEnvelope(
            "1.0",
            eventId,
            EventType.BindingAggregate,
            Severity.ERROR,
            DateTimeOffset.UtcNow,
            "device-test",
            "demo-broken-wpf-app",
            "0.1.0",
            "session-test",
            1,
            JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
            payload,
            "metadata-only-v1",
            CanonicalJson.Hash(payload));
    }

    private sealed class AcceptingHandler : HttpMessageHandler
    {
        public IReadOnlyList<string> EventIds { get; private set; } = [];

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            EventIds = document.RootElement.GetProperty("events")
                .EnumerateArray()
                .Select(item => item.GetProperty("event_id").GetString()!)
                .ToArray();
            var response = JsonSerializer.Serialize(new
            {
                accepted_event_ids = EventIds,
                duplicate_event_ids = Array.Empty<string>(),
                rejected = Array.Empty<object>(),
            });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(response, Encoding.UTF8, "application/json"),
            };
        }
    }

    private sealed class MixedResultHandler : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            var eventIds = document.RootElement.GetProperty("events")
                .EnumerateArray()
                .Select(item => item.GetProperty("event_id").GetString()!)
                .ToHashSet(StringComparer.Ordinal);
            var response = JsonSerializer.Serialize(new
            {
                accepted_event_ids = eventIds.Contains("accepted-event")
                    ? new[] { "accepted-event" }
                    : Array.Empty<string>(),
                duplicate_event_ids = eventIds.Contains("duplicate-event")
                    ? new[] { "duplicate-event" }
                    : Array.Empty<string>(),
                rejected = eventIds.Contains("rejected-event")
                    ? new[] { new { event_id = "rejected-event", code = "INVALID_EVENT" } }
                    : Array.Empty<object>(),
            });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(response, Encoding.UTF8, "application/json"),
            };
        }
    }

    private sealed class BlockingHandler : HttpMessageHandler
    {
        private readonly TaskCompletionSource _started = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task Started => _started.Task;

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            _started.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("Cancellation must end the blocked upload.");
        }
    }

    private sealed class OfflineHandler : HttpMessageHandler
    {
        private readonly TaskCompletionSource _started = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private readonly TaskCompletionSource _fail = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task Started => _started.Task;

        public void FailRequests() => _fail.TrySetResult();

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            _started.TrySetResult();
            await _fail.Task.WaitAsync(cancellationToken);
            throw new HttpRequestException("The fake network is offline.");
        }
    }
}
