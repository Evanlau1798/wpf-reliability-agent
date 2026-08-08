using System.Xml;
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

    [Fact]
    public void NormalizesRepoRelativePathsAndRejectsOutsidePaths()
    {
        var repositoryRoot = RepositoryRoot();
        var mainWindow = Path.Combine(repositoryRoot, "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");
        var outside = Path.GetFullPath(Path.Combine(repositoryRoot, "..", "outside.xaml"));

        var relative = SourceMapGenerator.NormalizeRepoRelativePath(repositoryRoot, mainWindow);

        Assert.Equal("src/windows/Demo.BrokenWpfApp/MainWindow.xaml", relative);
        Assert.Throws<ArgumentOutOfRangeException>(
            () => SourceMapGenerator.NormalizeRepoRelativePath(repositoryRoot, outside));
    }

    [Fact]
    public void LoadsXamlAsXmlWithLineInformation()
    {
        var path = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");

        var document = SourceMapGenerator.LoadXaml(path);
        var lineInfo = Assert.IsAssignableFrom<IXmlLineInfo>(document.Root);

        Assert.True(lineInfo.HasLineInfo());
        Assert.True(lineInfo.LineNumber > 0);
    }

    [Fact]
    public void ReadsMainWindowXClass()
    {
        var path = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");

        var xClass = SourceMapGenerator.GetXClass(SourceMapGenerator.LoadXaml(path));

        Assert.Equal("Demo.BrokenWpfApp.MainWindow", xClass);
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
