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

    private static async Task<OutboxEvent?> WaitForEventAsync(SqliteOutbox outbox, string eventId)
    {
        for (var attempt = 0; attempt < 100; attempt++)
        {
            var stored = await outbox.GetEventAsync(eventId);
            if (stored is not null)
            {
                return stored;
            }

            await Task.Delay(10);
        }

        return null;
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
}
