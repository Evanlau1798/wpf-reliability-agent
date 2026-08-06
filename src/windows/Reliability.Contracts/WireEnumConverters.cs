using System.Text.Json;
using System.Text.Json.Serialization;

namespace Reliability.Contracts;

public sealed class EventTypeJsonConverter : JsonConverter<EventType>
{
    private static readonly IReadOnlyDictionary<string, EventType> FromWire =
        new Dictionary<string, EventType>(StringComparer.Ordinal)
        {
            ["binding.aggregate"] = EventType.BindingAggregate,
            ["exception.summary"] = EventType.ExceptionSummary,
            ["ui.snapshot"] = EventType.UiSnapshot,
            ["performance.sample"] = EventType.PerformanceSample,
            ["tool.result"] = EventType.ToolResult,
            ["recovery.result"] = EventType.RecoveryResult,
        };

    private static readonly IReadOnlyDictionary<EventType, string> ToWire =
        FromWire.ToDictionary(pair => pair.Value, pair => pair.Key);

    public override EventType Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        var value = reader.GetString();
        return value is not null && FromWire.TryGetValue(value, out var result)
            ? result
            : throw new JsonException($"Unknown event type: {value}");
    }

    public override void Write(Utf8JsonWriter writer, EventType value, JsonSerializerOptions options) =>
        writer.WriteStringValue(ToWire[value]);
}

public sealed class DiagnosticToolJsonConverter : JsonConverter<DiagnosticTool>
{
    private static readonly IReadOnlyDictionary<string, DiagnosticTool> FromWire =
        new Dictionary<string, DiagnosticTool>(StringComparer.Ordinal)
        {
            ["health.get_snapshot"] = DiagnosticTool.HealthGetSnapshot,
            ["binding.get_errors"] = DiagnosticTool.BindingGetErrors,
            ["binding.get_live_candidates"] = DiagnosticTool.BindingGetLiveCandidates,
            ["exception.get_recent"] = DiagnosticTool.ExceptionGetRecent,
            ["ui.get_subtree"] = DiagnosticTool.UiGetSubtree,
            ["ui.get_element_details"] = DiagnosticTool.UiGetElementDetails,
            ["performance.sample"] = DiagnosticTool.PerformanceSample,
            ["state.compare_snapshots"] = DiagnosticTool.StateCompareSnapshots,
            ["recovery.set_feature_flag"] = DiagnosticTool.RecoverySetFeatureFlag,
        };

    private static readonly IReadOnlyDictionary<DiagnosticTool, string> ToWire =
        FromWire.ToDictionary(pair => pair.Value, pair => pair.Key);

    public override DiagnosticTool Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        var value = reader.GetString();
        return value is not null && FromWire.TryGetValue(value, out var result)
            ? result
            : throw new JsonException($"Unknown diagnostic tool: {value}");
    }

    public override void Write(Utf8JsonWriter writer, DiagnosticTool value, JsonSerializerOptions options) =>
        writer.WriteStringValue(ToWire[value]);
}
