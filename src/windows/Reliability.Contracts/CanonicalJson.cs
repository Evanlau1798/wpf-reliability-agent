using System.Buffers;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Reliability.Contracts;

public static class CanonicalJson
{
    public static string Serialize<T>(T value)
    {
        JsonElement element;
        try
        {
            element = value is JsonElement jsonElement
                ? jsonElement
                : JsonSerializer.SerializeToElement(value);
        }
        catch (Exception exception) when (exception is JsonException or NotSupportedException or ArgumentException)
        {
            throw new ArgumentException("Value cannot be represented as finite JSON.", nameof(value), exception);
        }

        var builder = new StringBuilder();
        WriteElement(builder, element);
        return builder.ToString();
    }

    public static string Hash<T>(T value) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Serialize(value)))).ToLowerInvariant();

    private static void WriteElement(StringBuilder builder, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                builder.Append('{');
                var firstProperty = true;
                foreach (var property in element.EnumerateObject().OrderBy(item => item.Name, StringComparer.Ordinal))
                {
                    if (!firstProperty)
                    {
                        builder.Append(',');
                    }
                    firstProperty = false;
                    WriteString(builder, property.Name);
                    builder.Append(':');
                    WriteElement(builder, property.Value);
                }
                builder.Append('}');
                break;
            case JsonValueKind.Array:
                builder.Append('[');
                var firstItem = true;
                foreach (var item in element.EnumerateArray())
                {
                    if (!firstItem)
                    {
                        builder.Append(',');
                    }
                    firstItem = false;
                    WriteElement(builder, item);
                }
                builder.Append(']');
                break;
            case JsonValueKind.String:
                WriteString(builder, element.GetString() ?? string.Empty);
                break;
            case JsonValueKind.Number:
                builder.Append(element.GetRawText());
                break;
            case JsonValueKind.True:
                builder.Append("true");
                break;
            case JsonValueKind.False:
                builder.Append("false");
                break;
            case JsonValueKind.Null:
                builder.Append("null");
                break;
            default:
                throw new ArgumentException($"Unsupported JSON value kind: {element.ValueKind}");
        }
    }

    private static void WriteString(StringBuilder builder, string value)
    {
        builder.Append('"');
        var remaining = value.AsSpan();
        while (!remaining.IsEmpty)
        {
            var status = Rune.DecodeFromUtf16(remaining, out var rune, out var consumed);
            if (status is not OperationStatus.Done)
            {
                throw new ArgumentException("String contains invalid UTF-16 data.", nameof(value));
            }

            remaining = remaining[consumed..];
            switch (rune.Value)
            {
                case '"': builder.Append("\\\""); break;
                case '\\': builder.Append("\\\\"); break;
                case '\b': builder.Append("\\b"); break;
                case '\f': builder.Append("\\f"); break;
                case '\n': builder.Append("\\n"); break;
                case '\r': builder.Append("\\r"); break;
                case '\t': builder.Append("\\t"); break;
                case < 0x20:
                    builder.Append("\\u");
                    builder.Append(rune.Value.ToString("x4", CultureInfo.InvariantCulture));
                    break;
                default:
                    builder.Append(rune.ToString());
                    break;
            }
        }
        builder.Append('"');
    }
}
