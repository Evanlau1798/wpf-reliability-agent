using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class MutationCommandVerifierTests
{
    [Fact]
    public async Task WrongRiskLabelIsRejected()
    {
        var command = await ReadMutationCommandAsync();

        var allowed = MutationCommandVerifier.HasRequiredRisk(command with
        {
            RiskLevel = RiskLevel.LOW,
        });

        Assert.False(allowed);
    }

    [Fact]
    public async Task HighRiskMutationPassesRiskCheck()
    {
        var command = await ReadMutationCommandAsync();

        Assert.True(MutationCommandVerifier.HasRequiredRisk(command));
    }

    [Fact]
    public async Task MissingApprovalReferenceIsRejected()
    {
        var command = await ReadMutationCommandAsync();

        Assert.False(MutationCommandVerifier.HasApprovalReference(command with
        {
            ApprovalId = null,
        }));
    }

    [Fact]
    public async Task PresentApprovalReferencePassesCheck()
    {
        var command = await ReadMutationCommandAsync();

        Assert.True(MutationCommandVerifier.HasApprovalReference(command));
    }

    private static async Task<DiagnosticCommand> ReadMutationCommandAsync()
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "diagnostic-command-valid-mutation.json"));
        return JsonSerializer.Deserialize(
            json,
            ContractJsonContext.Default.DiagnosticCommand)!;
    }
}
