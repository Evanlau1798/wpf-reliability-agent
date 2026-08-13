using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class CommandRoundTripTests
{
    [Fact]
    public void CommandResultTimestampsSerializeWithSixFractionalDigits()
    {
        var result = new CommandResult(
            "1.0",
            "command-1",
            "incident-1",
            "session-1",
            ResultStatus.SUCCEEDED,
            new DateTimeOffset(2026, 8, 13, 1, 0, 28, 298, TimeSpan.Zero).AddTicks(3_000),
            new DateTimeOffset(2026, 8, 13, 1, 0, 28, 315, TimeSpan.Zero).AddTicks(7_770),
            JsonSerializer.SerializeToElement(new { ok = true }),
            new string('0', 64),
            false,
            null);

        var json = JsonSerializer.Serialize(result, ContractJsonContext.Default.CommandResult);

        Assert.Contains("\"started_at_utc\":\"2026-08-13T01:00:28.298300Z\"", json);
        Assert.Contains("\"completed_at_utc\":\"2026-08-13T01:00:28.315777Z\"", json);
    }

    [Fact]
    public void CommandResultHashIgnoresSubMicrosecondTimestampTicks()
    {
        var startedAt = new DateTimeOffset(2026, 8, 10, 17, 7, 43, TimeSpan.Zero)
            .AddTicks(8_629_410);
        var completedAt = new DateTimeOffset(2026, 8, 10, 17, 7, 43, TimeSpan.Zero)
            .AddTicks(8_701_840);
        var baseline = new CommandResult(
            "1.0",
            "command-1",
            "incident-1",
            "session-1",
            ResultStatus.SUCCEEDED,
            startedAt,
            completedAt,
            JsonSerializer.SerializeToElement(new { ok = true }),
            new string('0', 64),
            false,
            null);
        var subMicrosecond = baseline with
        {
            StartedAtUtc = startedAt.AddTicks(1),
            CompletedAtUtc = completedAt.AddTicks(6),
        };

        Assert.Equal(
            ReliabilitySensor.ComputeCommandResultHash(baseline),
            ReliabilitySensor.ComputeCommandResultHash(subMicrosecond));
    }

    [Fact]
    public async Task StartedSensorLeasesExecutesAndCompletesReadOnlyCommand()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "command-round-trip-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new RoundTripHandler();

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

            var result = await handler.Completed.Task.WaitAsync(TimeSpan.FromSeconds(2));

            Assert.Equal("command-round-trip-1", result.CommandId);
            Assert.Equal(sensor.AppSessionId, result.AppSessionId);
            Assert.Equal(ResultStatus.SUCCEEDED, result.Status);
            Assert.Equal(64, result.ResultHash.Length);
            Assert.Equal(1, handler.CommandDeliveryCount);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private sealed class RoundTripHandler : HttpMessageHandler
    {
        private int _leaseRequestCount;
        private int _commandDeliveryCount;

        public int CommandDeliveryCount => Volatile.Read(ref _commandDeliveryCount);

        public TaskCompletionSource<CommandResult> Completed { get; } = new(
            TaskCreationOptions.RunContinuationsAsynchronously);

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            if (request.RequestUri?.AbsolutePath.EndsWith("commands:lease", StringComparison.Ordinal) is true)
            {
                if (Interlocked.Increment(ref _leaseRequestCount) == 1)
                {
                    Interlocked.Increment(ref _commandDeliveryCount);
                    var body = await request.Content!.ReadAsStringAsync(cancellationToken);
                    using var lease = JsonDocument.Parse(body);
                    var sessionId = lease.RootElement.GetProperty("app_session_id").GetString()!;
                    var arguments = JsonSerializer.SerializeToElement(new { });
                    var now = DateTimeOffset.UtcNow;
                    var command = new DiagnosticCommand(
                        "1.0",
                        "command-round-trip-1",
                        "incident-1",
                        sessionId,
                        DiagnosticTool.HealthGetSnapshot,
                        arguments,
                        CanonicalJson.Hash(arguments),
                        RiskLevel.LOW,
                        null,
                        "incident-1:1:health.get_snapshot:round-trip",
                        now,
                        now.AddMinutes(1),
                        5_000);
                    var json = JsonSerializer.Serialize(
                        command,
                        ContractJsonContext.Default.DiagnosticCommand);
                    return JsonResponse(json);
                }

                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            }

            if (request.RequestUri?.AbsolutePath.EndsWith(":complete", StringComparison.Ordinal) is true)
            {
                var json = await request.Content!.ReadAsStringAsync(cancellationToken);
                var result = JsonSerializer.Deserialize(
                    json,
                    ContractJsonContext.Default.CommandResult)!;
                Completed.TrySetResult(result);
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
