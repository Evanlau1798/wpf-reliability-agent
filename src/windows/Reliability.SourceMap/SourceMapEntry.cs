namespace Reliability.SourceMap;

public sealed record SourceMapEntry(
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
    string SourceSnippet,
    int SourceSnippetStartLine,
    bool SourceSnippetTruncated);
