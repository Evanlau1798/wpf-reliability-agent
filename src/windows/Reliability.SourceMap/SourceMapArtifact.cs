namespace Reliability.SourceMap;

public sealed record SourceMapArtifact(
    IReadOnlyList<SourceMapEntry> Entries,
    string Json,
    string Sha256);
