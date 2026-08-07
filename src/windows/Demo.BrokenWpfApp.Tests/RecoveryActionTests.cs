namespace Demo.BrokenWpfApp.Tests;

public sealed class RecoveryActionTests
{
    [Fact]
    public void ExperimentalFeatureStartsEnabled()
    {
        Assert.True(new ExperimentalPeopleGridState().IsEnabled);
    }

    [Fact]
    public void RegisteredDisableActionRejectsStaleExpectedStateAfterApply()
    {
        var state = new ExperimentalPeopleGridState();
        var registry = new RecoveryActionRegistry();
        registry.Register(RecoveryAction.DisableExperimentalPeopleGrid, state.Disable);

        var first = registry.Execute(RecoveryAction.DisableExperimentalPeopleGrid, expectedCurrentState: true);
        var second = registry.Execute(RecoveryAction.DisableExperimentalPeopleGrid, expectedCurrentState: true);

        Assert.Equal(RecoveryStatus.APPLIED, first.Status);
        Assert.True(first.BeforeState);
        Assert.False(first.AfterState);
        Assert.Equal(RecoveryStatus.REJECTED, second.Status);
        Assert.Equal("EXPECTED_STATE_MISMATCH", second.ErrorCode);
        Assert.False(state.IsEnabled);
    }

    [Fact]
    public void ExpectedStateMismatchFailsClosed()
    {
        var state = new ExperimentalPeopleGridState();
        var registry = new RecoveryActionRegistry();
        registry.Register(RecoveryAction.DisableExperimentalPeopleGrid, state.Disable);

        var result = registry.Execute(RecoveryAction.DisableExperimentalPeopleGrid, expectedCurrentState: false);

        Assert.Equal(RecoveryStatus.REJECTED, result.Status);
        Assert.Equal("EXPECTED_STATE_MISMATCH", result.ErrorCode);
        Assert.True(state.IsEnabled);
    }

    [Fact]
    public void UnregisteredActionFailsClosed()
    {
        var state = new ExperimentalPeopleGridState();
        var registry = new RecoveryActionRegistry();

        var result = registry.Execute(RecoveryAction.DisableExperimentalPeopleGrid, expectedCurrentState: true);

        Assert.Equal(RecoveryStatus.REJECTED, result.Status);
        Assert.Equal("UNREGISTERED_ACTION", result.ErrorCode);
        Assert.True(state.IsEnabled);
    }

    [Fact]
    public void RecoveryEnumHasOnlyTheApprovedAction()
    {
        Assert.Equal(
            [RecoveryAction.DisableExperimentalPeopleGrid],
            Enum.GetValues<RecoveryAction>());
    }
}
