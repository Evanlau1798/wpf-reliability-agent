using System.Net;
using System.Net.Http.Headers;
using System.Text;

namespace Reliability.Sensor.Tests;

public sealed class MutationCommandSafetyTests
{
    [Fact]
    public async Task StaleSessionMutationLeavesFeatureStateUnchanged()
    {
        var databasePath = TestDatabasePath();
        var outbox = await SqliteOutbox.OpenAsync(databasePath);
        using var cancellation = new CancellationTokenSource();
        using var handler = new SingleMutationThenBlockHandler(json => json
            .Replace(
                "\"target_app_session_id\": \"session-1\"",
                "\"target_app_session_id\": \"session-stale\"",
                StringComparison.Ordinal)
            .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z", StringComparison.Ordinal)
            .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z", StringComparison.Ordinal));
        using var client = CreateClient(handler);
        var featureEnabled = true;

        try
        {
            var poller = ReliabilitySensor.RunCommandPollerAsync(
                client,
                "device-test",
                "session-1",
                (_, _) => Task.CompletedTask,
                cancellation.Token,
                outbox,
                handleMutationCommand: (_, _) =>
                {
                    featureEnabled = false;
                    cancellation.Cancel();
                    return Task.CompletedTask;
                });

            await Task.WhenAny(handler.SecondRequestStarted.Task, poller)
                .WaitAsync(TimeSpan.FromSeconds(1));
            cancellation.Cancel();
            await poller.WaitAsync(TimeSpan.FromSeconds(1));

            Assert.True(featureEnabled);
        }
        finally
        {
            await outbox.DisposeAsync();
            DeleteDatabase(databasePath);
        }
    }

    [Fact]
    public async Task MutationWithoutApprovalLeavesFeatureStateUnchanged()
    {
        var databasePath = TestDatabasePath();
        var outbox = await SqliteOutbox.OpenAsync(databasePath);
        using var cancellation = new CancellationTokenSource();
        using var handler = new SingleMutationThenBlockHandler(json => json
            .Replace("\"approval_id\": \"approval-1\"", "\"approval_id\": null", StringComparison.Ordinal)
            .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z", StringComparison.Ordinal)
            .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z", StringComparison.Ordinal));
        using var client = CreateClient(handler);
        var featureEnabled = true;

        try
        {
            var poller = ReliabilitySensor.RunCommandPollerAsync(
                client,
                "device-test",
                "session-1",
                (_, _) => Task.CompletedTask,
                cancellation.Token,
                outbox,
                handleMutationCommand: (_, _) =>
                {
                    featureEnabled = false;
                    cancellation.Cancel();
                    return Task.CompletedTask;
                });

            await Task.WhenAny(handler.SecondRequestStarted.Task, poller)
                .WaitAsync(TimeSpan.FromSeconds(1));
            cancellation.Cancel();
            await poller.WaitAsync(TimeSpan.FromSeconds(1));

            Assert.True(featureEnabled);
        }
        finally
        {
            await outbox.DisposeAsync();
            DeleteDatabase(databasePath);
        }
    }

    private sealed class SingleMutationThenBlockHandler(Func<string, string> mutate) : HttpMessageHandler
    {
        private int _requestCount;

        public TaskCompletionSource SecondRequestStarted { get; } = new(
            TaskCreationOptions.RunContinuationsAsynchronously);

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (Interlocked.Increment(ref _requestCount) == 1)
            {
                var fixture = await File.ReadAllTextAsync(Path.Combine(
                    AppContext.BaseDirectory,
                    "fixtures",
                    "diagnostic-command-valid-mutation.json"), cancellationToken);
                var content = new ByteArrayContent(Encoding.UTF8.GetBytes(mutate(fixture)));
                content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
            }

            SecondRequestStarted.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("Lease request unexpectedly completed.");
        }
    }

    private static TelemetryApiClient CreateClient(HttpMessageHandler handler) => new(
        new Uri("https://reliability.example.test"),
        "test-token",
        handler,
        TimeSpan.FromSeconds(25));

    private static string TestDatabasePath() => Path.Combine(
        Path.GetTempPath(),
        "wpf-reliability-agent-tests",
        $"mutation-safety-{Guid.NewGuid():N}.db");

    private static void DeleteDatabase(string path)
    {
        foreach (var suffix in new[] { string.Empty, "-wal", "-shm" })
        {
            File.Delete(path + suffix);
        }
    }
}
