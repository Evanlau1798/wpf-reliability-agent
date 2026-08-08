using System.Text.Json;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class RecoveryActionDispatchTests
{
    [Fact]
    public async Task MutationExecutionUsesRegisteredUiDispatcher()
    {
        var ready = new TaskCompletionSource<(Dispatcher Dispatcher, int ThreadId)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var dispatcher = Dispatcher.CurrentDispatcher;
            ready.TrySetResult((dispatcher, Environment.CurrentManagedThreadId));
            Dispatcher.Run();
        })
        {
            IsBackground = true,
        };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var (dispatcher, dispatcherThreadId) = await ready.Task.WaitAsync(TimeSpan.FromSeconds(1));

        try
        {
            await using var sensor = ReliabilitySensor.Start(null);
            var handlerThreadId = 0;
            sensor.RecoveryActions.Register(
                RecoveryAction.DisableExperimentalPeopleGrid,
                dispatcher,
                expectedCurrentState =>
                {
                    Assert.True(expectedCurrentState);
                    handlerThreadId = Environment.CurrentManagedThreadId;
                    return new RecoveryResult(RecoveryStatus.APPLIED, true, false, 0);
                });
            var command = await ReadMutationCommandAsync();

            var result = await Task.Run(() =>
                sensor.ExecuteMutationCommandAsync(command, CancellationToken.None));

            Assert.Equal(RecoveryStatus.APPLIED, result.Status);
            Assert.Equal(dispatcherThreadId, handlerThreadId);
        }
        finally
        {
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(1)));
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
}
