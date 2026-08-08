using Reliability.SourceMap;

namespace Demo.BrokenWpfApp.Tests;

public sealed class SourceMapToolTests
{
    [Fact]
    public void SourceMapToolIsAnExecutableProject()
    {
        var project = Path.Combine(
            RepositoryRoot(),
            "src",
            "windows",
            "Reliability.SourceMap",
            "Reliability.SourceMap.csproj");

        Assert.True(File.Exists(project), $"Missing source-map project: {project}");
        Assert.Contains("<OutputType>Exe</OutputType>", File.ReadAllText(project), StringComparison.Ordinal);
    }

    [Fact]
    public void DiscoversOnlyXamlFilesInsideDemoProjectRoot()
    {
        var projectRoot = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp");

        var files = SourceMapGenerator.DiscoverXamlFiles(projectRoot);

        Assert.Equal(new[] { "App.xaml", "MainWindow.xaml" }, files.Select(Path.GetFileName));
        Assert.All(files, path => Assert.StartsWith(
            Path.GetFullPath(projectRoot) + Path.DirectorySeparatorChar,
            Path.GetFullPath(path),
            StringComparison.OrdinalIgnoreCase));
    }

    private static string RepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "WpfReliabilityAgent.sln")))
        {
            directory = directory.Parent;
        }

        return directory?.FullName ?? throw new DirectoryNotFoundException("Repository root not found.");
    }
}
