using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.DTOs.Documentation;
using Xunit;

namespace FYPilot.Tests.Documentation;

/// <summary>
/// Verifies AiSeDocumentationServiceResponse deserializes real Python
/// /generate-se-documentation payloads correctly -- using the exact
/// case-insensitive JsonSerializerOptions AiServiceClient.PostAsync uses in
/// production, not a hand-tuned test-only configuration.
/// </summary>
public class AiSeDocumentationDtoDeserializationTests
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private static string ReadFixture(string fileName) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", fileName));

    // 11. A real, live-captured Python response (full provider-chain
    // exhaustion: DeepInfra truncated, Ollama timed out, Groq hit its daily
    // quota -- captured 2026-08-03 against a locally running AI service)
    // deserializes correctly and every field the acceptance policy depends
    // on comes through with the right value.
    [Fact]
    public void LiveCapturedFallbackResponse_DeserializesCorrectly()
    {
        var json = ReadFixture("se_documentation_response_live_fallback.json");

        var response = JsonSerializer.Deserialize<AiSeDocumentationServiceResponse>(json, JsonOpts);

        Assert.NotNull(response);
        Assert.False(response!.LlmUsed);
        Assert.Equal("dynamic-fallback", response.Source);
        Assert.NotNull(response.Review);
        Assert.Equal("provider_unavailable", response.Review!.Status);
        Assert.False(response.Review.Usable);
        Assert.False(response.Review.Displayable);
        Assert.Equal("none", response.Review.OutputOrigin);
        Assert.Equal("none", response.Review.OutputReviewLevel);
        Assert.NotNull(response.Documentation);
        Assert.Equal("Arabic Medical Symptom Triage Assistant", response.Documentation!.ProjectTitle);
    }

    // 12. New review fields (displayable, outputOrigin, outputReviewLevel)
    // deserialize correctly for a successful generation, and the shape
    // matches what DocumentationGeneratorService.IsRealAiOutput requires.
    [Fact]
    public void SuccessResponse_DeserializesNewReviewFields()
    {
        var json = ReadFixture("se_documentation_response_success.json");

        var response = JsonSerializer.Deserialize<AiSeDocumentationServiceResponse>(json, JsonOpts);

        Assert.NotNull(response);
        Assert.True(response!.LlmUsed);
        Assert.Equal("deepinfra", response.Source);
        Assert.NotNull(response.Review);
        Assert.True(response.Review!.Usable);
        Assert.True(response.Review.Displayable);
        Assert.Equal("writer", response.Review.OutputOrigin);
        Assert.Equal("approved", response.Review.OutputReviewLevel);
        Assert.NotNull(response.Documentation);
        Assert.Single(response.Documentation!.FunctionalRequirements);
        Assert.Equal("provider", response.Documentation.SectionProvenance?["requirements"]);
    }

    // Older/partial payloads without the new fields still deserialize --
    // Displayable/OutputOrigin/OutputReviewLevel are additive with defaults,
    // so Mentor Chat/Roadmap responses that predate this schema change keep
    // working unchanged.
    [Fact]
    public void ReviewWithoutNewFields_StillDeserializes_WithSafeDefaults()
    {
        const string json = """
        {
          "status": "approved",
          "usable": true,
          "reviewUnavailable": false,
          "warning": "",
          "qualityScore": 90,
          "strengths": [],
          "issues": [],
          "decisionReason": "ok",
          "attempts": 1,
          "reviewerVersion": "review-pipeline-v1",
          "reviewRunId": "x"
        }
        """;

        var review = JsonSerializer.Deserialize<AiQualityPassportDto>(json, JsonOpts);

        Assert.NotNull(review);
        Assert.True(review!.Usable);
        Assert.False(review.Displayable);
        Assert.Null(review.OutputOrigin);
        Assert.Null(review.OutputReviewLevel);
    }
}
