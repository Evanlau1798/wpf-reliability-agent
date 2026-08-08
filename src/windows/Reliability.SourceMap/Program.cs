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

Console.WriteLine("[]");
return 0;
