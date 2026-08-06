using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Reliability.Sensor;

internal enum TelemetryUploadStatus
{
    SUCCESS,
    TRANSIENT_FAILURE,
    PERMANENT_FAILURE,
}

internal sealed record TelemetryBatchResult(
    TelemetryUploadStatus Status,
    IReadOnlyList<string> SubmittedEventIds,
    IReadOnlyList<string> AcceptedEventIds,
    IReadOnlyList<string> DuplicateEventIds,
    IReadOnlyList<string> RejectedEventIds,
    string? ErrorCode);

internal sealed class TelemetryApiClient : IDisposable
{
    private const int MaxBatchCount = 50;
    private const int MaxBatchBytes = 512 * 1024;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter<Reliability.Contracts.Severity>() },
    };
    private readonly HttpClient _httpClient;

    public TelemetryApiClient(
        Uri baseUri,
        string deviceToken,
        HttpMessageHandler? handler = null,
        TimeSpan? timeout = null)
    {
        if (!baseUri.IsAbsoluteUri || baseUri.Scheme != Uri.UriSchemeHttps)
        {
            throw new ArgumentException("Telemetry base URI must be absolute HTTPS.", nameof(baseUri));
        }

        if (string.IsNullOrWhiteSpace(deviceToken))
        {
            throw new ArgumentException("A device token is required for telemetry upload.", nameof(deviceToken));
        }

        _httpClient = handler is null ? new HttpClient() : new HttpClient(handler, disposeHandler: true);
        _httpClient.BaseAddress = new Uri($"{baseUri.AbsoluteUri.TrimEnd('/')}/");
        _httpClient.Timeout = timeout ?? TimeSpan.FromSeconds(15);
        _httpClient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", deviceToken);
        _httpClient.DefaultRequestHeaders.UserAgent.ParseAdd("WpfReliabilityAgent/0.1.0");
    }

    public async Task<TelemetryBatchResult> UploadAsync(
        IReadOnlyList<OutboxEvent> events,
        CancellationToken cancellationToken = default)
    {
        var selected = SelectBatch(events);
        var submittedIds = selected.Events.Select(item => item.EventId).ToArray();
        if (selected.Events.Count == 0)
        {
            return Failure(TelemetryUploadStatus.PERMANENT_FAILURE, submittedIds, "EVENT_TOO_LARGE");
        }

        try
        {
            using var content = new ByteArrayContent(selected.Body);
            content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
            using var response = await _httpClient.PostAsync(
                "v1/telemetry:batch",
                content,
                cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                var status = IsTransient(response.StatusCode)
                    ? TelemetryUploadStatus.TRANSIENT_FAILURE
                    : TelemetryUploadStatus.PERMANENT_FAILURE;
                return Failure(status, submittedIds, $"HTTP_{(int)response.StatusCode}");
            }

            var body = await response.Content.ReadFromJsonAsync<TelemetryBatchResponse>(
                JsonOptions,
                cancellationToken).ConfigureAwait(false);
            if (body?.AcceptedEventIds is null
                || body.DuplicateEventIds is null
                || body.Rejected is null
                || body.Rejected.Any(item => item is null || string.IsNullOrWhiteSpace(item.EventId)))
            {
                return Failure(TelemetryUploadStatus.PERMANENT_FAILURE, submittedIds, "INVALID_RESPONSE");
            }

            var submitted = submittedIds.ToHashSet(StringComparer.Ordinal);
            return new TelemetryBatchResult(
                TelemetryUploadStatus.SUCCESS,
                submittedIds,
                Filter(body.AcceptedEventIds, submitted),
                Filter(body.DuplicateEventIds, submitted),
                Filter(body.Rejected.Select(item => item!.EventId), submitted),
                null);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return Failure(TelemetryUploadStatus.TRANSIENT_FAILURE, submittedIds, "REQUEST_TIMEOUT");
        }
        catch (HttpRequestException)
        {
            return Failure(TelemetryUploadStatus.TRANSIENT_FAILURE, submittedIds, "NETWORK_UNAVAILABLE");
        }
        catch (JsonException)
        {
            return Failure(TelemetryUploadStatus.PERMANENT_FAILURE, submittedIds, "INVALID_RESPONSE");
        }
    }

    public void Dispose() => _httpClient.Dispose();

    private static (IReadOnlyList<OutboxEvent> Events, byte[] Body) SelectBatch(IReadOnlyList<OutboxEvent> events)
    {
        var selected = new List<OutboxEvent>(Math.Min(events.Count, MaxBatchCount));
        byte[] body = [];
        foreach (var item in events.Take(MaxBatchCount))
        {
            var candidate = selected.Append(item).Select(current => current.Envelope).ToArray();
            var candidateBody = JsonSerializer.SerializeToUtf8Bytes(
                new TelemetryBatchRequest(candidate),
                JsonOptions);
            if (candidateBody.Length > MaxBatchBytes)
            {
                break;
            }

            selected.Add(item);
            body = candidateBody;
        }

        return (selected, body);
    }

    private static bool IsTransient(HttpStatusCode statusCode) =>
        statusCode is HttpStatusCode.RequestTimeout or HttpStatusCode.TooManyRequests
        || (int)statusCode >= 500;

    private static IReadOnlyList<string> Filter(IEnumerable<string> values, HashSet<string> submitted) =>
        values.Where(submitted.Contains).Distinct(StringComparer.Ordinal).ToArray();

    private static TelemetryBatchResult Failure(
        TelemetryUploadStatus status,
        IReadOnlyList<string> submittedIds,
        string errorCode) =>
        new(status, submittedIds, [], [], [], errorCode);

    private sealed record TelemetryBatchRequest(
        [property: JsonPropertyName("events")] IReadOnlyList<Reliability.Contracts.DiagnosticEnvelope> Events);

    private sealed record RejectedEvent(
        [property: JsonPropertyName("event_id")] string EventId,
        [property: JsonPropertyName("code")] string Code);

    private sealed record TelemetryBatchResponse(
        [property: JsonPropertyName("accepted_event_ids")] IReadOnlyList<string>? AcceptedEventIds,
        [property: JsonPropertyName("duplicate_event_ids")] IReadOnlyList<string>? DuplicateEventIds,
        [property: JsonPropertyName("rejected")] IReadOnlyList<RejectedEvent?>? Rejected);
}
