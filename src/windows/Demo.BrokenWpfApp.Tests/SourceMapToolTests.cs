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
