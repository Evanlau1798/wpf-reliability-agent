using System.Security.Cryptography;
using System.Xml;
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
        if (path.StartsWith("Path=", StringComparison.Ordinal))
        {
            path = path["Path=".Length..].Trim();
        }

        return path.Length > 0 && !path.Contains(',') && !path.Contains('=') ? path : null;
    }

    public static BindingParseResult ParseBinding(string markup)
    {
        var path = ParseBindingPath(markup);
        return path is null
            ? new BindingParseResult(null, "unsupported_binding_markup")
            : new BindingParseResult(path, null);
    }

    public static SourcePosition? GetSourcePosition(XObject node) =>
        node is IXmlLineInfo lineInfo && lineInfo.HasLineInfo()
            ? new SourcePosition(lineInfo.LineNumber, lineInfo.LinePosition)
            : null;

    public static string ComputeFileSha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    public static string? ReadBuildCommit(string repositoryRoot)
    {
        var gitDirectory = ResolveGitDirectory(Path.Combine(Path.GetFullPath(repositoryRoot), ".git"));
        if (gitDirectory is null)
        {
            return null;
        }

        var headPath = Path.Combine(gitDirectory, "HEAD");
        if (!File.Exists(headPath))
        {
            return null;
        }

        var head = File.ReadAllText(headPath).Trim();
        if (!head.StartsWith("ref: ", StringComparison.Ordinal))
        {
            return NormalizeCommitHash(head);
        }

        var reference = head["ref: ".Length..].Trim();
        var commonDirectory = ResolveCommonGitDirectory(gitDirectory);
        var referencePath = Path.Combine(commonDirectory, reference.Replace('/', Path.DirectorySeparatorChar));
        if (File.Exists(referencePath))
        {
            return NormalizeCommitHash(File.ReadAllText(referencePath));
        }

        var packedRefs = Path.Combine(commonDirectory, "packed-refs");
        if (!File.Exists(packedRefs))
        {
            return null;
        }

        foreach (var line in File.ReadLines(packedRefs))
        {
            var parts = line.Split(' ', 2, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 2 && parts[1] == reference)
            {
                return NormalizeCommitHash(parts[0]);
            }
        }

        return null;
    }

    private static string? ResolveGitDirectory(string gitPath)
    {
        if (Directory.Exists(gitPath))
        {
            return Path.GetFullPath(gitPath);
        }

        if (!File.Exists(gitPath))
        {
            return null;
        }

        var value = File.ReadAllText(gitPath).Trim();
        if (!value.StartsWith("gitdir: ", StringComparison.Ordinal))
        {
            return null;
        }

        var directory = Path.GetDirectoryName(gitPath)!;
        var resolved = Path.GetFullPath(Path.Combine(directory, value["gitdir: ".Length..].Trim()));
        return Directory.Exists(resolved) ? resolved : null;
    }

    private static string ResolveCommonGitDirectory(string gitDirectory)
    {
        var commonDirPath = Path.Combine(gitDirectory, "commondir");
        if (!File.Exists(commonDirPath))
        {
            return gitDirectory;
        }

        return Path.GetFullPath(Path.Combine(gitDirectory, File.ReadAllText(commonDirPath).Trim()));
    }

    private static string? NormalizeCommitHash(string value)
    {
        var hash = value.Trim();
        return hash.Length is 40 or 64 && hash.All(Uri.IsHexDigit)
            ? hash.ToLowerInvariant()
            : null;
    }

    private static bool IsBuildOutput(string root, string path)
    {
        var relative = Path.GetRelativePath(root, path);
        return relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            .Any(segment => segment is "bin" or "obj");
    }
}
