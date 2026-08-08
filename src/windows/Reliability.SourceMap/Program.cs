if (args.Length is not 2 and not 4
    || args[0] != "--project-root"
    || (args.Length == 4 && args[2] != "--output"))
{
    Console.Error.WriteLine("Usage: Reliability.SourceMap --project-root <path> [--output <bin-or-obj-path>]");
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
if (args.Length == 4)
{
    var outputPath = Path.GetFullPath(args[3]);
    var relativeOutput = Path.GetRelativePath(projectRoot, outputPath);
    var firstSegment = relativeOutput.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)[0];
    if (Path.IsPathRooted(relativeOutput)
        || relativeOutput == ".."
        || relativeOutput.StartsWith($"..{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
        || relativeOutput.StartsWith($"..{Path.AltDirectorySeparatorChar}", StringComparison.Ordinal)
        || firstSegment is not ("bin" or "obj"))
    {
        Console.Error.WriteLine("Output must stay under the project bin or obj directory.");
        return 2;
    }

    Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
    File.WriteAllText(outputPath, sourceMap.Json);
}
else
{
    Console.WriteLine(sourceMap.Json);
}
return 0;
