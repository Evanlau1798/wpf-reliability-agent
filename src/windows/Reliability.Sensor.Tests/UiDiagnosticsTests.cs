using System.Runtime.ExceptionServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class UiDiagnosticsTests
{
    [Fact]
    public void NullRootReturnsStructuredError()
    {
        var result = UiTreeSnapshotter.Capture(
            null,
            new UiTreeOptions(),
            new ElementIdentityRegistry("session"),
            _ => false);

        Assert.False(result.Succeeded);
        Assert.Equal("UI_ROOT_NOT_FOUND", result.Error?.Code);
    }

    [Fact]
    public void TraversalHonorsDepthNodeAndChildBudgets()
    {
        var result = RunSta(() =>
        {
            var root = new StackPanel();
            for (var index = 0; index < 5; index++)
            {
                var child = new StackPanel();
                child.Children.Add(new TextBlock());
                root.Children.Add(child);
            }

            return UiTreeSnapshotter.Capture(
                root,
                new UiTreeOptions { MaxDepth = 1, MaxNodes = 4, MaxChildrenPerNode = 3 },
                new ElementIdentityRegistry("session"),
                _ => false);
        });

        Assert.True(result.Succeeded);
        Assert.True(result.Truncated);
        Assert.Equal(4, result.Nodes.Count);
        Assert.All(result.Nodes, node => Assert.InRange(node.Depth, 0, 1));
        Assert.Equal(5, result.OmittedNodeCount);
    }

    [Fact]
    public void OptionsAboveHardCeilingsAreRejected()
    {
        var result = UiTreeSnapshotter.Capture(
            null,
            new UiTreeOptions { MaxNodes = 501 },
            new ElementIdentityRegistry("session"),
            _ => false);

        Assert.False(result.Succeeded);
        Assert.Equal("INVALID_UI_TREE_OPTIONS", result.Error?.Code);
    }

    [Fact]
    public void SnapshotNeverSerializesTextOrPasswordContent()
    {
        var result = RunSta(() =>
        {
            var root = new StackPanel();
            root.Children.Add(new TextBox { Text = "private-text-value" });
            root.Children.Add(new PasswordBox { Password = "private-password-value" });
            return UiTreeSnapshotter.Capture(
                root,
                new UiTreeOptions(),
                new ElementIdentityRegistry("session"),
                _ => false);
        });
        var json = JsonSerializer.Serialize(result);

        Assert.DoesNotContain("private-text-value", json, StringComparison.Ordinal);
        Assert.DoesNotContain("private-password-value", json, StringComparison.Ordinal);
    }

    [Fact]
    public void SnapshotMarksOnlyKnownBindingErrorElements()
    {
        var result = RunSta(() =>
        {
            var identities = new ElementIdentityRegistry("session");
            var root = new StackPanel();
            var broken = new TextBlock();
            root.Children.Add(broken);
            var brokenId = identities.GetOrCreate(broken);
            return UiTreeSnapshotter.Capture(
                root,
                new UiTreeOptions(),
                identities,
                elementId => elementId == brokenId);
        });

        Assert.Single(result.Nodes, node => node.HasBindingError);
    }

    [Fact]
    public void DefaultSnapshotFitsTheEventPayloadBudget()
    {
        var byteCount = RunSta(() =>
        {
            var root = new StackPanel();
            for (var branchIndex = 0; branchIndex < 50; branchIndex++)
            {
                var branch = new StackPanel();
                for (var childIndex = 0; childIndex < 5; childIndex++)
                {
                    branch.Children.Add(new TextBlock());
                }

                root.Children.Add(branch);
            }

            var result = UiTreeSnapshotter.Capture(
                root,
                new UiTreeOptions(),
                new ElementIdentityRegistry("session"),
                _ => false);
            Assert.Equal(300, result.Nodes.Count);
            return JsonSerializer.SerializeToUtf8Bytes(new
            {
                nodes = result.Nodes,
                truncated = result.Truncated,
                omitted_node_count = result.OmittedNodeCount,
            }).Length;
        });

        Assert.InRange(byteCount, 1, 65_000);
    }

    [Fact]
    public async Task ElementDetailsRejectUnknownFieldsAndUnknownSessionElements()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var ready = new TaskCompletionSource<(Border Element, string ElementId)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var element = new Border { Name = "SafePanel", Width = 40, Height = 20 };
            ready.SetResult((element, sensor.GetElementId(element)));
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var (element, elementId) = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var denied = await sensor.GetUiElementDetailsAsync(elementId, ["Text"]);
            var missing = await sensor.GetUiElementDetailsAsync("element-old-session-1");
            var invalid = await sensor.GetUiElementDetailsAsync(null!);
            var allowed = await sensor.GetUiElementDetailsAsync(elementId);

            Assert.Equal("POLICY_DENIED", denied.Error?.Code);
            Assert.Equal("ELEMENT_NOT_FOUND", missing.Error?.Code);
            Assert.Equal("ELEMENT_NOT_FOUND", invalid.Error?.Code);
            Assert.True(allowed.Succeeded);
            Assert.Equal("SafePanel", allowed.Details?.Name);
            Assert.Null(allowed.Details?.BindingSummary.ErrorPath);
        }
        finally
        {
            element.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    [Fact]
    public async Task BackgroundCaptureMarshalsToTheOwningDispatcher()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var ready = new TaskCompletionSource<FrameworkElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel();
            root.Children.Add(new TextBlock { Name = "Child" });
            ready.SetResult(root);
            Dispatcher.Run();
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var root = await ready.Task.WaitAsync(TimeSpan.FromSeconds(5));

        try
        {
            var result = await sensor.CaptureUiTreeAsync(root, new UiTreeOptions());

            Assert.True(result.Succeeded);
            Assert.Equal(2, result.Nodes.Count);
            Assert.True(sensor.Events.TryRead(out var envelope));
            Assert.Equal(EventType.UiSnapshot, envelope.EventType);
            Assert.True(ContractValidator.Validate(envelope));
            Assert.InRange(
                JsonSerializer.SerializeToUtf8Bytes(envelope, ContractJsonContext.Default.DiagnosticEnvelope).Length,
                1,
                65_536);
        }
        finally
        {
            root.Dispatcher.InvokeShutdown();
            Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        }
    }

    private static ReliabilitySensorOptions Options() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
    };

    private static T RunSta<T>(Func<T> action)
    {
        T? result = default;
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                result = action();
            }
            catch (Exception exception)
            {
                failure = exception;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        Assert.True(thread.Join(TimeSpan.FromSeconds(5)));
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }

        return result!;
    }
}
