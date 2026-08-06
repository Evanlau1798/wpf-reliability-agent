namespace Demo.BrokenWpfApp.Tests;

public sealed class DemoXamlTests
{
    private static readonly string Xaml = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "MainWindow.xaml"));

    [Fact]
    public void ExperimentalTemplateContainsTheIntentionalBindingTypo()
    {
        Assert.Contains("{Binding DisplayNmae}", Xaml, StringComparison.Ordinal);
        Assert.Contains("x:Name=\"ExperimentalPeopleGrid\"", Xaml, StringComparison.Ordinal);
    }

    [Fact]
    public void FallbackAndVisibleStateIndicatorsExist()
    {
        Assert.Contains("x:Name=\"FallbackView\"", Xaml, StringComparison.Ordinal);
        Assert.Contains("FeatureStateText", Xaml, StringComparison.Ordinal);
        Assert.Contains("ItemCountText", Xaml, StringComparison.Ordinal);
        Assert.Contains("Reset Demo", Xaml, StringComparison.Ordinal);
    }

    [Fact]
    public void DemoHasBoundedAnimationAndNoAgentSubmissionButton()
    {
        Assert.Contains("IsAnimated", Xaml, StringComparison.Ordinal);
        Assert.DoesNotContain("Send to Agent", Xaml, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("Submit to Agent", Xaml, StringComparison.OrdinalIgnoreCase);
    }
}
