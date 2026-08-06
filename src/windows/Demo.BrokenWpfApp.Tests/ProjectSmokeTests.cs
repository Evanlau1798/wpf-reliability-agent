namespace Demo.BrokenWpfApp.Tests;

public sealed class ProjectSmokeTests
{
    [Fact]
    public void MainWindowTypeIsAvailable()
    {
        Assert.NotNull(typeof(MainWindow));
    }
}
