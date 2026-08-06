using System.Collections.Concurrent;
using System.Net;
using System.Runtime.ExceptionServices;
using System.Text;
using System.Text.Json;
using System.Windows.Threading;
using Reliability.Sensor;

namespace Demo.BrokenWpfApp.Tests;

public sealed class BindingSignalIntegrationTests
{
    [Fact]
    public void BrokenGridAutomaticallyProducesALocalBindingAggregate()
        => RunOnSta(RunBrokenGrid);

    [Fact]
    public void BrokenGridRemainsResponsiveAndRelaysDurableEventsAfterReconnect()
        => RunOnSta(RunDurableRelay);

    private static void RunOnSta(Action action)
    {
        Exception? failure = null;
        var completed = new ManualResetEventSlim();
        var thread = new Thread(() =>
        {
            try
            {
                action();
            }
            catch (Exception exception)
            {
                failure = exception;
            }
            finally
            {
                completed.Set();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();

        Assert.True(completed.Wait(TimeSpan.FromSeconds(20)), "The WPF binding signal smoke test timed out.");
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }
    }

    private static void RunBrokenGrid()
    {
        var sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
        {
            ApiBaseUri = new Uri("https://localhost"),
            DeviceId = "demo-test-device",
            DeviceToken = string.Empty,
            ApplicationId = "demo-broken-wpf-app",
            ApplicationVersion = "0.1.0",
            BindingBurstThreshold = 1,
        });
        sensor.InstallBindingDiagnostics();
        var window = new MainWindow(sensor);

        try
        {
            window.Show();
            window.UpdateLayout();
            window.Dispatcher.Invoke(static () => { }, DispatcherPriority.ApplicationIdle);

            Assert.True(sensor.BindingAggregateCount > 0);
            Assert.False(sensor.CanUpload);
        }
        finally
        {
            window.Close();
            sensor.StopBindingDiagnostics();
            sensor.DisposeAsync().AsTask().GetAwaiter().GetResult();
        }
    }

    private static void RunDurableRelay()
    {
        var directory = Path.Combine(
            Environment.CurrentDirectory,
            "tmp",
            "demo-relay-tests",
            Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "outbox.db");
        var handler = new RecoveringHandler();
        SqliteOutbox? inspection = null;
        ReliabilitySensor? sensor = null;
        MainWindow? window = null;

        try
        {
            inspection = SqliteOutbox.OpenAsync(path).GetAwaiter().GetResult();
            sensor = ReliabilitySensor.Start(new ReliabilitySensorOptions
            {
                ApiBaseUri = new Uri("https://localhost"),
                DeviceId = "demo-test-device",
                DeviceToken = "test-token",
                ApplicationId = "demo-broken-wpf-app",
                ApplicationVersion = "0.1.0",
                BindingBurstThreshold = 1,
                OutboxPath = path,
                TelemetryHandler = handler,
                RelayPollInterval = TimeSpan.FromMilliseconds(10),
            });
            sensor.InstallBindingDiagnostics();
            window = new MainWindow(sensor);
            window.Show();
            window.UpdateLayout();
            window.Dispatcher.Invoke(static () => { }, DispatcherPriority.ApplicationIdle);

            var dispatcherResponded = false;
            window.Dispatcher.BeginInvoke(() => dispatcherResponded = true, DispatcherPriority.Background);
            window.Dispatcher.Invoke(static () => { }, DispatcherPriority.ApplicationIdle);
            var pending = WaitForPendingRetry(inspection);

            Assert.True(dispatcherResponded);
            Assert.True(sensor.BindingAggregateCount > 0);
            Assert.NotNull(pending);
            handler.GoOnline();
            var sent = WaitForSent(inspection, pending!.EventId);
            Assert.NotNull(sent?.SentAtUtc);
            Assert.Contains(handler.Batches, batch => batch.Contains(pending.EventId));
        }
        finally
        {
            window?.Close();
            sensor?.StopBindingDiagnostics();
            sensor?.DisposeAsync().AsTask().GetAwaiter().GetResult();
            inspection?.DisposeAsync().AsTask().GetAwaiter().GetResult();
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private static OutboxEvent? WaitForPendingRetry(SqliteOutbox outbox)
    {
        for (var attempt = 0; attempt < 300; attempt++)
        {
            var pending = outbox.GetDueEventsAsync(DateTimeOffset.UtcNow.AddMinutes(5), 50)
                .GetAwaiter()
                .GetResult()
                .FirstOrDefault(item => item.AttemptCount > 0);
            if (pending is not null)
            {
                return pending;
            }

            Thread.Sleep(10);
        }

        return null;
    }

    private static OutboxEvent? WaitForSent(SqliteOutbox outbox, string eventId)
    {
        for (var attempt = 0; attempt < 300; attempt++)
        {
            var stored = outbox.GetEventAsync(eventId).GetAwaiter().GetResult();
            if (stored?.SentAtUtc is not null)
            {
                return stored;
            }

            Thread.Sleep(10);
        }

        return null;
    }

    private sealed class RecoveringHandler : HttpMessageHandler
    {
        private readonly ConcurrentQueue<IReadOnlyList<string>> _batches = new();
        private int _online;

        public IReadOnlyList<IReadOnlyList<string>> Batches => _batches.ToArray();

        public void GoOnline() => Volatile.Write(ref _online, 1);

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            var body = await request.Content!.ReadAsStringAsync(cancellationToken);
            using var document = JsonDocument.Parse(body);
            var eventIds = document.RootElement.GetProperty("events")
                .EnumerateArray()
                .Select(item => item.GetProperty("event_id").GetString()!)
                .ToArray();
            _batches.Enqueue(eventIds);
            if (Volatile.Read(ref _online) == 0)
            {
                throw new HttpRequestException("The fake network is offline.");
            }

            var response = JsonSerializer.Serialize(new
            {
                accepted_event_ids = eventIds,
                duplicate_event_ids = Array.Empty<string>(),
                rejected = Array.Empty<object>(),
            });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(response, Encoding.UTF8, "application/json"),
            };
        }
    }

}
