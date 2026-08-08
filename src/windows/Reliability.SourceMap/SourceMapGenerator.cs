namespace Reliability.SourceMap;

public static class SourceMapGenerator
{
    public static IReadOnlyList<string> DiscoverXamlFiles(string projectRoot)
    {
        var root = Path.GetFullPath(projectRoot);
        return Directory.EnumerateFiles(root, "*.xaml", SearchOption.AllDirectories)
            .Where(path => !IsBuildOutput(root, path))
            .Order(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    private static bool IsBuildOutput(string root, string path)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .Any(segment => segment is "bin" or "obj");
    }
}
