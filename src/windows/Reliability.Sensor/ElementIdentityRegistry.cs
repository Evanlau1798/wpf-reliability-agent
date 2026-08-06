using System.Runtime.CompilerServices;

namespace Reliability.Sensor;

internal sealed class ElementIdentityRegistry(string appSessionId)
{
    private const int MaxLookupEntries = 1_000;
    private readonly object _gate = new();
    private readonly ConditionalWeakTable<object, ElementIdentity> _identities = new();
    private readonly Dictionary<string, WeakReference<object>> _lookup = new(StringComparer.Ordinal);
    private readonly Queue<string> _lookupOrder = new();
    private long _nextId;

    public string GetOrCreate(object element)
    {
        ArgumentNullException.ThrowIfNull(element);
        lock (_gate)
        {
            if (!_identities.TryGetValue(element, out var identity))
            {
                identity = new ElementIdentity($"element-{appSessionId}-{Interlocked.Increment(ref _nextId)}");
                _identities.Add(element, identity);
            }

            if (!_lookup.ContainsKey(identity.Value))
            {
                AddLookup(identity.Value, element);
            }

            return identity.Value;
        }
    }

    public bool TryResolve<T>(string elementId, out T? element) where T : class
    {
        lock (_gate)
        {
            if (_lookup.TryGetValue(elementId, out var reference)
                && reference.TryGetTarget(out var target)
                && target is T typed)
            {
                element = typed;
                return true;
            }

            _lookup.Remove(elementId);
            element = null;
            return false;
        }
    }

    private void AddLookup(string elementId, object element)
    {
        while (_lookup.Count >= MaxLookupEntries && _lookupOrder.TryDequeue(out var oldest))
        {
            _lookup.Remove(oldest);
        }

        _lookup[elementId] = new WeakReference<object>(element);
        _lookupOrder.Enqueue(elementId);
    }

    private sealed record ElementIdentity(string Value);
}
