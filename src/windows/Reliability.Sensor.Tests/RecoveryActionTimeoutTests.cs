using System.Net;
using System.Text;
using System.Windows.Threading;

namespace Reliability.Sensor.Tests;

public sealed class RecoveryActionTimeoutTests
{
    [Fact]
    public async Task TimedOutMutationReturnsFailedAndPollerContinuesLeasing()
    {
        var databasePath = TestDatabasePath();
        var outbox = await SqliteOutbox.OpenAsync(databasePath);
        using var releaseHandler = new ManualResetEventSlim();
        var ready = new TaskCompletionSource<Dispatcher>(TaskCreationOptions.RunContinuationsAsynchronously);
        var dispatcherThread = new Thread(() =>
        {
            ready.TrySetResult(Dispatcher.CurrentDispatcher);
            Dispatcher.Run();
        })
        {
            IsBackground = true,
        };
        dispatcherThread.SetApartmentState(ApartmentState.STA);
        dispatcherThread.Start();
        var dispatcher = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            await using var sensor = ReliabilitySensor.Start(null);
            sensor.RecoveryActions.Register(
                RecoveryAction.DisableExperimentalPeopleGrid,
                dispatcher,
                _ =>
                {
                    releaseHandler.Wait();
                    return new RecoveryResult(RecoveryStatus.APPLIED, true, false, 0);
                });

            using var cancellation = new CancellationTokenSource();
            using var handler = new MutationThenBlockHandler();
            using var client = new TelemetryApiClient(
                new Uri("https://reliability.example.test"),
                "test-token",
                handler,
                TimeSpan.FromSeconds(25));
            RecoveryResult? recoveryResult = null;
            var poller = ReliabilitySensor.RunCommandPollerAsync(
                client,
                "device-test",
                "session-1",
                (_, _) => Task.CompletedTask,
                cancellation.Token,
                outbox,
                handleMutationCommand: async (command, token) =>
                {
                    recoveryResult = await sensor.ExecuteMutationCommandAsync(command, token);
                });

            await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));

            Assert.NotNull(recoveryResult);
            Assert.Equal(RecoveryStatus.FAILED, recoveryResult.Status);
            Assert.Equal("TIMEOUT", recoveryResult.ErrorCode);

            cancellation.Cancel();
            await poller.WaitAsync(TimeSpan.FromSeconds(1));
        }
        finally
        {
            releaseHandler.Set();
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(dispatcherThread.Join(TimeSpan.FromSeconds(1)));
            await outbox.DisposeAsync();
            DeleteDatabase(databasePath);
        }
    }

    private sealed class MutationThenBlockHandler : HttpMessageHandler
    {
        private int _requests;

        public TaskCompletionSource SecondRequestStarted { get; } = new(
            TaskCreationOptions.RunContinuationsAsynchronously);

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (Interlocked.Increment(ref _requests) == 1)
            {
                var fixture = await File.ReadAllTextAsync(Path.Combine(
                    AppContext.BaseDirectory,
                    "fixtures",
                    "diagnostic-command-valid-mutation.json"), cancellationToken);
                fixture = fixture
                    .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                    .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z")
                    .Replace("\"timeout_ms\": 10000", "\"timeout_ms\": 100");
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(fixture, Encoding.UTF8, "application/json"),
                };
            }

            SecondRequestStarted.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("Lease request unexpectedly completed.");
        }
    }

    private static string TestDatabasePath() => Path.Combine(
        Path.GetTempPath(),
        "wpf-reliability-agent-tests",
        $"mutation-timeout-{Guid.NewGuid():N}.db");

    private static void DeleteDatabase(string path)
    {
        foreach (var suffix in new[] { string.Empty, "-wal", "-shm" })
        {
            File.Delete(path + suffix);
        }
    }
}
