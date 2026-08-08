using System.Text.Json;
using Reliability.Contracts;

namespace Reliability.Sensor.Tests;

public sealed class SourceMapLookupTests
{
    [Fact]
    public async Task SourceLookupBindingFindsExactStableKeyAndBindingPathProperty()
    {
        var sourceMapPath = Path.Combine(
            Path.GetTempPath(),
            $"wpf-reliability-source-map-{Guid.NewGuid():N}.json");
        await File.WriteAllTextAsync(sourceMapPath, JsonSerializer.Serialize(new[]
        {
            new
            {
                key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae",
                file = "src/windows/Demo.BrokenWpfApp/MainWindow.xaml",
                line = 42,
                column = 17,
                window_type = "Demo.MainWindow",
                named_ancestors = new[] { "PeopleGrid" },
                element_type = "TextBlock",
                element_name = "PersonName",
                target_property = "Text",
                binding_path = "DisplayNmae",
                unsupported_reason = (string?)null,
                file_sha256 = new string('a', 64),
                build_commit = new string('b', 40),
                source_snippet = "<TextBlock Text=\"{Binding DisplayNmae}\" />",
                source_snippet_start_line = 42,
                source_snippet_truncated = false,
            },
            new
            {
                key = "Demo.MainWindow/PeopleGrid/PersonAge#Text|Age",
                file = "src/windows/Demo.BrokenWpfApp/MainWindow.xaml",
                line = 50,
                column = 17,
                window_type = "Demo.MainWindow",
                named_ancestors = new[] { "PeopleGrid" },
                element_type = "TextBlock",
                element_name = "PersonAge",
                target_property = "Text",
                binding_path = "Age",
                unsupported_reason = (string?)null,
                file_sha256 = new string('a', 64),
                build_commit = new string('b', 40),
                source_snippet = "<TextBlock Text=\"{Binding Age}\" />",
                source_snippet_start_line = 50,
                source_snippet_truncated = false,
            },
        }));

        try
        {
            await using var sensor = ReliabilitySensor.Start(TestOptions(sourceMapPath));
            var executor = new ReadOnlyCommandExecutor(sensor);
            var baseCommand = (await ReadCommandAsync()) with
            {
                Tool = DiagnosticTool.SourceLookupBinding,
            };

            var byKey = await executor.ExecuteAsync(baseCommand with
            {
                Arguments = JsonSerializer.SerializeToElement(new
                {
                    key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae",
                }),
            }, CancellationToken.None);
            var byBinding = await executor.ExecuteAsync(baseCommand with
            {
                Arguments = JsonSerializer.SerializeToElement(new
                {
                    binding_path = "DisplayNmae",
                    target_property = "Text",
                }),
            }, CancellationToken.None);

            AssertMatch(byKey, "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae");
            AssertMatch(byBinding, "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae");
            Assert.Equal("\"source.lookup_binding\"", JsonSerializer.Serialize(DiagnosticTool.SourceLookupBinding));
        }
        finally
        {
            File.Delete(sourceMapPath);
        }
    }

    [Fact]
    public async Task SourceLookupBindingRejectsIncompleteQuery()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions(sourceMapPath: null));
        var command = (await ReadCommandAsync()) with
        {
            Tool = DiagnosticTool.SourceLookupBinding,
            Arguments = JsonSerializer.SerializeToElement(new { binding_path = "DisplayNmae" }),
        };
        var executor = new ReadOnlyCommandExecutor(sensor);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            executor.ExecuteAsync(command, CancellationToken.None));

        Assert.Equal("Command arguments are invalid.", exception.Message);
    }

    [Fact]
    public async Task SourceLookupBindingRejectsArbitraryPathArgument()
    {
        await using var sensor = ReliabilitySensor.Start(TestOptions(sourceMapPath: null));
        var command = (await ReadCommandAsync()) with
        {
            Tool = DiagnosticTool.SourceLookupBinding,
            Arguments = JsonSerializer.SerializeToElement(new
            {
                key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae",
                path = "C:/secrets/MainWindow.xaml",
            }),
        };
        var executor = new ReadOnlyCommandExecutor(sensor);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() =>
            executor.ExecuteAsync(command, CancellationToken.None));

        Assert.Equal("Command arguments are invalid.", exception.Message);
    }

    [Theory]
    [InlineData("C:/secrets/MainWindow.xaml")]
    [InlineData("../secrets/MainWindow.xaml")]
    [InlineData("src/windows/../secrets/MainWindow.xaml")]
    public async Task SourceLookupBindingRejectsMapEntriesOutsideRepoRelativePaths(string file)
    {
        var sourceMapPath = Path.Combine(
            Path.GetTempPath(),
            $"wpf-reliability-source-map-{Guid.NewGuid():N}.json");
        await File.WriteAllTextAsync(sourceMapPath, JsonSerializer.Serialize(new[]
        {
            new
            {
                key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae",
                file,
                line = 42,
                column = 17,
                window_type = "Demo.MainWindow",
                named_ancestors = new[] { "PeopleGrid" },
                element_type = "TextBlock",
                element_name = "PersonName",
                target_property = "Text",
                binding_path = "DisplayNmae",
                unsupported_reason = (string?)null,
                file_sha256 = new string('a', 64),
                build_commit = new string('b', 40),
                source_snippet = "<TextBlock Text=\"{Binding DisplayNmae}\" />",
                source_snippet_start_line = 42,
                source_snippet_truncated = false,
            },
        }));

        try
        {
            await using var sensor = ReliabilitySensor.Start(TestOptions(sourceMapPath));

            Assert.Equal(0, sensor.SourceMapEntryCount);
        }
        finally
        {
            File.Delete(sourceMapPath);
        }
    }

    [Fact]
    public async Task SourceLookupBindingDoesNotAttributeMalformedOrStaleMaps()
    {
        var staleMap = JsonSerializer.Serialize(new[]
        {
            new
            {
                key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayName",
                file = "src/windows/Demo.BrokenWpfApp/MainWindow.xaml",
                binding_path = "DisplayName",
                target_property = "Text",
            },
        });

        foreach (var contents in new[] { "{", staleMap })
        {
            var sourceMapPath = Path.Combine(
                Path.GetTempPath(),
                $"wpf-reliability-source-map-{Guid.NewGuid():N}.json");
            await File.WriteAllTextAsync(sourceMapPath, contents);
            try
            {
                await using var sensor = ReliabilitySensor.Start(TestOptions(sourceMapPath));
                var executor = new ReadOnlyCommandExecutor(sensor);
                var command = (await ReadCommandAsync()) with
                {
                    Tool = DiagnosticTool.SourceLookupBinding,
                    Arguments = JsonSerializer.SerializeToElement(new
                    {
                        key = "Demo.MainWindow/PeopleGrid/PersonName#Text|DisplayNmae",
                    }),
                };

                var result = await executor.ExecuteAsync(command, CancellationToken.None);

                Assert.Equal(0, result.GetProperty("matches").GetArrayLength());
                Assert.DoesNotContain("\"file\"", result.GetRawText(), StringComparison.Ordinal);
                Assert.DoesNotContain("\"line\"", result.GetRawText(), StringComparison.Ordinal);
            }
            finally
            {
                File.Delete(sourceMapPath);
            }
        }
    }

    private static void AssertMatch(JsonElement result, string expectedKey)
    {
        var matches = result.GetProperty("matches");
        Assert.Equal(1, matches.GetArrayLength());
        Assert.Equal(expectedKey, matches[0].GetProperty("key").GetString());
        Assert.Equal(
            "src/windows/Demo.BrokenWpfApp/MainWindow.xaml",
            matches[0].GetProperty("file").GetString());
        Assert.Equal(42, matches[0].GetProperty("line").GetInt32());
        Assert.Equal(17, matches[0].GetProperty("column").GetInt32());
        Assert.Equal("Text", matches[0].GetProperty("target_property").GetString());
        Assert.Equal("DisplayNmae", matches[0].GetProperty("binding_path").GetString());
        Assert.Equal(
            "<TextBlock Text=\"{Binding DisplayNmae}\" />",
            matches[0].GetProperty("source_snippet").GetString());
        Assert.Equal(42, matches[0].GetProperty("source_snippet_start_line").GetInt32());
        Assert.False(matches[0].GetProperty("source_snippet_truncated").GetBoolean());
    }

    private static async Task<DiagnosticCommand> ReadCommandAsync()
    {
        var json = await File.ReadAllTextAsync(Path.Combine(
            AppContext.BaseDirectory,
            "fixtures",
            "diagnostic-command-valid-read.json"));
        return JsonSerializer.Deserialize(
            json,
            ContractJsonContext.Default.DiagnosticCommand)!;
    }

    private static ReliabilitySensorOptions TestOptions(string? sourceMapPath) => new()
    {
        ApiBaseUri = new Uri("https://reliability.example.test"),
        DeviceId = "device-test",
        DeviceToken = string.Empty,
        ApplicationId = "demo-app",
        ApplicationVersion = "1.2.3",
        DisableBackgroundPersistence = true,
        SourceMapPath = sourceMapPath ?? string.Empty,
    };
}
