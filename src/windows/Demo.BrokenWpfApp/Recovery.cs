using System.Diagnostics;

namespace Demo.BrokenWpfApp;

public enum RecoveryAction
{
    DisableExperimentalPeopleGrid
}

public enum RecoveryStatus
{
    APPLIED,
    ALREADY_APPLIED,
    REJECTED
}

public sealed record RecoveryResult(
    RecoveryStatus Status,
    bool BeforeState,
    bool AfterState,
    long DurationMilliseconds,
    string? ErrorCode = null);

public delegate RecoveryResult RecoveryActionHandler(bool expectedCurrentState);

public sealed class ExperimentalPeopleGridState
{
    private int _enabled = 1;

    public bool IsEnabled => Volatile.Read(ref _enabled) == 1;

    public void Enable() => Interlocked.Exchange(ref _enabled, 1);

    public RecoveryResult Disable(bool expectedCurrentState)
    {
        var started = Stopwatch.GetTimestamp();
        var beforeState = IsEnabled;

        if (!beforeState)
        {
            return Result(RecoveryStatus.ALREADY_APPLIED, false, false, started);
        }

        if (!expectedCurrentState)
        {
            return Result(
                RecoveryStatus.REJECTED,
                true,
                true,
                started,
                "EXPECTED_STATE_MISMATCH");
        }

        var previous = Interlocked.CompareExchange(ref _enabled, 0, 1);
        return previous == 1
            ? Result(RecoveryStatus.APPLIED, true, false, started)
            : Result(RecoveryStatus.ALREADY_APPLIED, false, false, started);
    }

    private static RecoveryResult Result(
        RecoveryStatus status,
        bool beforeState,
        bool afterState,
        long started,
        string? errorCode = null) =>
        new(
            status,
            beforeState,
            afterState,
            (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
            errorCode);
}

public sealed class RecoveryActionRegistry
{
    private readonly Dictionary<RecoveryAction, RecoveryActionHandler> _handlers = [];

    public void Register(RecoveryAction action, RecoveryActionHandler handler)
    {
        ArgumentNullException.ThrowIfNull(handler);
        _handlers[action] = handler;
    }

    public RecoveryResult Execute(RecoveryAction action, bool expectedCurrentState)
    {
        var started = Stopwatch.GetTimestamp();
        return _handlers.TryGetValue(action, out var handler)
            ? handler(expectedCurrentState)
            : new RecoveryResult(
                RecoveryStatus.REJECTED,
                expectedCurrentState,
                expectedCurrentState,
                (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds,
                "UNREGISTERED_ACTION");
    }
}
