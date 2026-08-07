using System.Net.Http;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed partial class ReliabilitySensor
{
    private async Task RunReadOnlyCommandLoopAsync(
        TelemetryApiClient client,
        string outboxPath,
        CancellationToken cancellationToken)
    {
        await using var commandJournal = await SqliteOutbox.OpenAsync(
            outboxPath,
            cancellationToken: cancellationToken).ConfigureAwait(false);
        await RunCommandPollerAsync(
            client,
            _deviceId,
            AppSessionId,
            (command, token) => ExecuteReadOnlyCommandAsync(
                client,
                commandJournal,
                command,
                token),
            cancellationToken,
            commandJournal,
            (_, completed, token) => ReplayCompletedCommandAsync(
                client,
                completed,
                token)).ConfigureAwait(false);
    }

    private async Task ExecuteReadOnlyCommandAsync(
        TelemetryApiClient client,
        SqliteOutbox commandJournal,
        DiagnosticCommand command,
        CancellationToken cancellationToken)
    {
        var startedAt = DateTimeOffset.UtcNow;
        if (await commandJournal.BeginCommandAsync(
                command.CommandId,
                command.ArgumentsHash,
                startedAt,
                cancellationToken).ConfigureAwait(false) is not CommandClaimStatus.CLAIMED)
        {
            return;
        }

        CommandResult result;
        try
        {
            var payload = await new ReadOnlyCommandExecutor(this)
                .ExecuteAsync(command, cancellationToken)
                .ConfigureAwait(false);
            result = CreateCommandResult(command, payload, startedAt, DateTimeOffset.UtcNow);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception)
        {
            result = CreateFailedCommandResult(command, startedAt, DateTimeOffset.UtcNow);
        }

        var resultJson = JsonSerializer.Serialize(
            result,
            ContractJsonContext.Default.CommandResult);
        await commandJournal.CompleteCommandAsync(
            command.CommandId,
            command.ArgumentsHash,
            resultJson,
            result.ResultHash,
            result.CompletedAtUtc,
            cancellationToken).ConfigureAwait(false);
        await client.CompleteCommandAsync(result, cancellationToken).ConfigureAwait(false);
    }

    private static async Task ReplayCompletedCommandAsync(
        TelemetryApiClient client,
        CompletedCommand completed,
        CancellationToken cancellationToken)
    {
        var result = JsonSerializer.Deserialize(
            completed.ResultJson,
            ContractJsonContext.Default.CommandResult)
            ?? throw new JsonException("Completed command result is empty.");
        if (!string.Equals(result.ResultHash, completed.ResultHash, StringComparison.Ordinal)
            || !string.Equals(ComputeCommandResultHash(result), result.ResultHash, StringComparison.Ordinal))
        {
            throw new JsonException("Completed command result hash is invalid.");
        }
        await client.CompleteCommandAsync(result, cancellationToken).ConfigureAwait(false);
    }

    private static CommandResult CreateCommandResult(
        DiagnosticCommand command,
        JsonElement payload,
        DateTimeOffset startedAt,
        DateTimeOffset completedAt)
    {
        var failed = payload.ValueKind is JsonValueKind.Object
            && payload.TryGetProperty("succeeded", out var succeeded)
            && succeeded.ValueKind is JsonValueKind.False;
        var error = failed ? ReadCommandError(payload) : null;
        var result = new CommandResult(
            "1.0",
            command.CommandId,
            command.IncidentId,
            command.TargetAppSessionId,
            failed ? ResultStatus.FAILED : ResultStatus.SUCCEEDED,
            startedAt,
            completedAt,
            payload.Clone(),
            new string('0', 64),
            payload.ValueKind is JsonValueKind.Object
                && payload.TryGetProperty("truncated", out var truncated)
                && truncated.ValueKind is JsonValueKind.True,
            error);
        return result with { ResultHash = ComputeCommandResultHash(result) };
    }

    private static CommandResult CreateFailedCommandResult(
        DiagnosticCommand command,
        DateTimeOffset startedAt,
        DateTimeOffset completedAt)
    {
        var result = new CommandResult(
            "1.0",
            command.CommandId,
            command.IncidentId,
            command.TargetAppSessionId,
            ResultStatus.FAILED,
            startedAt,
            completedAt,
            null,
            new string('0', 64),
            false,
            new CommandError("EXECUTION_FAILED", null));
        return result with { ResultHash = ComputeCommandResultHash(result) };
    }

    internal static string ComputeCommandResultHash(CommandResult result)
    {
        var element = JsonSerializer.SerializeToElement(
            result,
            ContractJsonContext.Default.CommandResult);
        var fields = element.EnumerateObject()
            .Where(property => !string.Equals(
                property.Name,
                "result_hash",
                StringComparison.Ordinal))
            .ToDictionary(
                property => property.Name,
                property => property.Value.Clone(),
                StringComparer.Ordinal);
        return CanonicalJson.Hash(fields);
    }

    private static CommandError ReadCommandError(JsonElement payload)
    {
        if (payload.TryGetProperty("error", out var error)
            && error.ValueKind is JsonValueKind.Object
            && error.TryGetProperty("code", out var code)
            && code.ValueKind is JsonValueKind.String
            && !string.IsNullOrWhiteSpace(code.GetString()))
        {
            return new CommandError(code.GetString()!, null);
        }
        return new CommandError("COMMAND_FAILED", null);
    }

    internal static async Task RunCommandPollerAsync(
        TelemetryApiClient client,
        string deviceId,
        string appSessionId,
        Func<DiagnosticCommand, CancellationToken, Task> handleReadOnlyCommand,
        CancellationToken cancellationToken,
        SqliteOutbox? commandJournal = null,
        Func<DiagnosticCommand, CompletedCommand, CancellationToken, Task>? replayCompletedCommand = null,
        Func<DiagnosticCommand, CancellationToken, Task>? handleMutationCommand = null)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                var command = await client.LeaseCommandAsync(
                    deviceId,
                    appSessionId,
                    cancellationToken).ConfigureAwait(false);
                if (command is not null
                    && string.Equals(
                        command.TargetAppSessionId,
                        appSessionId,
                        StringComparison.Ordinal)
                    && command.ExpiresAtUtc > DateTimeOffset.UtcNow
                    && string.Equals(
                        CanonicalJson.Hash(command.Arguments),
                        command.ArgumentsHash,
                        StringComparison.Ordinal))
                {
                    var isReadOnly = IsReadOnlyTool(command.Tool);
                    var isMutation = IsMutationTool(command.Tool);
                    if (!isReadOnly && (!isMutation || handleMutationCommand is null))
                    {
                        continue;
                    }
                    if (isMutation
                        && (!MutationCommandVerifier.HasRequiredRisk(command)
                            || !MutationCommandVerifier.HasApprovalReference(command)
                            || !MutationCommandVerifier.HasExactBindingReferences(command)))
                    {
                        continue;
                    }
                    if (commandJournal is not null)
                    {
                        var completed = await commandJournal.LoadCompletedCommandAsync(
                            command.CommandId,
                            command.ArgumentsHash,
                            cancellationToken).ConfigureAwait(false);
                        if (completed is not null)
                        {
                            if (replayCompletedCommand is not null)
                            {
                                await replayCompletedCommand(command, completed, cancellationToken)
                                    .ConfigureAwait(false);
                            }
                            continue;
                        }
                    }

                    if (isMutation)
                    {
                        if (commandJournal is null
                            || await commandJournal.BeginCommandAsync(
                                command.CommandId,
                                command.ArgumentsHash,
                                DateTimeOffset.UtcNow,
                                cancellationToken).ConfigureAwait(false) is not CommandClaimStatus.CLAIMED)
                        {
                            continue;
                        }
                        await handleMutationCommand!(command, cancellationToken).ConfigureAwait(false);
                    }
                    else
                    {
                        await handleReadOnlyCommand(command, cancellationToken).ConfigureAwait(false);
                    }
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception) when (exception is HttpRequestException or JsonException)
            {
                await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken).ConfigureAwait(false);
            }
        }
    }

    private static bool IsReadOnlyTool(DiagnosticTool tool) => tool is
        DiagnosticTool.HealthGetSnapshot
        or DiagnosticTool.BindingGetErrors
        or DiagnosticTool.BindingGetLiveCandidates
        or DiagnosticTool.ExceptionGetRecent
        or DiagnosticTool.UiGetSubtree
        or DiagnosticTool.UiGetElementDetails
        or DiagnosticTool.PerformanceSample
        or DiagnosticTool.StateCompareSnapshots;

    private static bool IsMutationTool(DiagnosticTool tool) =>
        tool is DiagnosticTool.RecoverySetFeatureFlag;
}
