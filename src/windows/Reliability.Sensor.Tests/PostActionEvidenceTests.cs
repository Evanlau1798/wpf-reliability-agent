using System.Text.Json;
using System.Windows.Controls;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class PostActionEvidenceTests
{
    [Fact]
    public async Task PostActionSnapshotQueuesReferenceableRecoveryEvidence()
    {
        await using var sensor = ReliabilitySensor.Start(Options());
        var ready = new TaskCompletionSource<(Dispatcher Dispatcher, StackPanel Root)>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var thread = new Thread(() =>
        {
            var root = new StackPanel();
            root.Children.Add(new TextBlock());
            sensor.InstallPerformanceDiagnostics(Dispatcher.CurrentDispatcher);
            ready.TrySetResult((Dispatcher.CurrentDispatcher, root));
            Dispatcher.Run();
        }) { IsBackground = true };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        var (dispatcher, root) = await ready.Task.WaitAsync(TimeSpan.FromSeconds(1));

        try
        {
            var command = await ReadMutationCommandAsync();
            var capture = sensor.CaptureAndQueuePostActionSnapshotAsync(
                command,
                root,
                CancellationToken.None,
                TimeSpan.FromMilliseconds(50));
            await Task.Delay(10);
            Assert.True(sensor.ReportBindingFailure("DisplayNmae", "Text", "TextBlock"));

            var evidenceId = await capture;

            Assert.True(sensor.Events.TryRead(out var envelope));
            Assert.Equal(evidenceId, envelope.EventId);
            Assert.Equal(EventType.RecoveryResult, envelope.EventType);
            Assert.Equal("incident-1", envelope.Correlation.GetProperty("incident_id").GetString());
            Assert.Equal("action-1", envelope.Correlation.GetProperty("action_id").GetString());
            Assert.Equal(1, envelope.Payload.GetProperty("binding_occurrence_count").GetInt64());
            Assert.Equal(2, envelope.Payload.GetProperty("visual_count").GetInt32());
            Assert.Equal(sensor.GetElementId(root), envelope.Payload.GetProperty("visual_scope_id").GetString());
            Assert.True(ContractValidator.Validate(envelope));
        }
        finally
        {
            dispatcher.BeginInvokeShutdown(DispatcherPriority.Send);
            Assert.True(thread.Join(TimeSpan.FromSeconds(1)));
        }
    }

    private static async Task<DiagnosticCommand> ReadMutationCommandAsync()
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "diagnostic-command-valid-mutation.json"));
        return JsonSerializer.Deserialize(json, ContractJsonContext.Default.DiagnosticCommand)!;
    }

    private static ReliabilitySensorOptions Options() => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = "test-token",
        ApplicationId = "demo-broken-wpf-app",
        ApplicationVersion = "0.1.0",
        DisableBackgroundPersistence = true,
    };
}
