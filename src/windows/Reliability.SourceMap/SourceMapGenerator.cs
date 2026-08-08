using System.Xml.Linq;

namespace Reliability.SourceMap;

public static class SourceMapGenerator
{
    private static readonly XNamespace XamlNamespace = "http://schemas.microsoft.com/winfx/2006/xaml";

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

    public static string? GetXClass(XDocument document) =>
        document.Root?.Attribute(XamlNamespace + "Class")?.Value;

    public static string? GetXName(XElement element) =>
        element.Attribute(XamlNamespace + "Name")?.Value;

    public static IReadOnlyList<string> GetNamedAncestorChain(XElement element) =>
        element.Ancestors()
            .Reverse()
            .Select(GetXName)
            .OfType<string>()
            .ToArray();

    public static string? GetTargetProperty(XElement binding)
    {
        var localName = binding.Parent?.Name.LocalName;
        var separator = localName?.LastIndexOf('.') ?? -1;
        return separator >= 0 ? localName![(separator + 1)..] : null;
    }

    public static string? ParseBindingPath(string markup)
    {
        const string prefix = "{Binding ";
        if (!markup.StartsWith(prefix, StringComparison.Ordinal) || !markup.EndsWith('}'))
        {
            return null;
        }

        var path = markup[prefix.Length..^1].Trim();
        return path.Length > 0 && !path.Contains(',') && !path.Contains('=') ? path : null;
    }

    private static bool IsBuildOutput(string root, string path)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .Any(segment => segment is "bin" or "obj");
    }
}
