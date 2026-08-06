using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed record OutboxSchemaSummary(
    int Version,
    IReadOnlyList<string> Tables,
    string JournalMode,
    long SynchronousMode);

internal sealed record OutboxEvent(
    string EventId,
    DiagnosticEnvelope Envelope,
    int AttemptCount,
    DateTimeOffset NextAttemptAtUtc,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset? SentAtUtc);

internal enum CommandClaimStatus
{
    CLAIMED,
    COMPLETED,
    NEEDS_REVIEW,
    CONFLICT,
}

internal sealed record CompletedCommand(string ResultJson, string ResultHash, DateTimeOffset CompletedAtUtc);

internal sealed class SqliteOutbox : IAsyncDisposable
{
    private const string RedactionProfile = "metadata-only-v1";
    private static readonly int[] RetrySeconds = [1, 2, 5, 10, 30, 60, 300];
    private readonly SqliteConnection _connection;
    private readonly string _path;
    private readonly int _maxUnsentEvents;
    private readonly long _maxDatabaseBytes;

    private SqliteOutbox(
        SqliteConnection connection,
        string path,
        int maxUnsentEvents,
        long maxDatabaseBytes)
    {
        _connection = connection;
        _path = path;
        _maxUnsentEvents = maxUnsentEvents;
        _maxDatabaseBytes = maxDatabaseBytes;
    }

    public static string GetDefaultPath(string applicationId)
    {
        var root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return System.IO.Path.Combine(root, "WpfReliabilityAgent", applicationId, "outbox.db");
    }

    public static async Task<SqliteOutbox> OpenAsync(
        string path,
        int maxUnsentEvents = 5_000,
        long maxDatabaseBytes = 10 * 1024 * 1024,
        CancellationToken cancellationToken = default)
    {
        if (maxUnsentEvents is < 1 or > 5_000 || maxDatabaseBytes < 64 * 1024)
        {
            throw new ArgumentOutOfRangeException(nameof(maxUnsentEvents));
        }

        var fullPath = System.IO.Path.GetFullPath(path);
        System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(fullPath)!);
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder
        {
            DataSource = fullPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Pooling = false,
        }.ToString());
        await connection.OpenAsync(cancellationToken).ConfigureAwait(false);
        var outbox = new SqliteOutbox(connection, fullPath, maxUnsentEvents, maxDatabaseBytes);
        try
        {
            await outbox.InitializeAsync(cancellationToken).ConfigureAwait(false);
            return outbox;
        }
        catch
        {
            await connection.DisposeAsync().ConfigureAwait(false);
            throw;
        }
    }

    public static TimeSpan RetryDelay(int attemptCount)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(attemptCount);
        return TimeSpan.FromSeconds(RetrySeconds[Math.Min(attemptCount, RetrySeconds.Length) - 1]);
    }

    public async Task<bool> TryAddEventAsync(
        DiagnosticEnvelope envelope,
        DateTimeOffset? createdAtUtc = null,
        CancellationToken cancellationToken = default)
    {
        var json = JsonSerializer.Serialize(envelope, ContractJsonContext.Default.DiagnosticEnvelope);
        if (envelope.RedactionProfile != RedactionProfile || !ContractValidator.Validate(envelope, json))
        {
            throw new ArgumentException("Only valid metadata-redacted diagnostic envelopes may enter the outbox.", nameof(envelope));
        }

        var created = createdAtUtc ?? DateTimeOffset.UtcNow;
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            INSERT OR IGNORE INTO outbound_events(
                event_id, payload_json, payload_hash, state, attempt_count,
                next_attempt_at, created_at, sent_at)
            VALUES($event_id, $payload_json, $payload_hash, 'pending', 0,
                $created_at, $created_at, NULL);
            """;
        command.Parameters.AddWithValue("$event_id", envelope.EventId);
        command.Parameters.AddWithValue("$payload_json", json);
        command.Parameters.AddWithValue("$payload_hash", envelope.EvidenceHash);
        command.Parameters.AddWithValue("$created_at", Format(created));
        return await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false) == 1;
    }

    public async Task<IReadOnlyList<OutboxEvent>> GetDueEventsAsync(
        DateTimeOffset nowUtc,
        int limit,
        CancellationToken cancellationToken = default)
    {
        if (limit is < 1 or > 50)
        {
            throw new ArgumentOutOfRangeException(nameof(limit));
        }

        await using var command = _connection.CreateCommand();
        command.CommandText = """
            SELECT event_id, payload_json, attempt_count, next_attempt_at, created_at, sent_at
            FROM outbound_events
            WHERE state = 'pending' AND next_attempt_at <= $now
            ORDER BY next_attempt_at, created_at, event_id
            LIMIT $limit;
            """;
        command.Parameters.AddWithValue("$now", Format(nowUtc));
        command.Parameters.AddWithValue("$limit", limit);
        var events = new List<OutboxEvent>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken).ConfigureAwait(false);
        while (await reader.ReadAsync(cancellationToken).ConfigureAwait(false))
        {
            events.Add(ReadEvent(reader));
        }

        return events;
    }

    public async Task<OutboxEvent?> GetEventAsync(string eventId, CancellationToken cancellationToken = default)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            SELECT event_id, payload_json, attempt_count, next_attempt_at, created_at, sent_at
            FROM outbound_events WHERE event_id = $event_id;
            """;
        command.Parameters.AddWithValue("$event_id", eventId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken).ConfigureAwait(false);
        return await reader.ReadAsync(cancellationToken).ConfigureAwait(false) ? ReadEvent(reader) : null;
    }

    public async Task MarkSentAsync(
        string eventId,
        DateTimeOffset sentAtUtc,
        CancellationToken cancellationToken = default)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            UPDATE outbound_events
            SET state = 'sent', sent_at = $sent_at
            WHERE event_id = $event_id AND state = 'pending';
            """;
        command.Parameters.AddWithValue("$event_id", eventId);
        command.Parameters.AddWithValue("$sent_at", Format(sentAtUtc));
        await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
    }

    public async Task MarkRetryAsync(
        string eventId,
        DateTimeOffset nowUtc,
        CancellationToken cancellationToken = default)
    {
        var current = await GetEventAsync(eventId, cancellationToken).ConfigureAwait(false);
        if (current is null || current.SentAtUtc is not null)
        {
            return;
        }

        var attemptCount = current.AttemptCount + 1;
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            UPDATE outbound_events
            SET attempt_count = $attempt_count, next_attempt_at = $next_attempt_at
            WHERE event_id = $event_id AND state = 'pending';
            """;
        command.Parameters.AddWithValue("$event_id", eventId);
        command.Parameters.AddWithValue("$attempt_count", attemptCount);
        command.Parameters.AddWithValue("$next_attempt_at", Format(nowUtc + RetryDelay(attemptCount)));
        await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
    }

    public async Task<int> EnforceLimitsAsync(CancellationToken cancellationToken = default)
    {
        var dropped = await DeleteUnsentExcessAsync(cancellationToken).ConfigureAwait(false);
        if (CurrentDatabaseBytes() <= _maxDatabaseBytes)
        {
            return dropped;
        }

        while (CurrentDatabaseBytes() > _maxDatabaseBytes)
        {
            var deletion = await DeleteOldestOutboundEventAsync(cancellationToken).ConfigureAwait(false);
            if (deletion.Deleted == 0)
            {
                break;
            }

            dropped += deletion.WasPending;
            await ExecuteAsync("VACUUM;", cancellationToken).ConfigureAwait(false);
            await ExecuteAsync("PRAGMA wal_checkpoint(TRUNCATE);", cancellationToken).ConfigureAwait(false);
        }

        return dropped;
    }

    public async Task<CommandClaimStatus> BeginCommandAsync(
        string commandId,
        string argumentsHash,
        DateTimeOffset startedAtUtc,
        CancellationToken cancellationToken = default)
    {
        var existing = await GetCommandStateAsync(commandId, cancellationToken).ConfigureAwait(false);
        if (existing is not null)
        {
            if (!string.Equals(existing.Value.ArgumentsHash, argumentsHash, StringComparison.Ordinal))
            {
                return CommandClaimStatus.CONFLICT;
            }

            return existing.Value.State == "completed"
                ? CommandClaimStatus.COMPLETED
                : CommandClaimStatus.NEEDS_REVIEW;
        }

        await using var command = _connection.CreateCommand();
        command.CommandText = """
            INSERT INTO command_executions(
                command_id, arguments_hash, state, result_json, result_hash, started_at, completed_at)
            VALUES($command_id, $arguments_hash, 'in_progress', NULL, NULL, $started_at, NULL);
            """;
        command.Parameters.AddWithValue("$command_id", commandId);
        command.Parameters.AddWithValue("$arguments_hash", argumentsHash);
        command.Parameters.AddWithValue("$started_at", Format(startedAtUtc));
        try
        {
            await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
            return CommandClaimStatus.CLAIMED;
        }
        catch (SqliteException exception) when (exception.SqliteErrorCode == 19)
        {
            return await BeginCommandAsync(commandId, argumentsHash, startedAtUtc, cancellationToken).ConfigureAwait(false);
        }
    }

    public async Task CompleteCommandAsync(
        string commandId,
        string argumentsHash,
        string resultJson,
        string resultHash,
        DateTimeOffset completedAtUtc,
        CancellationToken cancellationToken = default)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            UPDATE command_executions
            SET state = 'completed', result_json = $result_json,
                result_hash = $result_hash, completed_at = $completed_at
            WHERE command_id = $command_id AND arguments_hash = $arguments_hash
                AND state = 'in_progress';
            """;
        command.Parameters.AddWithValue("$command_id", commandId);
        command.Parameters.AddWithValue("$arguments_hash", argumentsHash);
        command.Parameters.AddWithValue("$result_json", resultJson);
        command.Parameters.AddWithValue("$result_hash", resultHash);
        command.Parameters.AddWithValue("$completed_at", Format(completedAtUtc));
        if (await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false) != 1)
        {
            throw new InvalidOperationException("The command execution cannot be completed from its current state.");
        }
    }

    public async Task<CompletedCommand?> LoadCompletedCommandAsync(
        string commandId,
        string argumentsHash,
        CancellationToken cancellationToken = default)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            SELECT result_json, result_hash, completed_at
            FROM command_executions
            WHERE command_id = $command_id AND arguments_hash = $arguments_hash AND state = 'completed';
            """;
        command.Parameters.AddWithValue("$command_id", commandId);
        command.Parameters.AddWithValue("$arguments_hash", argumentsHash);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken).ConfigureAwait(false);
        return await reader.ReadAsync(cancellationToken).ConfigureAwait(false)
            ? new CompletedCommand(reader.GetString(0), reader.GetString(1), Parse(reader.GetString(2)))
            : null;
    }

    public async Task<OutboxSchemaSummary> GetSchemaSummaryAsync(CancellationToken cancellationToken = default)
    {
        var tables = new List<string>();
        await using (var command = _connection.CreateCommand())
        {
            command.CommandText = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken).ConfigureAwait(false);
            while (await reader.ReadAsync(cancellationToken).ConfigureAwait(false))
            {
                tables.Add(reader.GetString(0));
            }
        }

        var version = Convert.ToInt32(await ScalarAsync("PRAGMA user_version;", cancellationToken), CultureInfo.InvariantCulture);
        var journal = Convert.ToString(await ScalarAsync("PRAGMA journal_mode;", cancellationToken), CultureInfo.InvariantCulture)!;
        var synchronous = Convert.ToInt64(await ScalarAsync("PRAGMA synchronous;", cancellationToken), CultureInfo.InvariantCulture);
        return new OutboxSchemaSummary(version, tables, journal, synchronous);
    }

    public ValueTask DisposeAsync() => _connection.DisposeAsync();

    private async Task InitializeAsync(CancellationToken cancellationToken)
    {
        // WAL preserves committed events across process failure; FULL sync prioritizes durability over write throughput.
        await ExecuteAsync("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = FULL;
            PRAGMA busy_timeout = 5000;
            PRAGMA user_version = 1;
            CREATE TABLE IF NOT EXISTS outbound_events(
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending', 'sent')),
                attempt_count INTEGER NOT NULL,
                next_attempt_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT NULL);
            CREATE INDEX IF NOT EXISTS ix_outbound_due
                ON outbound_events(state, next_attempt_at, created_at, event_id);
            CREATE TABLE IF NOT EXISTS command_executions(
                command_id TEXT PRIMARY KEY,
                arguments_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('in_progress', 'completed')),
                result_json TEXT NULL,
                result_hash TEXT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NULL);
            """, cancellationToken).ConfigureAwait(false);
    }

    private async Task<int> DeleteUnsentExcessAsync(CancellationToken cancellationToken)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            DELETE FROM outbound_events
            WHERE event_id IN (
                SELECT event_id FROM outbound_events
                WHERE state = 'pending'
                ORDER BY created_at, event_id
                LIMIT MAX(0, (SELECT COUNT(*) FROM outbound_events WHERE state = 'pending') - $maximum));
            """;
        command.Parameters.AddWithValue("$maximum", _maxUnsentEvents);
        return await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
    }

    private async Task<(int Deleted, int WasPending)> DeleteOldestOutboundEventAsync(CancellationToken cancellationToken)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = """
            DELETE FROM outbound_events
            WHERE event_id = (
                SELECT event_id FROM outbound_events
                ORDER BY CASE state WHEN 'sent' THEN 0 ELSE 1 END, created_at, event_id
                LIMIT 1)
            RETURNING state;
            """;
        var state = await command.ExecuteScalarAsync(cancellationToken).ConfigureAwait(false) as string;
        return state is null ? (0, 0) : (1, state == "pending" ? 1 : 0);
    }

    private async Task<(string ArgumentsHash, string State)?> GetCommandStateAsync(
        string commandId,
        CancellationToken cancellationToken)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = "SELECT arguments_hash, state FROM command_executions WHERE command_id = $command_id;";
        command.Parameters.AddWithValue("$command_id", commandId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken).ConfigureAwait(false);
        return await reader.ReadAsync(cancellationToken).ConfigureAwait(false)
            ? (reader.GetString(0), reader.GetString(1))
            : null;
    }

    private async Task ExecuteAsync(string sql, CancellationToken cancellationToken)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync(cancellationToken).ConfigureAwait(false);
    }

    private long CurrentDatabaseBytes() =>
        FileLength(_path) + FileLength($"{_path}-wal");

    private static long FileLength(string path) =>
        System.IO.File.Exists(path) ? new System.IO.FileInfo(path).Length : 0;

    private async Task<object?> ScalarAsync(string sql, CancellationToken cancellationToken)
    {
        await using var command = _connection.CreateCommand();
        command.CommandText = sql;
        return await command.ExecuteScalarAsync(cancellationToken).ConfigureAwait(false);
    }

    private static OutboxEvent ReadEvent(SqliteDataReader reader)
    {
        var envelope = JsonSerializer.Deserialize(
            reader.GetString(1),
            ContractJsonContext.Default.DiagnosticEnvelope)
            ?? throw new JsonException("Stored diagnostic envelope is invalid.");
        return new OutboxEvent(
            reader.GetString(0),
            envelope,
            reader.GetInt32(2),
            Parse(reader.GetString(3)),
            Parse(reader.GetString(4)),
            reader.IsDBNull(5) ? null : Parse(reader.GetString(5)));
    }

    private static string Format(DateTimeOffset value) =>
        value.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);

    private static DateTimeOffset Parse(string value) =>
        DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
}
