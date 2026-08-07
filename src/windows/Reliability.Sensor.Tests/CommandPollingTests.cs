using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;

namespace Reliability.Sensor.Tests;

public sealed class CommandPollingTests
{
    [Fact]
    public async Task PollLoopRequestsOneCommandWithTwentySecondWait()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new LeaseHandler();
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var handled = 0;

        await ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (command, _) =>
            {
                Assert.Equal("command-read-1", command.CommandId);
                handled++;
                cancellation.Cancel();
                return Task.CompletedTask;
            },
            cancellation.Token);

        Assert.Equal(1, handled);
        Assert.Equal("/v1/devices/device-test/commands:lease", handler.RequestPath);
        using var request = JsonDocument.Parse(handler.Body!);
        Assert.Equal("session-1", request.RootElement.GetProperty("app_session_id").GetString());
        Assert.Equal(20, request.RootElement.GetProperty("wait_seconds").GetInt32());
        Assert.Equal(1, request.RootElement.GetProperty("max_commands").GetInt32());
    }

    [Fact]
    public async Task PollLoopCancellationAbortsInflightLeaseRequest()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new BlockingLeaseHandler();
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "test-token",
            handler,
            TimeSpan.FromSeconds(25));
        var poller = ReliabilitySensor.RunCommandPollerAsync(
            client,
            "device-test",
            "session-1",
            (_, _) => Task.CompletedTask,
            cancellation.Token);

        await handler.Started.Task.WaitAsync(TimeSpan.FromSeconds(1));
        cancellation.Cancel();
        await poller.WaitAsync(TimeSpan.FromSeconds(1));

        Assert.True(handler.WasCancelled);
    }

    [Fact]
    public async Task InvalidCommandSchemaIsRejectedBeforeDispatch()
    {
        using var cancellation = new CancellationTokenSource();
        var handler = new InvalidThenBlockingLeaseHandler();
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

    private sealed class LeaseHandler : HttpMessageHandler
    {
        public string? RequestPath { get; private set; }

        public string? Body { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            RequestPath = request.RequestUri?.AbsolutePath;
            Body = await request.Content!.ReadAsStringAsync(cancellationToken);
            var fixture = await File.ReadAllTextAsync(
                System.IO.Path.Combine(
                    AppContext.BaseDirectory,
                    "fixtures",
                    "diagnostic-command-valid-read.json"),
                cancellationToken);
            var content = new ByteArrayContent(Encoding.UTF8.GetBytes(fixture));
            content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = content,
            };
        }
    }

    private sealed class BlockingLeaseHandler : HttpMessageHandler
    {
        public TaskCompletionSource Started { get; } = new(
            TaskCreationOptions.RunContinuationsAsynchronously);

        public bool WasCancelled { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Started.TrySetResult();
            try
            {
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
                throw new InvalidOperationException("Lease request unexpectedly completed.");
            }
            catch (OperationCanceledException)
            {
                WasCancelled = true;
                throw;
            }
        }
    }

    private sealed class InvalidThenBlockingLeaseHandler : HttpMessageHandler
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
                    System.IO.Path.Combine(
                        AppContext.BaseDirectory,
                        "fixtures",
                        "diagnostic-command-valid-read.json"),
                    cancellationToken);
                var invalid = fixture.Replace(
                    "\"schema_version\": \"1.0\"",
                    "\"schema_version\": \"2.0\"",
                    StringComparison.Ordinal);
                var content = new ByteArrayContent(Encoding.UTF8.GetBytes(invalid));
                content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
            }

            SecondRequestStarted.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("Lease request unexpectedly completed.");
        }
    }
}
