using System.Net.Http;
using System.Threading.Channels;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed partial class ReliabilitySensor
{
    private async Task RunRelayAsync(
        ChannelReader<DiagnosticEnvelope> events,
        string outboxPath,
        Uri? apiBaseUri,
        string? deviceToken,
        HttpMessageHandler? telemetryHandler,
        TimeSpan pollInterval,
        CancellationToken cancellationToken,
        Action<SensorDiagnostic>? diagnosticLogger)
    {
        try
        {
            await using var outbox = await SqliteOutbox.OpenAsync(outboxPath).ConfigureAwait(false);
            using var client = apiBaseUri is null || deviceToken is null
                ? null
                : new TelemetryApiClient(apiBaseUri, deviceToken, telemetryHandler);
            var commandTask = client is null
                ? Task.CompletedTask
                : RunReadOnlyCommandLoopAsync(client, outboxPath, cancellationToken);
            try
            {
                while (!cancellationToken.IsCancellationRequested)
                {
                    var persisted = await PersistAvailableAsync(events, outbox).ConfigureAwait(false);
                    if (persisted)
                    {
                        await outbox.EnforceLimitsAsync().ConfigureAwait(false);
                    }

                    if (client is not null)
                    {
                        await UploadDueAsync(outbox, client, cancellationToken).ConfigureAwait(false);
                    }

                    await Task.Delay(pollInterval, cancellationToken).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
            }

            if (await PersistAvailableAsync(events, outbox).ConfigureAwait(false))
            {
                await outbox.EnforceLimitsAsync().ConfigureAwait(false);
            }
            await commandTask.ConfigureAwait(false);
        }
        catch (Exception)
        {
            Emit(diagnosticLogger, SensorDiagnostic.OutboxPersistenceFailed);
        }
    }

    private static async Task<bool> PersistAvailableAsync(
        ChannelReader<DiagnosticEnvelope> events,
        SqliteOutbox outbox)
    {
        var persisted = false;
        while (events.TryRead(out var envelope))
        {
            await outbox.TryAddEventAsync(envelope).ConfigureAwait(false);
            persisted = true;
        }

        return persisted;
    }

    private static async Task UploadDueAsync(
        SqliteOutbox outbox,
        TelemetryApiClient client,
        CancellationToken cancellationToken)
    {
        var due = await outbox.GetDueEventsAsync(DateTimeOffset.UtcNow, 50, cancellationToken)
            .ConfigureAwait(false);
        if (due.Count == 0)
        {
            return;
        }

        var result = await client.UploadAsync(due, cancellationToken).ConfigureAwait(false);
        var now = DateTimeOffset.UtcNow;
        if (result.Status == TelemetryUploadStatus.TRANSIENT_FAILURE)
        {
            foreach (var eventId in result.SubmittedEventIds)
            {
                await outbox.MarkRetryAsync(eventId, now, cancellationToken).ConfigureAwait(false);
            }

            return;
        }

        foreach (var eventId in result.AcceptedEventIds.Concat(result.DuplicateEventIds))
        {
            await outbox.MarkSentAsync(eventId, now, cancellationToken).ConfigureAwait(false);
        }

        var discarded = result.Status == TelemetryUploadStatus.PERMANENT_FAILURE
            ? result.SubmittedEventIds
            : result.RejectedEventIds;
        foreach (var eventId in discarded)
        {
            await outbox.DiscardEventAsync(eventId, cancellationToken).ConfigureAwait(false);
        }
    }
}
