using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class MutationCommandPersistenceTests
{
    [Fact]
    public async Task DuplicateMutationReplaysCompletedResultWithoutReinvokingDelegate()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "mutation-replay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
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
        var handler = new DuplicateMutationHandler();
        var invocationCount = 0;

        try
        {
            await using var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
            {
                ApiBaseUri = new Uri("https://reliability.example.test"),
                DeviceId = "device-test",
                DeviceToken = "test-token",
                ApplicationId = "demo-broken-wpf-app",
                ApplicationVersion = "0.1.0",
                OutboxPath = path,
                TelemetryHandler = handler,
                RelayPollInterval = TimeSpan.FromMilliseconds(10),
            });
            sensor.RecoveryActions.Register(
                RecoveryAction.DisableExperimentalPeopleGrid,
                dispatcher,
                _ =>
                {
                    Interlocked.Increment(ref invocationCount);
                    return new RecoveryResult(RecoveryStatus.APPLIED, true, false, 3);
                });
            handler.AllowCommands();

            var results = await handler.CompletedTwice.Task.WaitAsync(TimeSpan.FromSeconds(2));

            Assert.Equal(1, invocationCount);
            Assert.Equal(2, results.Count);
            Assert.Equal(results[0].ResultHash, results[1].ResultHash);
            Assert.Equal(results[0] with { Result = null }, results[1] with { Result = null });
            Assert.Equal(
                CanonicalJson.Serialize(results[0].Result),
                CanonicalJson.Serialize(results[1].Result));
        }
        finally
        {
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(1)));
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

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

    private sealed class DuplicateMutationHandler : HttpMessageHandler
    {
        private readonly TaskCompletionSource _commandsAllowed = new(
            TaskCreationOptions.RunContinuationsAsynchronously);
        private readonly object _sync = new();
        private readonly List<CommandResult> _results = [];
        private int _leaseCount;

        public TaskCompletionSource<IReadOnlyList<CommandResult>> CompletedTwice { get; } = new(
            TaskCreationOptions.RunContinuationsAsynchronously);

        public void AllowCommands() => _commandsAllowed.TrySetResult();

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (request.RequestUri?.AbsolutePath.EndsWith("commands:lease", StringComparison.Ordinal) is true)
            {
                await _commandsAllowed.Task.WaitAsync(cancellationToken);
                if (Interlocked.Increment(ref _leaseCount) <= 2)
                {
                    var body = await request.Content!.ReadAsStringAsync(cancellationToken);
                    using var lease = JsonDocument.Parse(body);
                    var sessionId = lease.RootElement.GetProperty("app_session_id").GetString()!;
                    var fixture = await File.ReadAllTextAsync(Path.Combine(
                        AppContext.BaseDirectory,
                        "fixtures",
                        "diagnostic-command-valid-mutation.json"), cancellationToken);
                    var activeFixture = fixture
                        .Replace("session-1", sessionId, StringComparison.Ordinal)
                        .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z", StringComparison.Ordinal)
                        .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z", StringComparison.Ordinal);
                    return JsonResponse(activeFixture);
                }

                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            }

            if (request.RequestUri?.AbsolutePath.EndsWith(":complete", StringComparison.Ordinal) is true)
            {
                var json = await request.Content!.ReadAsStringAsync(cancellationToken);
                var result = JsonSerializer.Deserialize(
                    json,
                    ContractJsonContext.Default.CommandResult)!;
                lock (_sync)
                {
                    _results.Add(result);
                    if (_results.Count == 2)
                    {
                        CompletedTwice.TrySetResult(_results.ToArray());
                    }
                }
                return JsonResponse("{\"accepted\":true,\"idempotent\":false}");
            }

            throw new InvalidOperationException($"Unexpected request: {request.RequestUri}");
        }

        private static HttpResponseMessage JsonResponse(string json)
        {
            var content = new ByteArrayContent(Encoding.UTF8.GetBytes(json));
            content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
        }
    }
}
