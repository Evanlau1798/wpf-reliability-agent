using System.IO;
using System.Text.Json;

namespace Reliability.Sensor;

internal sealed class SourceMapCatalog
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    private readonly IReadOnlyList<SourceMapCatalogEntry> _entries;

    private SourceMapCatalog(IReadOnlyList<SourceMapCatalogEntry> entries)
    {
        _entries = entries;
    }

    public int Count => _entries.Count;

    public IReadOnlyList<SourceMapCatalogEntry> Lookup(
        string? key,
        string? bindingPath,
        string? targetProperty) =>
        _entries.Where(entry => key is not null
            ? string.Equals(entry.Key, key, StringComparison.Ordinal)
            : string.Equals(entry.BindingPath, bindingPath, StringComparison.Ordinal)
                && string.Equals(entry.TargetProperty, targetProperty, StringComparison.Ordinal))
            .ToArray();

    public static SourceMapCatalog Load(string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            return new SourceMapCatalog([]);
        }

        try
        {
            var entries = JsonSerializer.Deserialize<SourceMapCatalogEntry[]>(File.ReadAllText(path), JsonOptions);
            return new SourceMapCatalog((entries ?? [])
                .Where(entry => IsRepoRelativePath(entry.File))
                .ToArray());
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
            return new SourceMapCatalog([]);
        }
    }

    private static bool IsRepoRelativePath(string? path) =>
        !string.IsNullOrWhiteSpace(path)
        && !Path.IsPathRooted(path)
        && !path.Split('/', '\\').Any(segment => segment is "..");
}

internal sealed record SourceMapCatalogEntry(
    string Key,
    string File,
    int Line,
    int Column,
    string? WindowType,
    IReadOnlyList<string> NamedAncestors,
    string ElementType,
    string ElementName,
    string TargetProperty,
    string? BindingPath,
    string? UnsupportedReason,
    string FileSha256,
    string? BuildCommit,
    string? SourceSnippet,
    int SourceSnippetStartLine,
    bool SourceSnippetTruncated);
