namespace Demo.BrokenWpfApp.Tests;

public sealed class ProjectSmokeTests
{
    [Fact]
    public void MainWindowTypeIsAvailable()
    {
        Assert.NotNull(typeof(MainWindow));
    }

    [Fact]
    public void AppOwnsSensorStartupAndExitLifecycle()
    {
        var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;

        Assert.Equal(typeof(App), typeof(App).GetMethod("OnStartup", flags)?.DeclaringType);
        Assert.Equal(typeof(App), typeof(App).GetMethod("OnExit", flags)?.DeclaringType);
    }
}
