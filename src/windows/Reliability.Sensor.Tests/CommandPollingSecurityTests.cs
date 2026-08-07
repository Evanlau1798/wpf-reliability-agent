using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;

namespace Reliability.Sensor.Tests;

public sealed class CommandPollingSecurityTests
{
    [Fact]
    public async Task StaleTargetSessionIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(json => json
            .Replace("\"target_app_session_id\": \"session-1\"", "\"target_app_session_id\": \"session-stale\"")
            .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
            .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"));
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var handled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) =>
            {
                handled++;
                return Task.CompletedTask;
            },
            cancellation.Token);

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, handled);
    }

    [Fact]
    public async Task StaleMutationTargetSessionIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace("\"target_app_session_id\": \"session-1\"", "\"target_app_session_id\": \"session-stale\"")
                .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var mutationHandled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) => Task.CompletedTask,
            cancellation.Token,
            handleMutationCommand: (_, _) =>
            {
                mutationHandled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            });

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, mutationHandled);
    }

    [Fact]
    public async Task ExpiredCommandIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(json => json
            .Replace("2026-08-07T00:00:00Z", "2000-01-01T00:00:00Z")
            .Replace("2026-08-07T00:01:00Z", "2000-01-01T00:01:00Z"));
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var handled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) =>
            {
                handled++;
                return Task.CompletedTask;
            },
            cancellation.Token);

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, handled);
    }

    [Fact]
    public async Task ExpiredMutationCommandIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace("2026-08-07T00:00:00Z", "2000-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2000-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var mutationHandled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) => Task.CompletedTask,
            cancellation.Token,
            handleMutationCommand: (_, _) =>
            {
                mutationHandled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            });

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, mutationHandled);
    }

    [Fact]
    public async Task UnexpectedMutationFeatureIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace("ExperimentalPeopleGrid", "OtherFeature")
                .Replace(
                    "d229c1dcfe91553a7552c8ed06983a1a0b818d6e7b38931e0aab843b42517a5c",
                    "639862c594404e1431864b50dda6323f6059b80b08ba43a75e546cd0a6374d99")
                .Replace(
                    "cmd-f3e48f346ad520efbb2d3ea3e68607149cce16deb0f517771a27e2ede8c5d253",
                    "cmd-d146a2afbe0d9ec30362454b62aaaf607dad9a2cff00a7628f8d9b848a3941c9")
                .Replace(
                    "a82b1f00a52614637ed8b036c66608a7c2693282dc68374920bd6bb768074400",
                    "804e5cf1dd73c5d1e49a7949e27d62cccb4a902a80b38d973712ae8d0ba8b21a")
                .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var mutationHandled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) => Task.CompletedTask,
            cancellation.Token,
            handleMutationCommand: (_, _) =>
            {
                mutationHandled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            });

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, mutationHandled);
    }

    [Fact]
    public async Task MutationToolIsRejectedByReadOnlyAllowlist()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var handled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) =>
            {
                handled++;
                return Task.CompletedTask;
            },
            cancellation.Token);

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, handled);
    }

    [Fact]
    public async Task MutationToolUsesSeparateDispatchPath()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var readOnlyHandled = 0;
        var mutationHandled = 0;

        await ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) =>
            {
                readOnlyHandled++;
                return Task.CompletedTask;
            },
            cancellation.Token,
            handleMutationCommand: (_, _) =>
            {
                mutationHandled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            });

        Assert.Equal(0, readOnlyHandled);
        Assert.Equal(1, mutationHandled);
    }

    [Theory]
    [InlineData("\"proposal_version\": 1", "\"proposal_version\": 2")]
    [InlineData("\"action_id\": \"action-1\"", "\"action_id\": \"action-substituted\"")]
    public async Task MutationBindingMismatchIsRejectedBeforeDispatch(string original, string replacement)
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(
            json => json
                .Replace(original, replacement)
                .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
                .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"),
            "diagnostic-command-valid-mutation.json");
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var mutationHandled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) => Task.CompletedTask,
            cancellation.Token,
            handleMutationCommand: (_, _) =>
            {
                mutationHandled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            });

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, mutationHandled);
    }

    [Fact]
    public async Task ArgumentsHashMismatchIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new SingleCommandThenBlockHandler(json => json
            .Replace("\"max_depth\": 4", "\"max_depth\": 3")
            .Replace("2026-08-07T00:00:00Z", "2099-01-01T00:00:00Z")
            .Replace("2026-08-07T00:01:00Z", "2099-01-01T00:01:00Z"));
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var handled = 0;
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) =>
            {
                handled++;
                return Task.CompletedTask;
            },
            cancellation.Token);

        await handler.SecondRequestStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.Equal(0, handled);
    }

    private sealed class SingleCommandThenBlockHandler(
        Func<string, string> mutate,
        string fixtureName = "diagnostic-command-valid-read.json") : HttpMessageHandler
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
                var fixture = await File.ReadAllTextAsync(
                    Path.Combine(
                        AppContext.BaseDirectory,
                        "fixtures",
                        fixtureName),
                    cancellationToken);
                var content = new ByteArrayContent(Encoding.UTF8.GetBytes(mutate(fixture)));
                content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
            }

            SecondRequestStarted.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("Lease request unexpectedly completed.");
        }
    }
}
