using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Threading;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed record ExceptionDiagnostic(
    string ExceptionType,
    string MessageTemplate,
    IReadOnlyList<string> AppFrames,
    bool IsTerminating,
    bool IsUnhandled);

internal static partial class ExceptionDiagnosticFactory
{
    private const int MaxMessageCharacters = 1_024;
    private const int MaxAppFrames = 20;

    public static ExceptionDiagnostic Create(Exception exception, bool isTerminating, bool isUnhandled)
    {
        ArgumentNullException.ThrowIfNull(exception);
        return new ExceptionDiagnostic(
            exception.GetType().FullName ?? exception.GetType().Name,
            NormalizeMessage(exception.Message),
            AppFrames(exception),
            isTerminating,
            isUnhandled);
    }

    public static string Fingerprint(ExceptionDiagnostic diagnostic) =>
        CanonicalJson.Hash(JsonSerializer.SerializeToElement(new
        {
            exception_type = diagnostic.ExceptionType,
            message_template = diagnostic.MessageTemplate,
            app_frames = diagnostic.AppFrames,
        }));

    private static string NormalizeMessage(string message)
    {
        var normalized = UserProfilePath().Replace(message, "%USERPROFILE%");
        normalized = SecretAssignment().Replace(normalized, match => $"{match.Groups[1].Value}=[REDACTED]");
        normalized = BearerToken().Replace(normalized, "Bearer [REDACTED]");
        normalized = AbsoluteWindowsPath().Replace(normalized, "<path>");
        normalized = GuidValue().Replace(normalized, "<guid>");
        normalized = NumberValue().Replace(normalized, "#");
        return normalized.Length <= MaxMessageCharacters ? normalized : normalized[..MaxMessageCharacters];
    }

    private static IReadOnlyList<string> AppFrames(Exception exception)
    {
        var frames = new StackTrace(exception, false).GetFrames() ?? [];
        return frames
            .Select(frame => frame.GetMethod())
            .Where(method => method?.DeclaringType is not null && IsApplicationAssembly(method.DeclaringType.Assembly.GetName().Name))
            .Select(method => $"{method!.DeclaringType!.FullName}.{method.Name}")
            .Distinct(StringComparer.Ordinal)
            .Take(MaxAppFrames)
            .ToArray();
    }

    private static bool IsApplicationAssembly(string? assemblyName) =>
        assemblyName is not null
        && !assemblyName.StartsWith("System", StringComparison.Ordinal)
        && !assemblyName.StartsWith("Microsoft", StringComparison.Ordinal)
        && assemblyName is not "mscorlib" and not "WindowsBase" and not "PresentationCore" and not "PresentationFramework";

    [GeneratedRegex("(?i)[a-z]:[\\\\/]+users[\\\\/]+[^\\\\/\\s]+")]
    private static partial Regex UserProfilePath();

    [GeneratedRegex("(?i)\\b(api[_-]?key|token|secret)\\b\\s*[:=]\\s*[^\\s;,]+")]
    private static partial Regex SecretAssignment();

    [GeneratedRegex("(?i)\\bbearer\\s+[^\\s;,]+")]
    private static partial Regex BearerToken();

    [GeneratedRegex("(?i)\\b[a-z]:[\\\\/][^\\r\\n\\t ]+")]
    private static partial Regex AbsoluteWindowsPath();

    [GeneratedRegex("(?i)\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b")]
    private static partial Regex GuidValue();

    [GeneratedRegex("\\b\\d+\\b")]
    private static partial Regex NumberValue();
}

internal sealed class ExceptionDiagnosticCollector(ReliabilitySensor sensor) : IDisposable
{
    private readonly object _gate = new();
    private Application? _application;
    private bool _installed;

    public void Install(Application application)
    {
        ArgumentNullException.ThrowIfNull(application);
        lock (_gate)
        {
            if (_installed)
            {
                return;
            }

            _application = application;
            application.DispatcherUnhandledException += OnDispatcherUnhandledException;
            AppDomain.CurrentDomain.UnhandledException += OnUnhandledException;
            TaskScheduler.UnobservedTaskException += OnUnobservedTaskException;
            _installed = true;
        }
    }

    public void Uninstall()
    {
        lock (_gate)
        {
            if (!_installed)
            {
                return;
            }

            _application!.DispatcherUnhandledException -= OnDispatcherUnhandledException;
            AppDomain.CurrentDomain.UnhandledException -= OnUnhandledException;
            TaskScheduler.UnobservedTaskException -= OnUnobservedTaskException;
            _application = null;
            _installed = false;
        }
    }

    internal void OnUnhandledException(object? sender, UnhandledExceptionEventArgs args)
    {
        if (args.ExceptionObject is Exception exception)
        {
            Capture(exception, args.IsTerminating, isUnhandled: true);
        }
    }

    internal void OnUnobservedTaskException(object? sender, UnobservedTaskExceptionEventArgs args) =>
        Capture(args.Exception, isTerminating: false, isUnhandled: false);

    public void Dispose() => Uninstall();

    private void OnDispatcherUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs args) =>
        Capture(args.Exception, isTerminating: false, isUnhandled: true);

    private void Capture(Exception exception, bool isTerminating, bool isUnhandled)
    {
        try
        {
            var diagnostic = ExceptionDiagnosticFactory.Create(exception, isTerminating, isUnhandled);
            var fingerprint = ExceptionDiagnosticFactory.Fingerprint(diagnostic);
            sensor.TryEnqueue(
                EventType.ExceptionSummary,
                isTerminating ? Severity.CRITICAL : Severity.ERROR,
                JsonSerializer.SerializeToElement(new { exception_fingerprint = fingerprint }),
                JsonSerializer.SerializeToElement(new
                {
                    fingerprint,
                    exception_type = diagnostic.ExceptionType,
                    message_template = diagnostic.MessageTemplate,
                    app_frames = diagnostic.AppFrames,
                    is_terminating = diagnostic.IsTerminating,
                    is_unhandled = diagnostic.IsUnhandled,
                }),
                out _);
        }
        catch (Exception)
        {
            // Exception diagnostics must never alter the host application's exception policy.
        }
    }
}
