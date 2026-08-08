using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class ContractFixtureTests
{
    public static TheoryData<string> HashFixtures => new()
    {
        "hash-ascii.json",
        "hash-unicode.json",
        "hash-reordered.json",
    };

    [Theory]
    [MemberData(nameof(HashFixtures))]
    public void CanonicalHashMatchesGoldenFixture(string name)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(FixturePath(name)));
        var root = document.RootElement;

        Assert.Equal(root.GetProperty("canonical").GetString(), CanonicalJson.Serialize(root.GetProperty("input")));
        Assert.Equal(root.GetProperty("sha256").GetString(), CanonicalJson.Hash(root.GetProperty("input")));
    }

    [Fact]
    public void NonFiniteNumbersAreRejected()
    {
        Assert.Throws<ArgumentException>(() => CanonicalJson.Serialize(double.NaN));
    }

    [Fact]
    public void SharedFixturesHaveExpectedValidity()
    {
        using var manifest = JsonDocument.Parse(File.ReadAllText(FixturePath("manifest.json")));

        foreach (var item in manifest.RootElement.GetProperty("cases").EnumerateArray())
        {
            var name = item.GetProperty("file").GetString()!;
            var expected = item.GetProperty("valid").GetBoolean();
            Assert.Equal(expected, ContractValidator.ValidateFixture(FixturePath(name)));
        }
    }

    [Theory]
    [InlineData("diagnostic-command-blocked-tool.json")]
    [InlineData("diagnostic-command-patch-tool.json")]
    public void UnknownCommandToolIsRejected(string name)
    {
        var json = File.ReadAllText(FixturePath(name));

        Assert.Throws<JsonException>(() => JsonSerializer.Deserialize(json, ContractJsonContext.Default.DiagnosticCommand));
    }

    [Theory]
    [InlineData("diagnostic-command-valid-read.json")]
    [InlineData("diagnostic-command-valid-mutation.json")]
    public void DiagnosticCommandsDeserializeAndValidate(string name)
    {
        var json = File.ReadAllText(FixturePath(name));
        var command = JsonSerializer.Deserialize(json, ContractJsonContext.Default.DiagnosticCommand);

        Assert.NotNull(command);
        Assert.True(ContractValidator.Validate(command, json));
    }

    [Fact]
    public void CommandResultRoundTrips()
    {
        var json = File.ReadAllText(FixturePath("command-result-success.json"));
        var result = JsonSerializer.Deserialize(json, ContractJsonContext.Default.CommandResult);

        Assert.NotNull(result);
        var roundTrip = JsonSerializer.Serialize(result, ContractJsonContext.Default.CommandResult);
        var deserialized = JsonSerializer.Deserialize(roundTrip, ContractJsonContext.Default.CommandResult);
        Assert.NotNull(deserialized);
        Assert.Equal(result with { Result = null }, deserialized with { Result = null });
        Assert.Equal(CanonicalJson.Serialize(result.Result), CanonicalJson.Serialize(deserialized.Result));
    }

    [Fact]
    public void ConflictingResultFixtureHasDistinctHash()
    {
        var original = JsonSerializer.Deserialize(
            File.ReadAllText(FixturePath("command-result-success.json")),
            ContractJsonContext.Default.CommandResult);
        var conflict = JsonSerializer.Deserialize(
            File.ReadAllText(FixturePath("command-result-conflicting.json")),
            ContractJsonContext.Default.CommandResult);

        Assert.NotNull(original);
        Assert.NotNull(conflict);
        Assert.Equal(original.CommandId, conflict.CommandId);
        Assert.NotEqual(original.ResultHash, conflict.ResultHash);
    }

    private static string FixturePath(string name) => Path.Combine(AppContext.BaseDirectory, "fixtures", name);
}
