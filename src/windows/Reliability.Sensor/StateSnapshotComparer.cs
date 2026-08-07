using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor;

internal static class StateSnapshotComparer
{
    public static JsonElement Compare(JsonElement before, JsonElement after)
    {
        if (before.ValueKind is not JsonValueKind.Object || after.ValueKind is not JsonValueKind.Object)
        {
            throw new InvalidOperationException("Snapshot arguments must be JSON objects.");
        }

        var changes = new List<SnapshotChange>();
        Collect(string.Empty, before, after, changes);
        return JsonSerializer.SerializeToElement(new
        {
            changed = changes.Count > 0,
            changes = changes.Select(change => new
            {
                path = change.Path,
                before = change.Before,
                after = change.After,
                delta = change.Delta,
            }),
        });
    }

    private static void Collect(
        string path,
        JsonElement? before,
        JsonElement? after,
        List<SnapshotChange> changes)
    {
        if (before is { ValueKind: JsonValueKind.Object } beforeObject
            && after is { ValueKind: JsonValueKind.Object } afterObject)
        {
            var names = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var property in beforeObject.EnumerateObject())
            {
                names.Add(property.Name);
            }
            foreach (var property in afterObject.EnumerateObject())
            {
                names.Add(property.Name);
            }

            foreach (var name in names)
            {
                var childPath = path.Length == 0 ? name : $"{path}.{name}";
                Collect(
                    childPath,
                    beforeObject.TryGetProperty(name, out var beforeValue) ? beforeValue : null,
                    afterObject.TryGetProperty(name, out var afterValue) ? afterValue : null,
                    changes);
            }
            return;
        }

        if (before is JsonElement beforeValueElement
            && after is JsonElement afterValueElement
            && string.Equals(
                CanonicalJson.Hash(beforeValueElement),
                CanonicalJson.Hash(afterValueElement),
                StringComparison.Ordinal))
        {
            return;
        }

        double? delta = null;
        if (before is { ValueKind: JsonValueKind.Number } beforeNumber
            && after is { ValueKind: JsonValueKind.Number } afterNumber
            && beforeNumber.TryGetDouble(out var beforeDouble)
            && afterNumber.TryGetDouble(out var afterDouble))
        {
            delta = afterDouble - beforeDouble;
        }

        changes.Add(new SnapshotChange(
            path,
            before?.Clone(),
            after?.Clone(),
            delta));
    }

    private sealed record SnapshotChange(
        string Path,
        JsonElement? Before,
        JsonElement? After,
        double? Delta);
}
