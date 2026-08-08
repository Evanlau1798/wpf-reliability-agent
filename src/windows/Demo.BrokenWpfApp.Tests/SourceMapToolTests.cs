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

    [Fact]
    public void ReadsElementNameAndNamedAncestorChain()
    {
        var path = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");
        var document = SourceMapGenerator.LoadXaml(path);
        var peopleGrid = document.Descendants().Single(element => element.Name.LocalName == "ItemsControl");
        var binding = document.Descendants().Single(element =>
            element.Name.LocalName == "Binding" && (string?)element.Attribute("Path") == "DisplayNmae");
        var textBlock = binding.Ancestors().First(element => element.Name.LocalName == "TextBlock");

        Assert.Equal("ExperimentalPeopleGrid", SourceMapGenerator.GetXName(peopleGrid));
        Assert.Equal(
            new[] { "ExperimentalPeopleGrid" },
            SourceMapGenerator.GetNamedAncestorChain(textBlock));
    }

    [Fact]
    public void ReadsBindingTargetProperty()
    {
        var path = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");
        var document = SourceMapGenerator.LoadXaml(path);
        var binding = document.Descendants().Single(element =>
            element.Name.LocalName == "Binding" && (string?)element.Attribute("Path") == "DisplayNmae");

        Assert.Equal("Text", SourceMapGenerator.GetTargetProperty(binding));
    }

    [Fact]
    public void ParsesBindingShorthandPath()
    {
        Assert.Equal("DisplayNmae", SourceMapGenerator.ParseBindingPath("{Binding DisplayNmae}"));
    }

    [Fact]
    public void ParsesBindingPathEqualsForm()
    {
        Assert.Equal("DisplayNmae", SourceMapGenerator.ParseBindingPath("{Binding Path=DisplayNmae}"));
    }

    [Fact]
    public void ReportsUnsupportedBindingMarkupWithoutGuessing()
    {
        var result = SourceMapGenerator.ParseBinding("{Binding Path=DisplayNmae, Mode=OneWay}");

        Assert.Null(result.Path);
        Assert.Equal("unsupported_binding_markup", result.UnsupportedReason);
    }

    [Fact]
    public void ReportsBindingLineAndColumnFromSource()
    {
        var path = Path.Combine(RepositoryRoot(), "src", "windows", "Demo.BrokenWpfApp", "MainWindow.xaml");
        var document = SourceMapGenerator.LoadXaml(path);
        var binding = document.Descendants().Single(element =>
            element.Name.LocalName == "Binding" && (string?)element.Attribute("Path") == "DisplayNmae");

        var position = SourceMapGenerator.GetSourcePosition(binding);

        Assert.NotNull(position);
        var sourceLine = File.ReadAllLines(path)[position!.Line - 1];
        Assert.Equal(sourceLine.IndexOf("Binding Path=\"DisplayNmae\"", StringComparison.Ordinal) + 1, position.Column);
    }

    [Fact]
    public void FileSha256ChangesWhenSourceChanges()
    {
        var path = Path.GetTempFileName();
        try
        {
            File.WriteAllText(path, "one");
            var first = SourceMapGenerator.ComputeFileSha256(path);
            File.WriteAllText(path, "two");
            var second = SourceMapGenerator.ComputeFileSha256(path);

            Assert.Matches("^[0-9a-f]{64}$", first);
            Assert.NotEqual(first, second);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void ReadsBuildCommitAndReturnsNullWithoutGitMetadata()
    {
        var repositoryCommit = SourceMapGenerator.ReadBuildCommit(RepositoryRoot());
        var temporaryDirectory = Directory.CreateTempSubdirectory("source-map-git-");
        try
        {
            Assert.NotNull(repositoryCommit);
            Assert.Matches("^[0-9a-f]{40,64}$", repositoryCommit!);
            Assert.Null(SourceMapGenerator.ReadBuildCommit(temporaryDirectory.FullName));
        }
        finally
        {
            temporaryDirectory.Delete(recursive: true);
        }
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
