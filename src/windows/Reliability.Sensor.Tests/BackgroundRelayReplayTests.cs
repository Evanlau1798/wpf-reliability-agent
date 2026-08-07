using System.Collections.Concurrent;
using System.Net;
using System.Text;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class BackgroundRelayReplayTests
{
    [Fact]
    public async Task PendingEventUploadsAfterServerReturnsOnline()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-replay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new RecoveringHandler();

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await using var sensor = ReliabilitySensor.Start(Options(path, handler));
            Assert.True(sensor.TryEnqueue(
                EventType.BindingAggregate,
                Severity.ERROR,
                JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
                JsonSerializer.SerializeToElement(new { count = 1 }),
                out var envelope));

            var retried = await WaitForEventAsync(
                inspection,
                envelope!.EventId,
                item => item.AttemptCount == 1);
            handler.GoOnline();
            var sent = await WaitForEventAsync(
                inspection,
                envelope.EventId,
                item => item.SentAtUtc is not null);

            Assert.Equal(1, retried?.AttemptCount);
            Assert.NotNull(sent?.SentAtUtc);
            Assert.Equal(2, handler.RequestCount);
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
    public async Task OfflineReplayPreservesOrderAndCompletesServerDuplicates()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "relay-replay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new RecoveringHandler();
        var created = DateTimeOffset.UtcNow.AddMinutes(-1);

        try
        {
            await using var inspection = await SqliteOutbox.OpenAsync(path);
            await inspection.TryAddEventAsync(Envelope("event-a", 1), created);
            await inspection.TryAddEventAsync(Envelope("event-b", 2), created.AddMilliseconds(1));
            await using var sensor = ReliabilitySensor.Start(Options(path, handler));

            Assert.NotNull(await WaitForEventAsync(
                inspection,
                "event-b",
                item => item.AttemptCount == 1));
            handler.GoOnline("event-b");
            Assert.NotNull(await WaitForEventAsync(
                inspection,
                "event-a",
                item => item.SentAtUtc is not null));
            Assert.NotNull(await WaitForEventAsync(
                inspection,
                "event-b",
                item => item.SentAtUtc is not null));

            Assert.Equal(2, handler.Batches.Count);
            Assert.All(handler.Batches, batch => Assert.Equal(["event-a", "event-b"], batch));
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
        Func<OutboxEvent, bool> predicate)
    {
        for (var attempt = 0; attempt < 300; attempt++)
        {
            var stored = await outbox.GetEventAsync(eventId);
            if (stored is not null && predicate(stored))
            {
                return stored;
            }

            await Task.Delay(10);
        }

        return null;
    }

    private static ReliabilitySensorOptions Options(string path, HttpMessageHandler handler) => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
        OutboxPath = path,
        TelemetryHandler = handler,
        RelayPollInterval = TimeSpan.FromMilliseconds(10),
    };

    private static DiagnosticEnvelope Envelope(string eventId, long sequence)
    {
        var payload = JsonSerializer.SerializeToElement(new { count = sequence });
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
            sequence,
            JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
            payload,
            "metadata-only-v1",
            CanonicalJson.Hash(payload));
    }

    private sealed class RecoveringHandler : HttpMessageHandler
    {
        private readonly ConcurrentQueue<IReadOnlyList<string>> _batches = new();
        private string? _duplicateEventId;
        private int _online;
        private int _requestCount;

        public IReadOnlyList<IReadOnlyList<string>> Batches => _batches.ToArray();

        public int RequestCount => Volatile.Read(ref _requestCount);

        public void GoOnline(string? duplicateEventId = null)
        {
            Volatile.Write(ref _duplicateEventId, duplicateEventId);
            Volatile.Write(ref _online, 1);
        }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (request.RequestUri?.AbsolutePath.EndsWith("commands:lease", StringComparison.Ordinal) is true)
            {
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
                return new HttpResponseMessage(HttpStatusCode.NoContent);
            }

            Interlocked.Increment(ref _requestCount);
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            var eventIds = document.RootElement.GetProperty("events")
                .EnumerateArray()
                .Select(item => item.GetProperty("event_id").GetString()!)
                .ToArray();
            _batches.Enqueue(eventIds);
            if (Volatile.Read(ref _online) == 0)
            {
                throw new HttpRequestException("The fake network is offline.");
            }

            var duplicateEventId = Volatile.Read(ref _duplicateEventId);
            var response = JsonSerializer.Serialize(new
            {
                accepted_event_ids = eventIds.Where(item => item != duplicateEventId),
                duplicate_event_ids = eventIds.Where(item => item == duplicateEventId),
                rejected = Array.Empty<object>(),
            });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(response, Encoding.UTF8, "application/json"),
            };
        }
    }
}
