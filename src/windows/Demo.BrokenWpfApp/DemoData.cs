using System.Security.Cryptography;
using System.Text;

namespace Demo.BrokenWpfApp;

public static class DemoCalibration
{
    // ponytail: This non-virtualized row and animation ceiling is demo-only; tune it only when three-run frame p95 no longer separates broken and fallback views.
    public const int DefaultPersonCount = 1_500;
    public const int AnimatedRowCount = 8;
}

public sealed record PersonViewModel(
    int Id,
    string DisplayName,
    string Department,
    bool IsAnimated);

public static class DemoDataGenerator
{
    private static readonly string[] GivenNames =
    [
        "Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Harper", "Jordan"
    ];

    private static readonly string[] FamilyNames =
    [
        "Chen", "Garcia", "Johnson", "Kim", "Patel", "Rivera", "Smith", "Williams"
    ];

    private static readonly string[] Departments =
    [
        "Engineering", "Finance", "Operations", "Product", "Support"
    ];

    public static IReadOnlyList<PersonViewModel> Generate(int count, int seed)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(count);

        var random = new Random(seed);
        var people = new List<PersonViewModel>(count);

        for (var index = 0; index < count; index++)
        {
            var displayName = $"{GivenNames[random.Next(GivenNames.Length)]} {FamilyNames[random.Next(FamilyNames.Length)]}";
            people.Add(new PersonViewModel(
                index + 1,
                displayName,
                Departments[random.Next(Departments.Length)],
                index < DemoCalibration.AnimatedRowCount));
        }

        return people;
    }

    public static string Summarize(IEnumerable<PersonViewModel> people)
    {
        ArgumentNullException.ThrowIfNull(people);

        var summary = new StringBuilder();
        foreach (var person in people)
        {
            summary.Append(person.Id)
                .Append('|')
                .Append(person.DisplayName)
                .Append('|')
                .Append(person.Department)
                .Append('|')
                .Append(person.IsAnimated)
                .Append('\n');
        }

        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(summary.ToString())));
    }
}
