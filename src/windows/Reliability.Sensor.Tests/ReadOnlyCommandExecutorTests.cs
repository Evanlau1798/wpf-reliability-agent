using System.Text.Json;
using System.Windows.Controls;
using System.Windows.Threading;
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
    public async Task BindingGetLiveCandidatesReturnsKnownErrorsInsideRequestedSubtree()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        var ready = new TaskCompletionSource<(StackPanel Root, TextBlock Broken, TextBlock Outside)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel { Name = "PeopleGrid" };
            var broken = new TextBlock { Name = "PersonName" };
            var outside = new TextBlock { Name = "OtherName" };
            root.Children.Add(broken);
            ready.SetResult((root, broken, outside));
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var (root, broken, outside) = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            Assert.True(sensor.ReportBindingFailure(broken, "DisplayNmae", "Text", "PersonName"));
            Assert.True(sensor.ReportBindingFailure(outside, "OtherTypo", "Text", "OtherName"));
            var command = (await ReadCommandAsync()) with
            {
                Tool = DiagnosticTool.BindingGetLiveCandidates,
                Arguments = JsonSerializer.SerializeToElement(new { element_id = sensor.GetElementId(root) }),
            };
            var executor = new ReadOnlyCommandExecutor(sensor);

            var result = await executor.ExecuteAsync(command, CancellationToken.None);

            var candidates = result.GetProperty("candidates");
            Assert.Equal(1, candidates.GetArrayLength());
            Assert.Equal(sensor.GetElementId(broken), candidates[0].GetProperty("element_id").GetString());
            Assert.Equal("DisplayNmae", candidates[0].GetProperty("binding_path").GetString());
            Assert.Equal("Text", candidates[0].GetProperty("target_property").GetString());
        }
        finally
        {
            root.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    [Fact]
    public async Task ExceptionGetRecentReturnsOnlyRedactedSummaries()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        var collector = new ExceptionDiagnosticCollector(sensor);
        collector.OnUnhandledException(
            null,
            new UnhandledExceptionEventArgs(
                new InvalidOperationException("Failed at C:\\Users\\Alice\\data.txt token=secret-value"),
                isTerminating: false));
        var command = (await ReadCommandAsync()) with
        {
            Tool = DiagnosticTool.ExceptionGetRecent,
            Arguments = JsonSerializer.SerializeToElement(new { }),
        };
        var executor = new ReadOnlyCommandExecutor(sensor);

        var result = await executor.ExecuteAsync(command, CancellationToken.None);

        var summaries = result.GetProperty("summaries");
        Assert.Equal(1, summaries.GetArrayLength());
        var serialized = summaries[0].GetRawText();
        Assert.Contains("[REDACTED]", serialized, StringComparison.Ordinal);
        Assert.DoesNotContain("Alice", serialized, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret-value", serialized, StringComparison.Ordinal);
    }

    [Fact]
    public async Task UiGetSubtreeHonorsRequestedBudgetsAndLocalCeilings()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        var ready = new TaskCompletionSource<StackPanel>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel { Name = "Root" };
            for (var index = 0; index < 60; index++)
            {
                root.Children.Add(new TextBlock { Name = $"Child{index}" });
            }
            ready.SetResult(root);
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var root = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var rootId = sensor.GetElementId(root);
            var baseCommand = (await ReadCommandAsync()) with { Tool = DiagnosticTool.UiGetSubtree };
            var executor = new ReadOnlyCommandExecutor(sensor);

            var requested = await executor.ExecuteAsync(baseCommand with
            {
                Arguments = JsonSerializer.SerializeToElement(new
                {
                    element_id = rootId,
                    max_depth = 4,
                    max_nodes = 2,
                    max_children_per_node = 1000,
                }),
            }, CancellationToken.None);
            var ceiling = await executor.ExecuteAsync(baseCommand with
            {
                Arguments = JsonSerializer.SerializeToElement(new
                {
                    element_id = rootId,
                    max_depth = 100,
                    max_nodes = 1000,
                    max_children_per_node = 1000,
                }),
            }, CancellationToken.None);

            Assert.Equal(2, requested.GetProperty("nodes").GetArrayLength());
            Assert.Equal(51, ceiling.GetProperty("nodes").GetArrayLength());
            Assert.True(ceiling.GetProperty("truncated").GetBoolean());
        }
        finally
        {
            root.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    [Fact]
    public async Task UiGetElementDetailsReturnsOnlyAllowlistedMetadata()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        var ready = new TaskCompletionSource<Border>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var element = new Border { Name = "SafePanel", Width = 40, Height = 20 };
            ready.SetResult(element);
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var element = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            Assert.True(sensor.ReportBindingFailure(element, "DisplayNmae", "DataContext", "SafePanel"));
            var command = (await ReadCommandAsync()) with
            {
                Tool = DiagnosticTool.UiGetElementDetails,
                Arguments = JsonSerializer.SerializeToElement(new
                {
                    element_id = sensor.GetElementId(element),
                    fields = new[] { "type", "name", "layout", "binding_summary" },
                }),
            };
            var executor = new ReadOnlyCommandExecutor(sensor);

            var result = await executor.ExecuteAsync(command, CancellationToken.None);

            Assert.True(result.GetProperty("succeeded").GetBoolean());
            var details = result.GetProperty("details");
            Assert.Equal("Border", details.GetProperty("type").GetString());
            Assert.Equal("SafePanel", details.GetProperty("name").GetString());
            Assert.Equal("DisplayNmae", details.GetProperty("binding_summary").GetProperty("error_path").GetString());
            Assert.False(details.TryGetProperty("text", out _));
            Assert.False(details.TryGetProperty("data_context", out _));
        }
        finally
        {
            element.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    [Fact]
    public async Task PerformanceSampleReturnsBoundedPassiveMetrics()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        var ready = new TaskCompletionSource<StackPanel>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel();
            root.Children.Add(new TextBlock());
            sensor.InstallPerformanceDiagnostics(
                Dispatcher.CurrentDispatcher,
                new PerformanceOptions { SampleCooldown = TimeSpan.FromMilliseconds(100) });
            ready.SetResult(root);
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var root = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var command = (await ReadCommandAsync()) with
            {
                Tool = DiagnosticTool.PerformanceSample,
                Arguments = JsonSerializer.SerializeToElement(new { element_id = sensor.GetElementId(root) }),
            };
            var executor = new ReadOnlyCommandExecutor(sensor);

            var result = await executor.ExecuteAsync(command, CancellationToken.None);

            Assert.True(result.GetProperty("succeeded").GetBoolean());
            Assert.Equal(2, result.GetProperty("visual_count").GetInt32());
            Assert.InRange(result.GetProperty("sample_duration_ms").GetDouble(), 0, 60_000);
            Assert.Equal("LOW", result.GetProperty("confidence").GetString());
        }
        finally
        {
            sensor.StopPerformanceDiagnostics();
            root.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    [Fact]
    public async Task ToolTimeoutReturnsStructuredError()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions());
        using var releaseDispatcher = new ManualResetEventSlim();
        var ready = new TaskCompletionSource<StackPanel>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel { Name = "BlockedRoot" };
            ready.SetResult(root);
            releaseDispatcher.Wait();
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var root = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var command = (await ReadCommandAsync()) with
            {
                Tool = DiagnosticTool.UiGetSubtree,
                Arguments = JsonSerializer.SerializeToElement(new { element_id = sensor.GetElementId(root) }),
                TimeoutMs = 100,
            };
            var executor = new ReadOnlyCommandExecutor(sensor);

            var result = await executor.ExecuteAsync(command, CancellationToken.None);

            Assert.False(result.GetProperty("succeeded").GetBoolean());
            Assert.Equal("TIMEOUT", result.GetProperty("error").GetProperty("code").GetString());
        }
        finally
        {
            releaseDispatcher.Set();
            root.Dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
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

    private static ReliabilitySensorOptions TestOptions() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-app",
        ApplicationVersion = "1.2.3",
        DisableBackgroundPersistence = true,
    };
}
