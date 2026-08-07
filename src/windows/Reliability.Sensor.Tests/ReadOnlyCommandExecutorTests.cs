using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class ReadOnlyCommandExecutorTests
{
    [Fact]
    public async Task HealthGetSnapshotReturnsAppSensorSessionAndQueueHealth()
    {
        await using var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
        {
            ApiBaseUri = new Uri("https://reliability.example.test"),
            DeviceId = "device-test",
            DeviceToken = "test-token",
            ApplicationId = "demo-app",
            ApplicationVersion = "1.2.3",
            DisableBackgroundPersistence = true,
        });
        Assert.True(sensor.TryEnqueue(
            EventType.PerformanceSample,
            Severity.WARNING,
            JsonSerializer.SerializeToElement(new { }),
            JsonSerializer.SerializeToElement(new { p95_ms = 30 }),
            out _));
        var command = (await ReadCommandAsync()) with
        {
            Tool = DiagnosticTool.HealthGetSnapshot,
            Arguments = JsonSerializer.SerializeToElement(new { }),
        };
        var executor = new ReadOnlyCommandExecutor(sensor);

        var result = await executor.ExecuteAsync(command, CancellationToken.None);

        Assert.Equal("demo-app", result.GetProperty("application_id").GetString());
        Assert.Equal("1.2.3", result.GetProperty("application_version").GetString());
        Assert.Equal(sensor.AppSessionId, result.GetProperty("app_session_id").GetString());
        Assert.True(result.GetProperty("sensor_enabled").GetBoolean());
        Assert.True(result.GetProperty("can_upload").GetBoolean());
        Assert.Equal(1, result.GetProperty("queued_event_count").GetInt32());
        Assert.Equal(0, result.GetProperty("dropped_event_count").GetInt64());
    }

    [Fact]
    public async Task BindingGetErrorsReturnsRecentAggregateSummaries()
    {
        await using var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
        {
            ApiBaseUri = new Uri("https://reliability.example.test"),
            DeviceId = "device-test",
            DeviceToken = "test-token",
            ApplicationId = "demo-app",
            ApplicationVersion = "1.2.3",
            BindingBurstThreshold = 1,
            DisableBackgroundPersistence = true,
        });
        Assert.True(sensor.ReportBindingFailure(
            "DisplayNmae",
            "Text",
            "TextBlock",
            "PersonName"));
        var command = (await ReadCommandAsync()) with
        {
            Tool = DiagnosticTool.BindingGetErrors,
            Arguments = JsonSerializer.SerializeToElement(new { }),
        };
        var executor = new ReadOnlyCommandExecutor(sensor);

        var result = await executor.ExecuteAsync(command, CancellationToken.None);

        var aggregates = result.GetProperty("aggregates");
        Assert.Equal(1, aggregates.GetArrayLength());
        var aggregate = aggregates[0];
        Assert.Equal("DisplayNmae", aggregate.GetProperty("binding_path").GetString());
        Assert.Equal("Text", aggregate.GetProperty("target_property").GetString());
        Assert.Equal("TextBlock", aggregate.GetProperty("element_type").GetString());
        Assert.Equal(1, aggregate.GetProperty("occurrence_count").GetInt32());
    }

    [Fact]
    public async Task MutationToolIsRejectedBySwitchDispatcher()
    {
        await using var sensor = ReliabilitySensor.Start(null);
        var command = await ReadCommandAsync("diagnostic-command-valid-mutation.json");
        var executor = new ReadOnlyCommandExecutor(sensor);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            executor.ExecuteAsync(command, CancellationToken.None));

        Assert.Equal("Command tool is not available to the read-only executor.", exception.Message);
    }

    private static async Task<DiagnosticCommand> ReadCommandAsync(
        string fixtureName = "diagnostic-command-valid-read.json")
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            fixtureName));
        return JsonSerializer.Deserialize(
            json,
            ContractJsonContext.Default.DiagnosticCommand)!;
    }
}
