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

    private sealed class RecoveringHandler : HttpMessageHandler
    {
        private int _online;
        private int _requestCount;

        public int RequestCount => Volatile.Read(ref _requestCount);

        public void GoOnline() => Volatile.Write(ref _online, 1);

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Interlocked.Increment(ref _requestCount);
            if (Volatile.Read(ref _online) == 0)
            {
                throw new HttpRequestException("The fake network is offline.");
            }

            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            var eventIds = document.RootElement.GetProperty("events")
                .EnumerateArray()
                .Select(item => item.GetProperty("event_id").GetString()!)
                .ToArray();
            var response = JsonSerializer.Serialize(new
            {
                accepted_event_ids = eventIds,
                duplicate_event_ids = Array.Empty<string>(),
                rejected = Array.Empty<object>(),
            });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(response, Encoding.UTF8, "application/json"),
            };
        }
    }
}
