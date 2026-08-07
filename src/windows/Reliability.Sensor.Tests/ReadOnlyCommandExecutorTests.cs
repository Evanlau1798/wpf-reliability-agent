using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class ReadOnlyCommandExecutorTests
{
    [Fact]
    public async Task MutationToolIsRejectedBySwitchDispatcher()
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "diagnostic-command-valid-mutation.json"));
        var command = JsonSerializer.Deserialize(
            json,
            ContractJsonContext.Default.DiagnosticCommand)!;
        var executor = new ReadOnlyCommandExecutor();

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            executor.ExecuteAsync(command, CancellationToken.None));

        Assert.Equal("Command tool is not available to the read-only executor.", exception.Message);
    }
}
