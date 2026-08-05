using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Web.Pages.Student;
using Xunit;

namespace FYPilot.Tests.Roadmap;

/// <summary>
/// RoadmapModel.ClassifyRoadmapOutcome is the single source of truth for
/// Roadmap result messaging -- both the POST-time flash message
/// (OnPostGenerateAsync) and the persisted-reload banner (Roadmap.cshtml)
/// derive their wording from it, so they can never contradict each other.
/// Root cause this protects against: the previous unconditional
/// TempData["Success"] = $"AI roadmap with {N} phases generated." toast,
/// which fired whenever response.Roadmap was non-null regardless of
/// whether real AI generation actually happened.
/// </summary>
public class RoadmapOutcomeClassificationTests
{
    private static ProjectRoadmapDto MinimalRoadmap() =>
        new("Test Roadmap", 8, "medium", "solo strategy", [], "final advice");

    private static AiQualityPassportDto Review(
        string status, bool usable, string decisionReason = "", string? outputOrigin = null) =>
        new(
            Status: status, Usable: usable, ReviewUnavailable: status == "review_unavailable",
            Warning: "", QualityScore: usable ? 90 : null, Strengths: [], Issues: [],
            DecisionReason: decisionReason, Attempts: 1, ReviewerVersion: "v1", ReviewRunId: "run-1",
            OutputOrigin: outputOrigin);

    private static ProjectRoadmapServiceResponse Response(
        bool llmUsed,
        bool fallbackUsed,
        string? outputOrigin,
        AiQualityPassportDto? review,
        string? fallbackReasonCode = null) =>
        new(
            Roadmap: MinimalRoadmap(), Agent: "ProjectRoadmapAgent", LlmUsed: llmUsed,
            Source: llmUsed ? "deepinfra" : "dynamic-fallback",
            OllamaError: null, OllamaRawPreview: null, GeneratedAt: DateTime.UtcNow,
            Message: "Project roadmap generated successfully", Review: review,
            Provider: "deepinfra", ModelUsed: "test-model",
            OutputOrigin: outputOrigin, FallbackUsed: fallbackUsed,
            FallbackReasonCode: fallbackReasonCode,
            FallbackReasonMessage: fallbackReasonCode != null ? "fallback reason message" : null);

    private static AiOutputReview PersistedReview(string status, bool usable, string decisionReason = "") =>
        new()
        {
            Status = status,
            Usable = usable,
            DecisionReason = decisionReason,
            GeneratorProvider = "deepinfra",
            GeneratorModel = "test-model",
            CreatedAt = DateTime.UtcNow,
        };

    // ---- Test 1: accepted AI result -----------------------------------

    [Fact]
    public void AcceptedAiResult_IsClassifiedAsSuccessAndMayClaimAiGenerated()
    {
        var response = Response(
            llmUsed: true, fallbackUsed: false, outputOrigin: "ai_generated",
            review: Review("approved", usable: true));

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.Equal(RoadmapOutcome.AcceptedAi, outcome.Outcome);
        Assert.Equal(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.True(outcome.IsAiGenerated);
        Assert.False(outcome.IsFallbackDisplayed);
        Assert.Contains("generated", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 2: deterministic fallback --------------------------------

    [Fact]
    public void DeterministicFallback_IsWarningAndNeverClaimsAcceptedAiResult()
    {
        var response = Response(
            llmUsed: false, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review("provider_unavailable", usable: false));

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.False(outcome.IsAiGenerated);
        Assert.True(outcome.IsFallbackDisplayed);
        Assert.Contains("fallback", outcome.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("accepted", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 3: writer deadline exceeded ------------------------------

    [Fact]
    public void WriterDeadlineExceeded_IsClassifiedSpecifically_NotAsGenericProviderFailure()
    {
        var response = Response(
            llmUsed: false, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review("provider_unavailable", usable: false),
            fallbackReasonCode: "writer_deadline_exceeded");

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.Equal(RoadmapOutcome.WriterDeadlineExceeded, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.DoesNotContain("generated successfully", outcome.Message, StringComparison.OrdinalIgnoreCase);
        Assert.True(outcome.IsFallbackDisplayed);
        Assert.Contains("time", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 4: review unavailable ------------------------------------

    [Fact]
    public void ReviewUnavailable_IsDistinctFromDeadlineAndProviderFailure()
    {
        var response = Response(
            llmUsed: false, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review("review_unavailable", usable: false),
            fallbackReasonCode: "reviewer_unavailable");

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.Equal(RoadmapOutcome.ReviewUnavailable, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcome.WriterDeadlineExceeded, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcome.ProviderFailure, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
    }

    // ---- Test 5: review rejected ----------------------------------------

    [Fact]
    public void ReviewRejected_StatesQualityFailure_NotSuccess()
    {
        var response = Response(
            llmUsed: false, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review("rejected", usable: false),
            fallbackReasonCode: "semantic_rewrite_failed");

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.Equal(RoadmapOutcome.ReviewRejected, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.Contains("quality review", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 6: conflicting fields fail safely -------------------------

    [Fact]
    public void ConflictingFields_StrongFallbackProvenanceWins_NeverClassifiedAcceptedAi()
    {
        // LlmUsed=true AND Usable=true both look like success, but
        // OutputOrigin/FallbackUsed say otherwise -- the stronger fallback
        // evidence must win, exactly matching what the router actually
        // produces for a build_safe_fallback() response (its Provider/
        // ModelUsed describe the FAILED attempt, not "no AI was used").
        var response = Response(
            llmUsed: true, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review("provider_unavailable", usable: true));

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.NotEqual(RoadmapOutcome.AcceptedAi, outcome.Outcome);
        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.True(outcome.IsFallbackDisplayed);
        Assert.False(outcome.IsAiGenerated);
    }

    // ---- Test 7: no response / invalid response --------------------------

    [Fact]
    public void NullResponse_IsErrorState_WithSafeGenericMessage()
    {
        var outcome = RoadmapModel.ClassifyRoadmapOutcome((ProjectRoadmapServiceResponse?)null);

        Assert.Equal(RoadmapOutcome.InvalidResponse, outcome.Outcome);
        Assert.Equal(RoadmapOutcomeStyle.Error, outcome.Style);
        Assert.False(outcome.IsAiGenerated);
        Assert.DoesNotContain("Exception", outcome.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("generated successfully", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void ResponseWithNullRoadmap_IsAlsoInvalidResponse()
    {
        var response = new ProjectRoadmapServiceResponse(
            Roadmap: null!, Agent: "ProjectRoadmapAgent", LlmUsed: false, Source: "dynamic-fallback",
            OllamaError: "boom", OllamaRawPreview: null, GeneratedAt: DateTime.UtcNow,
            Message: "failed");

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.Equal(RoadmapOutcome.InvalidResponse, outcome.Outcome);
        Assert.Equal(RoadmapOutcomeStyle.Error, outcome.Style);
    }

    // ---- Test 8: exact regression for the old unconditional message -----

    [Theory]
    [InlineData("writer_deadline_exceeded", "provider_unavailable")]
    [InlineData("reviewer_unavailable", "review_unavailable")]
    [InlineData("semantic_rewrite_failed", "rejected")]
    [InlineData("provider_timeout", "provider_unavailable")]
    [InlineData(null, "provider_unavailable")]
    public void NonAcceptedOutcomes_NeverProduceTheOldUnconditionalSuccessText(
        string? fallbackReasonCode, string reviewStatus)
    {
        const string previousUnconditionalText = "AI roadmap with";

        var response = Response(
            llmUsed: false, fallbackUsed: true, outputOrigin: "deterministic_fallback",
            review: Review(reviewStatus, usable: false),
            fallbackReasonCode: fallbackReasonCode);

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(response);

        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.DoesNotContain(previousUnconditionalText, outcome.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(previousUnconditionalText, outcome.Title, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("generated successfully", outcome.Message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Persisted-reload overload (AiOutputReview) ----------------------

    [Fact]
    public void PersistedReview_Usable_ClassifiesAsAcceptedAi()
    {
        var review = PersistedReview("approved", usable: true);

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(review);

        Assert.Equal(RoadmapOutcome.AcceptedAi, outcome.Outcome);
        Assert.Equal(RoadmapOutcomeStyle.Success, outcome.Style);
    }

    [Fact]
    public void PersistedReview_NotUsable_ClassifiesAsFallbackNotSuccess()
    {
        var review = PersistedReview("provider_unavailable", usable: false);

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(review);

        Assert.NotEqual(RoadmapOutcomeStyle.Success, outcome.Style);
        Assert.True(outcome.IsFallbackDisplayed);
        Assert.False(outcome.IsAiGenerated);
    }

    [Fact]
    public void PersistedReview_RejectedStatus_ClassifiesAsReviewRejected()
    {
        var review = PersistedReview("rejected", usable: false);

        var outcome = RoadmapModel.ClassifyRoadmapOutcome(review);

        Assert.Equal(RoadmapOutcome.ReviewRejected, outcome.Outcome);
    }
}
