using System.Threading.Channels;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed partial class ReliabilitySensor
{
    private static async Task PersistEventsAsync(
        ChannelReader<DiagnosticEnvelope> events,
        string outboxPath,
        CancellationToken cancellationToken,
        Action<SensorDiagnostic>? diagnosticLogger)
    {
        try
        {
            await using var outbox = await SqliteOutbox.OpenAsync(outboxPath).ConfigureAwait(false);
            try
            {
                await foreach (var envelope in events.ReadAllAsync(cancellationToken).ConfigureAwait(false))
                {
                    await outbox.TryAddEventAsync(envelope).ConfigureAwait(false);
                    await outbox.EnforceLimitsAsync().ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                while (events.TryRead(out var envelope))
                {
                    await outbox.TryAddEventAsync(envelope).ConfigureAwait(false);
                    await outbox.EnforceLimitsAsync().ConfigureAwait(false);
                }
            }
        }
        catch (Exception)
        {
            Emit(diagnosticLogger, SensorDiagnostic.OutboxPersistenceFailed);
        }
    }
}
