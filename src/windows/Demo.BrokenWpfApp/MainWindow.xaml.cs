using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using Reliability.Sensor;

namespace Demo.BrokenWpfApp;

public partial class MainWindow : Window
{
    private readonly IReadOnlyList<PersonViewModel> _people =
        DemoDataGenerator.Generate(DemoCalibration.DefaultPersonCount, seed: 1729);
    private readonly ExperimentalPeopleGridState _featureState = new();
    private readonly RecoveryActionRegistry _recoveryActions = new();
    private readonly DispatcherTimer _renderingFaultTimer;
    private readonly ReliabilitySensor? _sensor;
    private bool _bindingProbeCompleted;

    public MainWindow() : this(null)
    {
    }

    public MainWindow(ReliabilitySensor? sensor)
    {
        _sensor = sensor;
        InitializeComponent();
        _renderingFaultTimer = new DispatcherTimer(
            TimeSpan.FromMilliseconds(DemoCalibration.RenderingFaultIntervalMilliseconds),
            DispatcherPriority.Render,
            (_, _) => Thread.Sleep(DemoCalibration.RenderingFaultDelayMilliseconds),
            Dispatcher);
        _renderingFaultTimer.Start();
        Closed += (_, _) => _renderingFaultTimer.Stop();
        _recoveryActions.Register(
            RecoveryAction.DisableExperimentalPeopleGrid,
            _featureState.Disable);
        RefreshView();
        if (_sensor is not null)
        {
            _sensor.RecoveryActions.Register(
                RecoveryAction.DisableExperimentalPeopleGrid,
                Dispatcher,
                expectedCurrentState => ApplyRecoveryAction(
                    RecoveryAction.DisableExperimentalPeopleGrid,
                    expectedCurrentState));
            Dispatcher.BeginInvoke(ProbeBrokenBindings, DispatcherPriority.Loaded);
        }
    }

    public RecoveryResult ApplyRecoveryAction(
        RecoveryAction action,
        bool expectedCurrentState)
    {
        var result = _recoveryActions.Execute(action, expectedCurrentState);
        if (Dispatcher.CheckAccess())
        {
            RefreshView();
        }
        else
        {
            Dispatcher.Invoke(RefreshView);
        }

        return result;
    }

    public bool IsRenderingFaultActive => _renderingFaultTimer.IsEnabled;

    private void ResetDemo_Click(object sender, RoutedEventArgs e)
    {
        _featureState.Enable();
        _renderingFaultTimer.Start();
        RefreshView();
    }

    private void RefreshView()
    {
        var isEnabled = _featureState.IsEnabled;
        if (!isEnabled)
        {
            _renderingFaultTimer.Stop();
        }
        ExperimentalPeopleGrid.ItemsSource = isEnabled ? _people : null;
        ExperimentalPeopleGrid.Visibility = isEnabled ? Visibility.Visible : Visibility.Collapsed;
        FallbackView.Visibility = isEnabled ? Visibility.Collapsed : Visibility.Visible;
        FeatureStateText.Text = isEnabled ? "Feature: ENABLED (broken)" : "Feature: DISABLED (fallback)";
        ItemCountText.Text = $"Items: {_people.Count:N0}";
    }

    private void ProbeBrokenBindings(DependencyObject root, int maxNodes)
    {
        // ponytail: this bounded demo fallback covers machines where WPF managed tracing is disabled.
        var pending = new Stack<DependencyObject>();
        pending.Push(root);
        for (var visited = 0; pending.Count > 0 && visited < maxNodes; visited++)
        {
            var current = pending.Pop();
            if (current is TextBlock textBlock
                && textBlock.GetBindingExpression(TextBlock.TextProperty) is { } expression
                && expression.ParentBinding.Path?.Path is { Length: > 0 } bindingPath
                && !bindingPath.Contains('.')
                && textBlock.DataContext is { } dataItem
                && dataItem.GetType().GetProperty(bindingPath) is null)
            {
                _sensor!.ReportBindingFailure(
                    textBlock,
                    bindingPath,
                    TextBlock.TextProperty.Name,
                    string.IsNullOrEmpty(textBlock.Name) ? null : textBlock.Name);
            }

            for (var childIndex = VisualTreeHelper.GetChildrenCount(current) - 1; childIndex >= 0; childIndex--)
            {
                pending.Push(VisualTreeHelper.GetChild(current, childIndex));
            }
        }
    }

    private void ProbeBrokenBindings()
    {
        if (_bindingProbeCompleted || _sensor is null || _sensor.BindingAggregateCount > 0)
        {
            return;
        }

        _bindingProbeCompleted = true;
        ProbeBrokenBindings(ExperimentalPeopleGrid, maxNodes: 300);
    }
}
