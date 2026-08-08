namespace Reliability.Sensor;

internal sealed record PostBindingWindowSnapshot(
    DateTimeOffset StartedAtUtc,
    DateTimeOffset CompletedAtUtc,
    long OccurrenceCount,
    double ErrorsPerSecond);

public sealed partial class ReliabilitySensor
{
    internal static readonly TimeSpan PostActionObservationWindow = TimeSpan.FromSeconds(10);

    internal async Task<PostBindingWindowSnapshot> CapturePostBindingWindowAsync(
        CancellationToken cancellationToken,
        TimeSpan? observationWindow = null)
    {
        var aggregator = _bindingAggregator
            ?? throw new InvalidOperationException("Binding diagnostics are not configured.");
        var window = observationWindow ?? PostActionObservationWindow;
        if (window <= TimeSpan.Zero || window > TimeSpan.FromMinutes(1))
        {
            throw new ArgumentOutOfRangeException(nameof(observationWindow));
        }

        var startedAt = DateTimeOffset.UtcNow;
        var beforeCount = aggregator.AcceptedCount;
        await Task.Delay(window, cancellationToken).ConfigureAwait(false);
        var completedAt = DateTimeOffset.UtcNow;
        var occurrenceCount = aggregator.AcceptedCount - beforeCount;
        return new PostBindingWindowSnapshot(
            startedAt,
            completedAt,
            occurrenceCount,
            occurrenceCount / window.TotalSeconds);
    }
}
