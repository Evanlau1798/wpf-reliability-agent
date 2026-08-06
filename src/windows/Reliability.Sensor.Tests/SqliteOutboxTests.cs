using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class SqliteOutboxTests
{
    [Fact]
    public void DefaultPathUsesLocalApplicationDataOutsideTheWorkspace()
    {
        var path = SqliteOutbox.GetDefaultPath("demo-broken-wpf-app");

        Assert.StartsWith(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            path,
            StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("wpf-reliability-agent", path, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task FreshDatabaseCreatesVersionedTablesAndDurabilityPragmas()
    {
        await using var database = await TestDatabase.CreateAsync();

        var schema = await database.Outbox.GetSchemaSummaryAsync();

        Assert.Equal(1, schema.Version);
        Assert.Contains("outbound_events", schema.Tables);
        Assert.Contains("command_executions", schema.Tables);
        Assert.Equal("wal", schema.JournalMode, StringComparer.OrdinalIgnoreCase);
        Assert.Equal(2, schema.SynchronousMode);
    }

    [Fact]
    public async Task EventsSurviveRestartAndDuplicateIdsAreNoOp()
    {
        await using var database = await TestDatabase.CreateAsync();
        var envelope = Envelope("event-1", sequence: 1);

        Assert.True(await database.Outbox.TryAddEventAsync(envelope));
        Assert.False(await database.Outbox.TryAddEventAsync(envelope));
        await database.ReopenAsync();

        var due = await database.Outbox.GetDueEventsAsync(DateTimeOffset.UtcNow.AddMinutes(1), 50);
        Assert.Single(due);
        Assert.Equal("event-1", due[0].EventId);
    }

    [Fact]
    public async Task DueEventsHaveDeterministicOrderAndRetryState()
    {
        await using var database = await TestDatabase.CreateAsync();
        var now = DateTimeOffset.UtcNow;
        await database.Outbox.TryAddEventAsync(Envelope("event-b", 2), now.AddSeconds(-2));
        await database.Outbox.TryAddEventAsync(Envelope("event-a", 1), now.AddSeconds(-2));

        var initial = await database.Outbox.GetDueEventsAsync(now, 50);
        await database.Outbox.MarkRetryAsync("event-a", now);
        var retried = await database.Outbox.GetEventAsync("event-a");
        await database.Outbox.MarkSentAsync("event-b", now);
        var sent = await database.Outbox.GetEventAsync("event-b");

        Assert.Equal(["event-a", "event-b"], initial.Select(item => item.EventId));
        Assert.Equal(1, retried?.AttemptCount);
        Assert.Equal(now.AddSeconds(1), retried?.NextAttemptAtUtc);
        Assert.Equal(now, sent?.SentAtUtc);
    }

    [Fact]
    public void RetryBackoffUsesTheApprovedBoundedSequence()
    {
        Assert.Equal(
            [1, 2, 5, 10, 30, 60, 300, 300],
            Enumerable.Range(1, 8).Select(attempt => (int)SqliteOutbox.RetryDelay(attempt).TotalSeconds));
    }

    [Fact]
    public async Task InsertRejectsUnclassifiedPayload()
    {
        await using var database = await TestDatabase.CreateAsync();
        var envelope = Envelope("event-1", 1) with { RedactionProfile = "unknown" };

        await Assert.ThrowsAsync<ArgumentException>(() => database.Outbox.TryAddEventAsync(envelope));
    }

    [Fact]
    public async Task UnsentLimitDropsOldestRowsFirst()
    {
        await using var database = await TestDatabase.CreateAsync(maxUnsentEvents: 2);
        var now = DateTimeOffset.UtcNow;
        await database.Outbox.TryAddEventAsync(Envelope("event-1", 1), now.AddSeconds(-3));
        await database.Outbox.TryAddEventAsync(Envelope("event-2", 2), now.AddSeconds(-2));
        await database.Outbox.TryAddEventAsync(Envelope("event-3", 3), now.AddSeconds(-1));

        var dropped = await database.Outbox.EnforceLimitsAsync();
        var due = await database.Outbox.GetDueEventsAsync(now, 50);

        Assert.Equal(1, dropped);
        Assert.Equal(["event-2", "event-3"], due.Select(item => item.EventId));
    }

    [Fact]
    public async Task DatabaseSizeCleanupRetainsActiveCommandJournalRows()
    {
        await using var database = await TestDatabase.CreateAsync(maxDatabaseBytes: 64 * 1024);
        var now = DateTimeOffset.UtcNow;
        Assert.Equal(CommandClaimStatus.CLAIMED, await database.Outbox.BeginCommandAsync("command-1", "hash-a", now));
        for (var index = 1; index <= 3; index++)
        {
            var payload = JsonSerializer.SerializeToElement(new { message = new string('x', 45_000) });
            var envelope = Envelope($"large-{index}", index) with
            {
                Payload = payload,
                EvidenceHash = CanonicalJson.Hash(payload),
            };
            await database.Outbox.TryAddEventAsync(envelope, now.AddSeconds(index));
        }

        var dropped = await database.Outbox.EnforceLimitsAsync();
        var commandState = await database.Outbox.BeginCommandAsync("command-1", "hash-a", now);

        Assert.True(dropped > 0);
        Assert.Equal(CommandClaimStatus.NEEDS_REVIEW, commandState);
    }

    [Fact]
    public async Task CommandJournalClaimsOnceReplaysCompletedAndRejectsHashConflict()
    {
        await using var database = await TestDatabase.CreateAsync();
        var now = DateTimeOffset.UtcNow;

        Assert.Equal(CommandClaimStatus.CLAIMED, await database.Outbox.BeginCommandAsync("command-1", "hash-a", now));
        Assert.Equal(CommandClaimStatus.NEEDS_REVIEW, await database.Outbox.BeginCommandAsync("command-1", "hash-a", now));
        Assert.Equal(CommandClaimStatus.CONFLICT, await database.Outbox.BeginCommandAsync("command-1", "hash-b", now));
        await database.Outbox.CompleteCommandAsync("command-1", "hash-a", "{\"status\":\"ok\"}", "result-hash", now);
        Assert.Equal(CommandClaimStatus.COMPLETED, await database.Outbox.BeginCommandAsync("command-1", "hash-a", now));

        var completed = await database.Outbox.LoadCompletedCommandAsync("command-1", "hash-a");
        Assert.Equal("result-hash", completed?.ResultHash);
        Assert.Equal("{\"status\":\"ok\"}", completed?.ResultJson);
        Assert.Null(await database.Outbox.LoadCompletedCommandAsync("command-1", "hash-b"));
    }

    private static DiagnosticEnvelope Envelope(string eventId, long sequence) => new(
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
        JsonSerializer.SerializeToElement(new { count = 1 }),
        "metadata-only-v1",
        CanonicalJson.Hash(JsonSerializer.SerializeToElement(new { count = 1 })));

    private sealed class TestDatabase : IAsyncDisposable
    {
        private readonly string _directory;
        private readonly int _maxUnsentEvents;

        private TestDatabase(string directory, string path, int maxUnsentEvents, SqliteOutbox outbox)
        {
            _directory = directory;
            Path = path;
            _maxUnsentEvents = maxUnsentEvents;
            Outbox = outbox;
        }

        public string Path { get; }

        public SqliteOutbox Outbox { get; private set; }

        public static async Task<TestDatabase> CreateAsync(
            int maxUnsentEvents = 5_000,
            long maxDatabaseBytes = 10 * 1024 * 1024)
        {
            var directory = System.IO.Path.GetFullPath(System.IO.Path.Combine(
                Environment.CurrentDirectory,
                "tmp",
                "outbox-tests",
                Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            var path = System.IO.Path.Combine(directory, "outbox.db");
            var outbox = await SqliteOutbox.OpenAsync(path, maxUnsentEvents, maxDatabaseBytes);
            return new TestDatabase(directory, path, maxUnsentEvents, outbox);
        }

        public async Task ReopenAsync()
        {
            await Outbox.DisposeAsync();
            Outbox = await SqliteOutbox.OpenAsync(Path, _maxUnsentEvents, 10 * 1024 * 1024);
        }

        public async ValueTask DisposeAsync()
        {
            await Outbox.DisposeAsync();
            Directory.Delete(_directory, recursive: true);
        }
    }
}
