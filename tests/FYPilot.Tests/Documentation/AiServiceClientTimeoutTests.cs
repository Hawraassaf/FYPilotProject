using System.Reflection;
using FYPilot.Infrastructure.Services;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace FYPilot.Tests.Documentation;

/// <summary>
/// Verifies AiServiceClient gives SE Documentation its own, longer-lived
/// HttpClient (1260s) without changing the shared client (600s) every other
/// agent (Roadmap, Idea Generator, Mentor Chat, etc.) still uses -- see the
/// class-level comment on AiServiceClient's _seDocumentationHttp field for
/// why HttpClient.Timeout can't just be a per-request CancellationToken
/// override here.
/// </summary>
public class AiServiceClientTimeoutTests
{
    // Python's coordinated GLOBAL total: SEDocumentationAgent's
    // ReviewPipeline max_total_seconds (app/review/registry.py) == 1200s.
    // The Writer itself is bounded tighter (900s = global - the 300s
    // semantic-review reserve), but .NET's timeout must exceed the FULL
    // global deadline, since that's what ReviewPipeline itself enforces
    // end to end.
    private const int PythonSeDocumentationTotalDeadlineSeconds = 1200;

    private static AiServiceClient NewClient()
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["AiService:BaseUrl"] = "http://localhost:8000",
                ["AiService:InternalApiKey"] = "test-key",
            })
            .Build();

        return new AiServiceClient(configuration, NullLogger<AiServiceClient>.Instance);
    }

    private static HttpClient GetPrivateHttpClient(AiServiceClient client, string fieldName)
    {
        var field = typeof(AiServiceClient).GetField(fieldName, BindingFlags.NonPublic | BindingFlags.Instance);
        Assert.NotNull(field);
        var value = field!.GetValue(client) as HttpClient;
        Assert.NotNull(value);
        return value!;
    }

    [Fact]
    public void SeDocumentationHttpClient_HasA1260SecondTimeout()
    {
        var client = NewClient();
        var seDocHttp = GetPrivateHttpClient(client, "_seDocumentationHttp");

        Assert.Equal(TimeSpan.FromSeconds(1260), seDocHttp.Timeout);
    }

    [Fact]
    public void SeDocumentationTimeout_IsLongerThanPythonsTotalDeadline()
    {
        // Requirement: .NET must never abandon a request while Python is
        // still legitimately working on it.
        var client = NewClient();
        var seDocHttp = GetPrivateHttpClient(client, "_seDocumentationHttp");

        Assert.True(
            seDocHttp.Timeout > TimeSpan.FromSeconds(PythonSeDocumentationTotalDeadlineSeconds),
            $"Expected the .NET SE Documentation timeout ({seDocHttp.Timeout}) to exceed " +
            $"Python's {PythonSeDocumentationTotalDeadlineSeconds}s total deadline.");
    }

    [Fact]
    public void SharedHttpClient_Keeps600SecondTimeout_UnaffectedBySeDocumentationChange()
    {
        // Every other agent (Roadmap, Idea Generator, Mentor Chat, Idea
        // Comparison, Project DNA, ...) goes through the shared _http
        // client -- confirms adding a dedicated SE Documentation client
        // didn't change their timeout.
        var client = NewClient();
        var sharedHttp = GetPrivateHttpClient(client, "_http");

        Assert.Equal(TimeSpan.FromSeconds(600), sharedHttp.Timeout);
    }

    [Fact]
    public void SeDocumentationAndSharedClients_AreGenuinelyDifferentInstances()
    {
        var client = NewClient();
        var sharedHttp = GetPrivateHttpClient(client, "_http");
        var seDocHttp = GetPrivateHttpClient(client, "_seDocumentationHttp");

        Assert.NotSame(sharedHttp, seDocHttp);
    }

    [Fact]
    public void BothClients_CarryTheInternalApiKeyHeader()
    {
        var client = NewClient();
        var sharedHttp = GetPrivateHttpClient(client, "_http");
        var seDocHttp = GetPrivateHttpClient(client, "_seDocumentationHttp");

        Assert.True(sharedHttp.DefaultRequestHeaders.Contains("X-Internal-Api-Key"));
        Assert.True(seDocHttp.DefaultRequestHeaders.Contains("X-Internal-Api-Key"));
    }
}
