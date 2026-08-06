using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal sealed record BindingTraceMessage(
    DateTimeOffset TimestampUtc,
    string Message,
    bool WasTruncated);

internal sealed record BindingDiagnostic(
    DateTimeOffset TimestampUtc,
    string Category,
    string? BindingPath,
    string? TargetProperty,
    string? ElementType,
    string? ElementName,
    bool WasTruncated);

internal sealed class BindingTraceListener(Action<BindingTraceMessage> sink) : TraceListener
{
    private const int MaxMessageBytes = 4_096;
    private readonly object _lifecycleLock = new();
    private SourceLevels _originalLevel;
    private bool _installed;

    public void Install()
    {
        lock (_lifecycleLock)
        {
            var source = PresentationTraceSources.DataBindingSource;
            if (!_installed)
            {
                _originalLevel = source.Switch.Level;
                _installed = true;
            }

            RemoveRegistrations(source);
            source.Listeners.Add(this);
            source.Switch.Level = SourceLevels.All;
        }
    }

    public void Uninstall()
    {
        lock (_lifecycleLock)
        {
            if (!_installed)
            {
                return;
            }

            var source = PresentationTraceSources.DataBindingSource;
            RemoveRegistrations(source);
            source.Switch.Level = _originalLevel;
            _installed = false;
        }
    }

    public override void Write(string? message)
    {
    }

    public override void WriteLine(string? message)
    {
    }

    public override void TraceEvent(
        TraceEventCache? eventCache,
        string source,
        TraceEventType eventType,
        int id,
        string? message)
    {
        if (string.IsNullOrEmpty(message))
        {
            return;
        }

        var bounded = TruncateUtf8(message, out var wasTruncated);
        sink(new BindingTraceMessage(DateTimeOffset.UtcNow, bounded, wasTruncated));
    }

    public override void TraceEvent(
        TraceEventCache? eventCache,
        string source,
        TraceEventType eventType,
        int id,
        string? format,
        params object?[]? args)
    {
        string? message;
        try
        {
            message = args is not null && format is not null
                ? string.Format(System.Globalization.CultureInfo.InvariantCulture, format, args)
                : format;
        }
        catch (FormatException)
        {
            message = format;
        }

        TraceEvent(eventCache, source, eventType, id, message);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            Uninstall();
        }

        base.Dispose(disposing);
    }

    private void RemoveRegistrations(TraceSource source)
    {
        while (source.Listeners.Cast<TraceListener>().Any(current => ReferenceEquals(current, this)))
        {
            source.Listeners.Remove(this);
        }
    }

    private static string TruncateUtf8(string message, out bool wasTruncated)
    {
        wasTruncated = Encoding.UTF8.GetByteCount(message) > MaxMessageBytes;
        if (!wasTruncated)
        {
            return message;
        }

        var bytes = new byte[MaxMessageBytes];
        Encoding.UTF8.GetEncoder().Convert(
            message.AsSpan(),
            bytes,
            flush: true,
            out var charsUsed,
            out _,
            out _);
        return message[..charsUsed];
    }
}

internal static partial class BindingDiagnosticParser
{
    public static BindingDiagnostic? Parse(BindingTraceMessage trace)
    {
        if (!trace.Message.Contains("System.Windows.Data Error", StringComparison.OrdinalIgnoreCase)
            && !trace.Message.Contains("BindingExpression path error", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        if (IsValidationOnly(trace.Message))
        {
            return null;
        }

        var bindingPath = Value(PropertyNotFoundPath().Match(trace.Message))
            ?? Value(BindingExpressionPath().Match(trace.Message));
        var element = TargetElement().Match(trace.Message);
        var targetProperty = Value(TargetProperty().Match(trace.Message));
        var elementType = Value(element, "type");
        var elementName = Value(element, "name");
        var category = trace.Message.Contains("property not found", StringComparison.OrdinalIgnoreCase)
            ? "PROPERTY_NOT_FOUND"
            : "BINDING_ERROR";

        return new BindingDiagnostic(
            trace.TimestampUtc,
            category,
            bindingPath,
            targetProperty,
            elementType,
            elementName,
            trace.WasTruncated);
    }

    private static bool IsValidationOnly(string message) =>
        !message.Contains("BindingExpression path error", StringComparison.OrdinalIgnoreCase)
        && (message.Contains("validation failed", StringComparison.OrdinalIgnoreCase)
            || message.Contains("ValidationError", StringComparison.OrdinalIgnoreCase)
            || message.Contains("ValidationRule", StringComparison.OrdinalIgnoreCase));

    private static string? Value(Match match, string group = "value")
    {
        if (!match.Success)
        {
            return null;
        }

        var value = match.Groups[group].Value.Trim();
        return value.Length == 0 ? null : value;
    }

    [GeneratedRegex(
        "BindingExpression path error:\\s*'(?<value>[^']+)' property not found",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex PropertyNotFoundPath();

    [GeneratedRegex(
        "BindingExpression:Path=(?<value>[^;]+);",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex BindingExpressionPath();

    [GeneratedRegex(
        "target element is '(?<type>[^']+)' \\(Name='(?<name>[^']*)'\\)",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex TargetElement();

    [GeneratedRegex(
        "target property is '(?<value>[^']+)'",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex TargetProperty();
}

internal sealed class BindingDiagnosticAggregator
{
    private readonly object _gate = new();
    private readonly Dictionary<string, AggregateState> _aggregates = new(StringComparer.Ordinal);
    private readonly ReliabilitySensor _sensor;
    private readonly TimeSpan _window;
    private readonly int _burstThreshold;
    private readonly int _maxFingerprints;

    public BindingDiagnosticAggregator(
        ReliabilitySensor sensor,
        TimeSpan window,
        int burstThreshold,
        int maxFingerprints = 500)
    {
        _sensor = sensor;
        _window = window;
        _burstThreshold = burstThreshold;
        _maxFingerprints = maxFingerprints;
    }

    public void Accept(BindingTraceMessage trace)
    {
        var diagnostic = BindingDiagnosticParser.Parse(trace);
        if (diagnostic is null)
        {
            return;
        }

        Accept(diagnostic);
    }

    public void Accept(BindingDiagnostic diagnostic)
    {
        var pending = new List<PendingAggregate>();
        var rejected = false;
        lock (_gate)
        {
            CollectExpired(diagnostic.TimestampUtc, pending);
            var fingerprint = Fingerprint(_sensor.ApplicationVersion, diagnostic);
            if (!_aggregates.TryGetValue(fingerprint, out var aggregate))
            {
                if (_aggregates.Count >= _maxFingerprints)
                {
                    rejected = true;
                }
                else
                {
                    aggregate = new AggregateState(fingerprint, diagnostic);
                    _aggregates.Add(fingerprint, aggregate);
                }
            }

            if (aggregate is not null)
            {
                aggregate.Add(diagnostic);
                if (aggregate.Count == _burstThreshold)
                {
                    pending.Add(aggregate.Snapshot());
                    aggregate.LastEmittedCount = aggregate.Count;
                }
            }
        }

        Emit(pending);
        if (rejected)
        {
            _sensor.RecordDroppedBindingDiagnostic();
        }
    }

    public void FlushExpired(DateTimeOffset now)
    {
        var pending = new List<PendingAggregate>();
        lock (_gate)
        {
            CollectExpired(now, pending);
        }

        Emit(pending);
    }

    public static string Fingerprint(string applicationVersion, BindingDiagnostic diagnostic) =>
        CanonicalJson.Hash(JsonSerializer.SerializeToElement(new
        {
            application_version = applicationVersion.Trim(),
            category = diagnostic.Category,
            binding_path = diagnostic.BindingPath?.Trim(),
            target_property = diagnostic.TargetProperty?.Trim(),
            element_type = diagnostic.ElementType?.Trim(),
            element_name = diagnostic.ElementName?.Trim(),
        }));

    private void CollectExpired(DateTimeOffset now, List<PendingAggregate> pending)
    {
        foreach (var pair in _aggregates.Where(pair => now - pair.Value.FirstSeenUtc >= _window).ToArray())
        {
            if (pair.Value.Count != pair.Value.LastEmittedCount)
            {
                pending.Add(pair.Value.Snapshot());
            }

            _aggregates.Remove(pair.Key);
        }
    }

    private void Emit(IEnumerable<PendingAggregate> pending)
    {
        foreach (var aggregate in pending)
        {
            var correlation = JsonSerializer.SerializeToElement(new
            {
                binding_path = aggregate.Diagnostic.BindingPath,
                element_id = (string?)null,
                window_type = (string?)null,
            });
            var payload = JsonSerializer.SerializeToElement(new
            {
                fingerprint = aggregate.Fingerprint,
                category = aggregate.Diagnostic.Category,
                binding_path = aggregate.Diagnostic.BindingPath,
                target_property = aggregate.Diagnostic.TargetProperty,
                element_type = aggregate.Diagnostic.ElementType,
                element_name = aggregate.Diagnostic.ElementName,
                occurrence_count = aggregate.Count,
                first_seen_utc = aggregate.FirstSeenUtc,
                last_seen_utc = aggregate.LastSeenUtc,
                message_truncated = aggregate.WasTruncated,
            });

            if (_sensor.TryEnqueue(EventType.BindingAggregate, Severity.ERROR, correlation, payload, out _))
            {
                _sensor.RecordBindingAggregateQueued();
            }
        }
    }

    private sealed class AggregateState(string fingerprint, BindingDiagnostic diagnostic)
    {
        public string Fingerprint { get; } = fingerprint;

        public BindingDiagnostic Diagnostic { get; } = diagnostic;

        public DateTimeOffset FirstSeenUtc { get; private set; } = diagnostic.TimestampUtc;

        public DateTimeOffset LastSeenUtc { get; private set; } = diagnostic.TimestampUtc;

        public int Count { get; private set; }

        public int LastEmittedCount { get; set; }

        public bool WasTruncated { get; private set; }

        public void Add(BindingDiagnostic item)
        {
            Count++;
            FirstSeenUtc = item.TimestampUtc < FirstSeenUtc ? item.TimestampUtc : FirstSeenUtc;
            LastSeenUtc = item.TimestampUtc > LastSeenUtc ? item.TimestampUtc : LastSeenUtc;
            WasTruncated |= item.WasTruncated;
        }

        public PendingAggregate Snapshot() => new(
            Fingerprint,
            Diagnostic,
            Count,
            FirstSeenUtc,
            LastSeenUtc,
            WasTruncated);
    }

    private sealed record PendingAggregate(
        string Fingerprint,
        BindingDiagnostic Diagnostic,
        int Count,
        DateTimeOffset FirstSeenUtc,
        DateTimeOffset LastSeenUtc,
        bool WasTruncated);
}
