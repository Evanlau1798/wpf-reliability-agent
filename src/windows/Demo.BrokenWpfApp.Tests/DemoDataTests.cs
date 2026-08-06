namespace Demo.BrokenWpfApp.Tests;

public sealed class DemoDataTests
{
    [Fact]
    public void GeneratorCreatesExpectedDeterministicPeople()
    {
        var first = DemoDataGenerator.Generate(DemoCalibration.DefaultPersonCount, seed: 1729);
        var second = DemoDataGenerator.Generate(DemoCalibration.DefaultPersonCount, seed: 1729);

        Assert.Equal(1_500, first.Count);
        Assert.Equal(first, second);
        Assert.Equal(DemoDataGenerator.Summarize(first), DemoDataGenerator.Summarize(second));
        Assert.All(first, person => Assert.False(string.IsNullOrWhiteSpace(person.DisplayName)));
    }

    [Fact]
    public void AnimationIsBoundedToCalibrationCeiling()
    {
        var people = DemoDataGenerator.Generate(DemoCalibration.DefaultPersonCount, seed: 1729);

        Assert.Equal(DemoCalibration.AnimatedRowCount, people.Count(person => person.IsAnimated));
    }
}
