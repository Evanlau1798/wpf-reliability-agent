using System.Xml.Linq;

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

    public static string NormalizeRepoRelativePath(string repositoryRoot, string path)
    {
        var root = Path.GetFullPath(repositoryRoot);
        var fullPath = Path.GetFullPath(path);
        var relative = Path.GetRelativePath(root, fullPath);
        if (Path.IsPathRooted(relative)
            || relative == ".."
            || relative.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
            || relative.StartsWith($"..{Path.AltDirectorySeparatorChar}", StringComparison.Ordinal))
        {
            throw new ArgumentOutOfRangeException(nameof(path), "Path must stay inside repository root.");
        }

        return relative.Replace(Path.DirectorySeparatorChar, '/');
    }

    public static XDocument LoadXaml(string path) => XDocument.Load(
        path,
        LoadOptions.SetLineInfo | LoadOptions.PreserveWhitespace);

    private static bool IsBuildOutput(string root, string path)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .Any(segment => segment is "bin" or "obj");
    }
}
