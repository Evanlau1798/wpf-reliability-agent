using System.Text;
using System.Text.Json;
using System.Xml;
using Reliability.Sensor;
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

    [Fact]
    public void GeneratesDeterministicSortedSourceMapJson()
    {
        var repositoryRoot = RepositoryRoot();
        var projectRoot = Path.Combine(repositoryRoot, "src", "windows", "Demo.BrokenWpfApp");
        var buildCommit = new string('a', 40);

        var first = SourceMapGenerator.GenerateSourceMap(repositoryRoot, projectRoot, buildCommit);
        var second = SourceMapGenerator.GenerateSourceMap(repositoryRoot, projectRoot, buildCommit);

        Assert.Equal(first.Json, second.Json);
        Assert.Equal(first.Sha256, second.Sha256);
        Assert.NotEmpty(first.Entries);
        Assert.Equal(
            first.Entries.Select(entry => entry.Key).Order(StringComparer.Ordinal),
            first.Entries.Select(entry => entry.Key));
    }

    [Fact]
    public void SourceMapSnippetIsBoundedByLinesAndUtf8Bytes()
    {
        var repositoryRoot = Directory.CreateTempSubdirectory("source-map-snippet-");
        var projectRoot = Directory.CreateDirectory(Path.Combine(repositoryRoot.FullName, "Demo"));
        var sourcePath = Path.Combine(projectRoot.FullName, "MainWindow.xaml");
        var lines = new List<string>
        {
            "<Window xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" xmlns:x=\"http://schemas.microsoft.com/winfx/2006/xaml\" x:Class=\"Demo.MainWindow\">",
        };
        lines.AddRange(Enumerable.Range(0, 41).Select(index => $"  <!-- before {index:D2} -->"));
        lines.Add($"  <TextBlock x:Name=\"PersonName\" Text=\"{{Binding DisplayNmae}}\" Tag=\"{new string('é', 3_000)}\" />");
        lines.AddRange(Enumerable.Range(0, 41).Select(index => $"  <!-- after {index:D2} -->"));
        lines.Add("</Window>");
        File.WriteAllText(sourcePath, string.Join('\n', lines));

        try
        {
            var entry = Assert.Single(SourceMapGenerator.GenerateSourceMap(
                repositoryRoot.FullName,
                projectRoot.FullName,
                buildCommit: null).Entries);

            Assert.InRange(entry.SourceSnippet.Split('\n').Length, 1, 40);
            Assert.True(Encoding.UTF8.GetByteCount(entry.SourceSnippet) <= 4_096);
            Assert.True(entry.SourceSnippetTruncated);
            Assert.True(entry.SourceSnippetStartLine > 1);
            Assert.Contains("{Binding DisplayNmae}", entry.SourceSnippet, StringComparison.Ordinal);
        }
        finally
        {
            repositoryRoot.Delete(recursive: true);
        }
    }

    [Fact]
    public void DisplayNmaeSourceMapEntryMatchesGoldenSnapshot()
    {
        var repositoryRoot = RepositoryRoot();
        var projectRoot = Path.Combine(repositoryRoot, "src", "windows", "Demo.BrokenWpfApp");
        var goldenPath = Path.Combine(
            repositoryRoot,
            "src",
            "windows",
            "Demo.BrokenWpfApp.Tests",
            "Golden",
            "source-map-displaynmae.json");
        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        var expected = JsonSerializer.Deserialize<SourceMapGolden>(File.ReadAllText(goldenPath), options);
        var entry = SourceMapGenerator.GenerateSourceMap(repositoryRoot, projectRoot, new string('a', 40))
            .Entries.Single(item => item.BindingPath == "DisplayNmae");

        Assert.NotNull(expected);
        Assert.Equal(expected!.File, entry.File);
        Assert.Equal(expected.Line, entry.Line);
        Assert.Equal(expected.Key, entry.Key);
    }

    [Fact]
    public async Task DemoBuildOutputContainsSourceMapReadableAtSensorStartup()
    {
        var repositoryRoot = RepositoryRoot();
        var configuration = new DirectoryInfo(AppContext.BaseDirectory).Parent?.Name
            ?? throw new DirectoryNotFoundException("Test build configuration directory not found.");
        var sourceMapPath = Path.Combine(
            repositoryRoot,
            "src",
            "windows",
            "Demo.BrokenWpfApp",
            "bin",
            configuration,
            "net8.0-windows",
            "source-map.json");

        Assert.True(File.Exists(sourceMapPath), $"Missing source-map artifact: {sourceMapPath}");
        await using var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
        {
            ApiBaseUri = new Uri("https://localhost"),
            DeviceId = "demo-test-device",
            DeviceToken = string.Empty,
            ApplicationId = "demo-broken-wpf-app",
            ApplicationVersion = "0.1.0",
            DisableBackgroundPersistence = true,
            SourceMapPath = sourceMapPath,
        });

        Assert.True(sensor.SourceMapEntryCount > 0);
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

    private sealed record SourceMapGolden(string File, int Line, string Key);
}
