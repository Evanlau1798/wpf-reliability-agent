using System.Windows;

namespace Demo.BrokenWpfApp;

public partial class MainWindow : Window
{
    private readonly IReadOnlyList<PersonViewModel> _people =
        DemoDataGenerator.Generate(DemoCalibration.DefaultPersonCount, seed: 1729);
    private readonly ExperimentalPeopleGridState _featureState = new();
    private readonly RecoveryActionRegistry _recoveryActions = new();

    public MainWindow()
    {
        InitializeComponent();
        _recoveryActions.Register(
            RecoveryAction.DisableExperimentalPeopleGrid,
            _featureState.Disable);
        RefreshView();
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

    private void ResetDemo_Click(object sender, RoutedEventArgs e)
    {
        _featureState.Enable();
        RefreshView();
    }

    private void RefreshView()
    {
        var isEnabled = _featureState.IsEnabled;
        ExperimentalPeopleGrid.ItemsSource = isEnabled ? _people : null;
        ExperimentalPeopleGrid.Visibility = isEnabled ? Visibility.Visible : Visibility.Collapsed;
        FallbackView.Visibility = isEnabled ? Visibility.Collapsed : Visibility.Visible;
        FeatureStateText.Text = isEnabled ? "Feature: ENABLED (broken)" : "Feature: DISABLED (fallback)";
        ItemCountText.Text = $"Items: {_people.Count:N0}";
    }
}
