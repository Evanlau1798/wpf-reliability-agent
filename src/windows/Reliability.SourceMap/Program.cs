if (args.Length != 2 || args[0] != "--project-root")
{
    Console.Error.WriteLine("Usage: Reliability.SourceMap --project-root <path>");
    return 2;
}

var projectRoot = Path.GetFullPath(args[1]);
if (!Directory.Exists(projectRoot))
{
    Console.Error.WriteLine("Project root does not exist.");
    return 2;
}

var repositoryRoot = Reliability.SourceMap.SourceMapGenerator.FindRepositoryRoot(projectRoot);
if (repositoryRoot is null)
{
    Console.Error.WriteLine("Repository root not found.");
    return 2;
}

var sourceMap = Reliability.SourceMap.SourceMapGenerator.GenerateSourceMap(
    repositoryRoot,
    projectRoot,
    Reliability.SourceMap.SourceMapGenerator.ReadBuildCommit(repositoryRoot));
Console.WriteLine(sourceMap.Json);
return 0;
