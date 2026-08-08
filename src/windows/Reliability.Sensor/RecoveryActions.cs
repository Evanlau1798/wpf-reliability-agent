using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor;

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

public sealed class RecoveryActionRegistrations
{
    private readonly object _sync = new();
    private readonly Dictionary<RecoveryAction, (Dispatcher Dispatcher, RecoveryActionHandler Handler)> _handlers = [];

    public void Register(
        RecoveryAction action,
        Dispatcher dispatcher,
        RecoveryActionHandler handler)
    {
        ArgumentNullException.ThrowIfNull(dispatcher);
        ArgumentNullException.ThrowIfNull(handler);
        lock (_sync)
        {
            _handlers[action] = (dispatcher, handler);
        }
    }

    internal async Task<RecoveryResult> ExecuteAsync(
        RecoveryAction action,
        bool expectedCurrentState,
        CancellationToken cancellationToken)
    {
        (Dispatcher Dispatcher, RecoveryActionHandler Handler) registration;
        lock (_sync)
        {
            if (!_handlers.TryGetValue(action, out registration))
            {
                return new RecoveryResult(
                    RecoveryStatus.REJECTED,
                    expectedCurrentState,
                    expectedCurrentState,
                    0,
                    "UNREGISTERED_ACTION");
            }
        }

        RecoveryResult Invoke() => registration.Handler(expectedCurrentState);
        return registration.Dispatcher.CheckAccess()
            ? Invoke()
            : await registration.Dispatcher
                .InvokeAsync(Invoke, DispatcherPriority.Normal, cancellationToken)
                .Task.WaitAsync(cancellationToken)
                .ConfigureAwait(false);
    }
}

public sealed partial class ReliabilitySensor
{
    internal Task<RecoveryResult> ExecuteMutationCommandAsync(
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(command);
        if (command.Tool is not DiagnosticTool.RecoverySetFeatureFlag
            || command.Arguments.ValueKind is not System.Text.Json.JsonValueKind.Object
            || !command.Arguments.TryGetProperty("feature", out var feature)
            || feature.GetString() is not "ExperimentalPeopleGrid"
            || !command.Arguments.TryGetProperty("enabled", out var enabled)
            || enabled.ValueKind is not System.Text.Json.JsonValueKind.False
            || !command.Arguments.TryGetProperty("expected_current_value", out var expectedCurrentValue)
            || expectedCurrentValue.ValueKind is not System.Text.Json.JsonValueKind.True)
        {
            return Task.FromResult(new RecoveryResult(
                RecoveryStatus.REJECTED,
                false,
                false,
                0,
                "UNSUPPORTED_ACTION"));
        }

        return RecoveryActions.ExecuteAsync(
            RecoveryAction.DisableExperimentalPeopleGrid,
            expectedCurrentState: true,
            cancellationToken);
    }
}
