using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Reliability.Contracts;

namespace Reliability.Sensor;

public sealed record UiTreeOptions
{
    public int MaxDepth { get; init; } = 4;

    public int MaxNodes { get; init; } = 300;

    public int MaxChildrenPerNode { get; init; } = 50;
}

public sealed record UiDiagnosticError(string Code, string Message);

public sealed record UiTreeNode(
    string ElementId,
    string? ParentId,
    string Type,
    string? Name,
    int Depth,
    int ChildCount,
    bool IsVisible,
    bool IsEnabled,
    bool HasBindingError);

public sealed record UiTreeSnapshotResult(
    bool Succeeded,
    IReadOnlyList<UiTreeNode> Nodes,
    bool Truncated,
    int OmittedNodeCount,
    UiDiagnosticError? Error);

public sealed record UiLayoutDetails(double ActualWidth, double ActualHeight);

public sealed record UiBindingSummary(bool HasKnownError, string? ErrorPath);

public sealed record UiElementDetails(
    string ElementId,
    string Type,
    string? Name,
    bool IsVisible,
    bool IsEnabled,
    UiLayoutDetails Layout,
    UiBindingSummary BindingSummary);

public sealed record UiElementDetailsResult(
    bool Succeeded,
    UiElementDetails? Details,
    UiDiagnosticError? Error);

internal static class UiTreeSnapshotter
{
    public static UiTreeSnapshotResult Capture(
        DependencyObject? root,
        UiTreeOptions options,
        ElementIdentityRegistry identities,
        Func<string, bool> hasBindingError)
    {
        if (!IsValid(options))
        {
            return Error("INVALID_UI_TREE_OPTIONS", "UI tree budgets exceed the allowed range.");
        }

        if (root is null)
        {
            return Error("UI_ROOT_NOT_FOUND", "No WPF root element is available.");
        }

        var nodes = new List<UiTreeNode>(Math.Min(options.MaxNodes, 300));
        var pending = new Stack<PendingNode>();
        pending.Push(new PendingNode(root, null, 0));
        var omitted = 0;
        while (pending.Count > 0 && nodes.Count < options.MaxNodes)
        {
            var current = pending.Pop();
            var elementId = identities.GetOrCreate(current.Element);
            var childCount = VisualTreeHelper.GetChildrenCount(current.Element);
            nodes.Add(CreateNode(current, elementId, childCount, hasBindingError(elementId)));

            if (current.Depth >= options.MaxDepth)
            {
                omitted += childCount;
                continue;
            }

            var includedChildren = Math.Min(childCount, options.MaxChildrenPerNode);
            omitted += childCount - includedChildren;
            for (var index = includedChildren - 1; index >= 0; index--)
            {
                pending.Push(new PendingNode(
                    VisualTreeHelper.GetChild(current.Element, index),
                    elementId,
                    current.Depth + 1));
            }
        }

        if (pending.Count > 0)
        {
            omitted += pending.Count;
        }

        return new UiTreeSnapshotResult(true, nodes, omitted > 0, omitted, null);
    }

    private static UiTreeNode CreateNode(PendingNode pending, string elementId, int childCount, bool hasBindingError)
    {
        var frameworkElement = pending.Element as FrameworkElement;
        var uiElement = pending.Element as UIElement;
        return new UiTreeNode(
            elementId,
            pending.ParentId,
            pending.Element.GetType().Name,
            string.IsNullOrWhiteSpace(frameworkElement?.Name) ? null : frameworkElement.Name,
            pending.Depth,
            childCount,
            uiElement?.IsVisible ?? false,
            uiElement?.IsEnabled ?? false,
            hasBindingError);
    }

    private static bool IsValid(UiTreeOptions options) =>
        options.MaxDepth is >= 0 and <= 8
        && options.MaxNodes is >= 1 and <= 500
        && options.MaxChildrenPerNode is >= 1 and <= 100;

    private static UiTreeSnapshotResult Error(string code, string message) =>
        new(false, [], false, 0, new UiDiagnosticError(code, message));

    private sealed record PendingNode(DependencyObject Element, string? ParentId, int Depth);
}

public sealed partial class ReliabilitySensor
{
    private static readonly HashSet<string> AllowedElementDetailFields = new(StringComparer.Ordinal)
    {
        "type", "name", "visibility", "enabled", "layout", "binding_summary",
    };
    private readonly object _bindingErrorElementLock = new();
    private readonly HashSet<string> _bindingErrorElementIds = new(StringComparer.Ordinal);

    public bool ReportBindingFailure(
        DependencyObject element,
        string bindingPath,
        string targetProperty,
        string? elementName = null)
    {
        ArgumentNullException.ThrowIfNull(element);
        if (!ReportBindingFailure(bindingPath, targetProperty, element.GetType().Name, elementName))
        {
            return false;
        }

        var elementId = GetElementId(element);
        lock (_bindingErrorElementLock)
        {
            if (_bindingErrorElementIds.Count < 500)
            {
                _bindingErrorElementIds.Add(elementId);
            }
        }

        return true;
    }

    public async Task<UiTreeSnapshotResult> CaptureUiTreeAsync(
        DependencyObject? root = null,
        UiTreeOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        var dispatcher = root?.Dispatcher ?? Application.Current?.Dispatcher;
        if (dispatcher is null)
        {
            return UiTreeSnapshotter.Capture(null, options ?? new UiTreeOptions(), _elementIds, HasBindingError);
        }

        UiTreeSnapshotResult Capture()
        {
            var resolvedRoot = root ?? Application.Current?.MainWindow;
            return UiTreeSnapshotter.Capture(
                resolvedRoot,
                options ?? new UiTreeOptions(),
                _elementIds,
                HasBindingError);
        }

        var result = dispatcher.CheckAccess()
            ? Capture()
            : await dispatcher.InvokeAsync(Capture).Task.WaitAsync(cancellationToken).ConfigureAwait(false);
        if (result.Succeeded)
        {
            TryEnqueue(
                EventType.UiSnapshot,
                Severity.INFO,
                JsonSerializer.SerializeToElement(new { root_element_id = result.Nodes.FirstOrDefault()?.ElementId }),
                JsonSerializer.SerializeToElement(new
                {
                    nodes = result.Nodes,
                    truncated = result.Truncated,
                    omitted_node_count = result.OmittedNodeCount,
                }),
                out _);
        }

        return result;
    }

    public async Task<UiElementDetailsResult> GetUiElementDetailsAsync(
        string elementId,
        IReadOnlyCollection<string>? requestedFields = null,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(elementId) || elementId.Length > 128)
        {
            return DetailsError("ELEMENT_NOT_FOUND", "The element does not exist in the current application session.");
        }

        if (requestedFields is not null && requestedFields.Any(field => !AllowedElementDetailFields.Contains(field)))
        {
            return DetailsError("POLICY_DENIED", "Only allowlisted UI element detail fields may be requested.");
        }

        if (!_elementIds.TryResolve<DependencyObject>(elementId, out var element))
        {
            return DetailsError("ELEMENT_NOT_FOUND", "The element does not exist in the current application session.");
        }

        UiElementDetailsResult Capture() => CreateDetails(elementId, element!);
        return element!.Dispatcher.CheckAccess()
            ? Capture()
            : await element.Dispatcher.InvokeAsync(Capture).Task.WaitAsync(cancellationToken).ConfigureAwait(false);
    }

    private bool HasBindingError(string elementId)
    {
        lock (_bindingErrorElementLock)
        {
            return _bindingErrorElementIds.Contains(elementId);
        }
    }

    private UiElementDetailsResult CreateDetails(string elementId, DependencyObject element)
    {
        var frameworkElement = element as FrameworkElement;
        var uiElement = element as UIElement;
        return new UiElementDetailsResult(
            true,
            new UiElementDetails(
                elementId,
                element.GetType().Name,
                string.IsNullOrWhiteSpace(frameworkElement?.Name) ? null : frameworkElement.Name,
                uiElement?.IsVisible ?? false,
                uiElement?.IsEnabled ?? false,
                new UiLayoutDetails(frameworkElement?.ActualWidth ?? 0, frameworkElement?.ActualHeight ?? 0),
                new UiBindingSummary(HasBindingError(elementId), null)),
            null);
    }

    private static UiElementDetailsResult DetailsError(string code, string message) =>
        new(false, null, new UiDiagnosticError(code, message));
}
