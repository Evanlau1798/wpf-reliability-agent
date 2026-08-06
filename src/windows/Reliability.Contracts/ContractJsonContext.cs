using System.Text.Json.Serialization;

namespace Reliability.Contracts;

[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    GenerationMode = JsonSourceGenerationMode.Metadata,
    UseStringEnumConverter = true,
    WriteIndented = false)]
[JsonSerializable(typeof(DiagnosticEnvelope))]
[JsonSerializable(typeof(DiagnosticCommand))]
[JsonSerializable(typeof(CommandResult))]
[JsonSerializable(typeof(AgentDecision))]
[JsonSerializable(typeof(ApprovalRecord))]
[JsonSerializable(typeof(IncidentReport))]
public partial class ContractJsonContext : JsonSerializerContext;
