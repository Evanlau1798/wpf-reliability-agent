using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class StateSnapshotComparerTests
{
    [Fact]
    public async Task CompareSnapshotsReturnsStableLeafDiffsAndNumericDeltas()
    {
        await using var sensor = ReliabilitySensor.Start(null);
        using var fixture = JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "state-compare-snapshots.json")));
        var command = new DiagnosticCommand(
            "1.0",
            "command-state-compare",
            "incident-1",
            sensor.AppSessionId,
            DiagnosticTool.StateCompareSnapshots,
            fixture.RootElement.Clone(),
            "unused-in-direct-executor-test",
            RiskLevel.LOW,
            null,
            "idempotency-state-compare",
            DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow.AddMinutes(1),
            5_000);
        var executor = new ReadOnlyCommandExecutor(sensor);

        var result = await executor.ExecuteAsync(command, CancellationToken.None);

        Assert.True(result.GetProperty("changed").GetBoolean());
        var changes = result.GetProperty("changes");
        Assert.Equal(4, changes.GetArrayLength());
        Assert.Equal("binding_errors_per_second", changes[0].GetProperty("path").GetString());
        Assert.Equal(-100.0, changes[0].GetProperty("delta").GetDouble());
        Assert.Equal("frame.p95_ms", changes[1].GetProperty("path").GetString());
        Assert.Equal(-20.0, changes[1].GetProperty("delta").GetDouble());
        Assert.Equal("status", changes[2].GetProperty("path").GetString());
        Assert.Equal("visual_count", changes[3].GetProperty("path").GetString());
        Assert.Equal(-1100.0, changes[3].GetProperty("delta").GetDouble());
    }
}
