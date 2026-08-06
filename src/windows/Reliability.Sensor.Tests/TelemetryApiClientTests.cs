using System.Net;
using System.Text;
using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class TelemetryApiClientTests
{
    [Fact]
    public async Task UploadUsesSharedConfigurationAndParsesPerEventResults()
    {
        var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(
                "{\"accepted_event_ids\":[\"event-1\"],\"duplicate_event_ids\":[\"event-2\"]," +
                "\"rejected\":[{\"event_id\":\"event-3\",\"code\":\"invalid\"}]}",
                Encoding.UTF8,
                "application/json"),
        });
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test/base/"),
            "device-secret-token",
            handler);

        var result = await client.UploadAsync(
            [OutboxItem("event-1", 1), OutboxItem("event-2", 2), OutboxItem("event-3", 3)]);

        Assert.Equal(TelemetryUploadStatus.SUCCESS, result.Status);
        Assert.Equal(["event-1"], result.AcceptedEventIds);
        Assert.Equal(["event-2"], result.DuplicateEventIds);
        Assert.Equal(["event-3"], result.RejectedEventIds);
        Assert.Equal("Bearer", handler.Request?.Headers.Authorization?.Scheme);
        Assert.Equal("device-secret-token", handler.Request?.Headers.Authorization?.Parameter);
        Assert.Equal("WpfReliabilityAgent/0.1.0", handler.Request?.Headers.UserAgent.ToString());
        Assert.Equal("https://reliability.example.test/base/v1/telemetry:batch", handler.Request?.RequestUri?.ToString());
        Assert.Equal(3, JsonDocument.Parse(handler.Body!).RootElement.GetProperty("events").GetArrayLength());
    }

    [Fact]
    public async Task UploadCapsCountAndShrinksToTheSerializedByteBudget()
    {
        var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{\"accepted_event_ids\":[],\"duplicate_event_ids\":[],\"rejected\":[]}"),
        });
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "token",
            handler);
        var events = Enumerable.Range(1, 60)
            .Select(index => OutboxItem($"event-{index:D2}", index, new string('x', 50_000)))
            .ToArray();

        var result = await client.UploadAsync(events);

        Assert.InRange(result.SubmittedEventIds.Count, 1, 50);
        Assert.True(handler.BodyBytes <= 512 * 1024);
        Assert.True(result.SubmittedEventIds.Count < events.Length);
    }

    [Theory]
    [InlineData(HttpStatusCode.BadRequest, "PERMANENT_FAILURE")]
    [InlineData(HttpStatusCode.Unauthorized, "PERMANENT_FAILURE")]
    [InlineData(HttpStatusCode.RequestTimeout, "TRANSIENT_FAILURE")]
    [InlineData(HttpStatusCode.TooManyRequests, "TRANSIENT_FAILURE")]
    [InlineData(HttpStatusCode.ServiceUnavailable, "TRANSIENT_FAILURE")]
    public async Task StatusCodesAreClassifiedWithoutLeakingTheToken(
        HttpStatusCode statusCode,
        string expected)
    {
        const string token = "top-secret-device-token";
        var handler = new RecordingHandler(_ => new HttpResponseMessage(statusCode));
        using var client = new TelemetryApiClient(new Uri("https://reliability.example.test"), token, handler);

        var result = await client.UploadAsync([OutboxItem("event-1", 1)]);
        var captured = result.ToString();

        Assert.Equal(expected, result.Status.ToString());
        Assert.DoesNotContain(token, captured, StringComparison.Ordinal);
    }

    [Fact]
    public async Task NetworkFailureReturnsFixedTransientErrorWithoutRawExceptionText()
    {
        const string secret = "never-log-this-token";
        var handler = new RecordingHandler(_ => throw new HttpRequestException($"failure {secret}"));
        using var client = new TelemetryApiClient(new Uri("https://reliability.example.test"), secret, handler);

        var result = await client.UploadAsync([OutboxItem("event-1", 1)]);

        Assert.Equal(TelemetryUploadStatus.TRANSIENT_FAILURE, result.Status);
        Assert.Equal("NETWORK_UNAVAILABLE", result.ErrorCode);
        Assert.DoesNotContain(secret, result.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task MissingResponseFieldsFailClosed()
    {
        var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("{}"),
        });
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "token",
            handler);

        var result = await client.UploadAsync([OutboxItem("event-1", 1)]);

        Assert.Equal(TelemetryUploadStatus.PERMANENT_FAILURE, result.Status);
        Assert.Equal("INVALID_RESPONSE", result.ErrorCode);
    }

    [Fact]
    public async Task RequestTimeoutReturnsTransientFailure()
    {
        using var client = new TelemetryApiClient(
            new Uri("https://reliability.example.test"),
            "token",
            new TimeoutHandler(),
            TimeSpan.FromMilliseconds(50));

        var result = await client.UploadAsync([OutboxItem("event-1", 1)]);

        Assert.Equal(TelemetryUploadStatus.TRANSIENT_FAILURE, result.Status);
        Assert.Equal("REQUEST_TIMEOUT", result.ErrorCode);
    }

    private static OutboxEvent OutboxItem(string eventId, int sequence, string? message = null)
    {
        var payload = JsonSerializer.SerializeToElement(new { message = message ?? "bounded" });
        var envelope = new DiagnosticEnvelope(
            "1.0",
            eventId,
            EventType.BindingAggregate,
            Severity.ERROR,
            DateTimeOffset.UtcNow,
            "device-test",
            "demo-broken-wpf-app",
            "0.1.0",
            "session-test",
            sequence,
            JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
            payload,
            "metadata-only-v1",
            CanonicalJson.Hash(payload));
        return new OutboxEvent(eventId, envelope, 0, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null);
    }

    private sealed class RecordingHandler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
    {
        public HttpRequestMessage? Request { get; private set; }

        public string? Body { get; private set; }

        public int BodyBytes { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Request = request;
            var bytes = request.Content is null
                ? []
                : await request.Content.ReadAsByteArrayAsync(cancellationToken);
            BodyBytes = bytes.Length;
            Body = Encoding.UTF8.GetString(bytes);
            return response(request);
        }
    }

    private sealed class TimeoutHandler : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            throw new InvalidOperationException("The timeout cancellation should end the request.");
        }
    }
}
