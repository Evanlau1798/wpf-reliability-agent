using System.Text.Json;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class MutationCommandPersistenceTests
{
    [Fact]
    public async Task ClaimedMutationPersistsDurableResultAndHash()
    {
        var databasePath = TestDatabasePath();
        var outbox = await SqliteOutbox.OpenAsync(databasePath);
        var ready = new TaskCompletionSource<Dispatcher>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            ready.TrySetResult(Dispatcher.CurrentDispatcher);
            Dispatcher.Run();
        })
        {
            IsBackground = true,
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var dispatcher = await ready.Task.WaitAsync(TimeSpan.FromSeconds(1));

        try
        {
            await using var sensor = ReliabilitySensor.Start(null);
            sensor.RecoveryActions.Register(
                RecoveryAction.DisableExperimentalPeopleGrid,
                dispatcher,
                _ => new RecoveryResult(RecoveryStatus.APPLIED, true, false, 3));
            var command = await ReadMutationCommandAsync();
            Assert.Equal(
                CommandClaimStatus.CLAIMED,
                await outbox.BeginCommandAsync(command.CommandId, command.ArgumentsHash, DateTimeOffset.UtcNow));

            var result = await sensor.ExecuteClaimedMutationCommandAsync(
                outbox,
                command,
                CancellationToken.None);
            var completed = await outbox.LoadCompletedCommandAsync(command.CommandId, command.ArgumentsHash);

            Assert.NotNull(completed);
            Assert.Equal(result.ResultHash, completed.ResultHash);
            Assert.Equal(ReliabilitySensor.ComputeCommandResultHash(result), completed.ResultHash);
            var stored = JsonSerializer.Deserialize(
                completed.ResultJson,
                ContractJsonContext.Default.CommandResult);
            Assert.NotNull(stored);
            Assert.Equal(result with { Result = null }, stored with { Result = null });
            Assert.Equal(
                CanonicalJson.Serialize(result.Result),
                CanonicalJson.Serialize(stored.Result));
        }
        finally
        {
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(1)));
            await outbox.DisposeAsync();
            DeleteDatabase(databasePath);
        }
    }

    private static async Task<DiagnosticCommand> ReadMutationCommandAsync()
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "diagnostic-command-valid-mutation.json"));
        return JsonSerializer.Deserialize(json, ContractJsonContext.Default.DiagnosticCommand)!;
    }

    private static string TestDatabasePath() => Path.Combine(
        Path.GetTempPath(),
        "wpf-reliability-agent-tests",
        $"mutation-persistence-{Guid.NewGuid():N}.db");

    private static void DeleteDatabase(string path)
    {
        foreach (var suffix in new[] { string.Empty, "-wal", "-shm" })
        {
            File.Delete(path + suffix);
        }
    }
}
