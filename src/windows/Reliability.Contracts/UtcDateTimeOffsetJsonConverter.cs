using System.Text.Json;
using System.Text.Json.Serialization;

namespace Reliability.Contracts;

public sealed class UtcDateTimeOffsetJsonConverter : JsonConverter<DateTimeOffset>
{
    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
        reader.TryGetDateTimeOffset(out var value)
            ? value
            : throw new JsonException("Expected an ISO-8601 timestamp.");

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) =>
        writer.WriteStringValue(value.UtcDateTime);
}

public sealed class NullableUtcDateTimeOffsetJsonConverter : JsonConverter<DateTimeOffset?>
{
    public override DateTimeOffset? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) =>
        reader.TokenType is JsonTokenType.Null
            ? null
            : reader.TryGetDateTimeOffset(out var value)
                ? value
                : throw new JsonException("Expected an ISO-8601 timestamp or null.");

    public override void Write(Utf8JsonWriter writer, DateTimeOffset? value, JsonSerializerOptions options)
    {
        if (value is null)
        {
            writer.WriteNullValue();
            return;
        }

        writer.WriteStringValue(value.Value.UtcDateTime);
    }
}
