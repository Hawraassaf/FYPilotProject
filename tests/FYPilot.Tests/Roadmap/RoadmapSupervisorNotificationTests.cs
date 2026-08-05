using FYPilot.Application.DTOs;
using FYPilot.Web.Pages.Student;
using Xunit;

namespace FYPilot.Tests.Roadmap;

/// <summary>
/// RoadmapModel.BuildSupervisorRoadmapNotification maps the SAME centralized
/// RoadmapOutcomeDescription used for the student flash message/banner (see
/// RoadmapOutcomeClassificationTests) into supervisor-notification wording.
/// Root cause this protects against: NotifySupervisorAsync used to be called
/// with unconditional "A new AI roadmap with N phase(s) was generated for
/// the project." wording BEFORE the outcome was even classified, so a
/// deterministic fallback, a rejected candidate, or a writer-deadline/
/// review-unavailable case notified the supervisor as if a real accepted AI
/// roadmap had been produced, while the student simultaneously saw an
/// honest fallback warning.
///
/// These tests exercise the mapping helper directly -- NOT the classifier
/// itself (already covered by RoadmapOutcomeClassificationTests).
/// </summary>
public class RoadmapSupervisorNotificationTests
{
    private const string PreviousUnconditionalWording = "A new AI roadmap with";

    private static RoadmapOutcomeDescription Describe(
        RoadmapOutcome outcome, RoadmapOutcomeStyle style, bool isAiGenerated, bool isFallbackDisplayed) =>
        new(outcome, style, Title: "unused-in-these-tests", Message: "unused-in-these-tests",
            isAiGenerated, isFallbackDisplayed);

    // ---- Test 1: accepted AI notification --------------------------------

    [Fact]
    public void AcceptedAi_TitleAndMessageIndicateReviewedAiRoadmap()
    {
        var outcome = Describe(RoadmapOutcome.AcceptedAi, RoadmapOutcomeStyle.Success,
            isAiGenerated: true, isFallbackDisplayed: false);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 7);

        Assert.Contains("AI Roadmap", title, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("reviewed", message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("7", message);
        Assert.DoesNotContain("fallback", message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 2: deterministic fallback -----------------------------------

    [Fact]
    public void DeterministicFallback_TitleAndMessageIdentifyFallback_NotAiRoadmap()
    {
        var outcome = Describe(RoadmapOutcome.DeterministicFallback, RoadmapOutcomeStyle.Warning,
            isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 5);

        Assert.Contains("fallback", title, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("deterministic fallback", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("An AI roadmap", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(PreviousUnconditionalWording, message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 3: writer deadline exceeded ----------------------------------

    [Fact]
    public void WriterDeadlineExceeded_StatesTimeout_NotGenericSuccess_NoInternalTermLeaked()
    {
        var outcome = Describe(RoadmapOutcome.WriterDeadlineExceeded, RoadmapOutcomeStyle.Warning,
            isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 4);

        Assert.Contains("Timed Out", title, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("processing time", message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("fallback", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("writer_deadline_exceeded", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(PreviousUnconditionalWording, message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 4: review unavailable ----------------------------------------

    [Fact]
    public void ReviewUnavailable_IsDistinctFromDeadlineAndProviderFailure_NoAcceptedAiWording()
    {
        var outcome = Describe(RoadmapOutcome.ReviewUnavailable, RoadmapOutcomeStyle.Warning,
            isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 6);
        var (deadlineTitle, deadlineMessage) = RoadmapModel.BuildSupervisorRoadmapNotification(
            Describe(RoadmapOutcome.WriterDeadlineExceeded, RoadmapOutcomeStyle.Warning, false, true), 6);
        var (providerTitle, providerMessage) = RoadmapModel.BuildSupervisorRoadmapNotification(
            Describe(RoadmapOutcome.ProviderFailure, RoadmapOutcomeStyle.Warning, false, true), 6);

        Assert.Contains("Review Unavailable", title, StringComparison.OrdinalIgnoreCase);
        Assert.NotEqual((deadlineTitle, deadlineMessage), (title, message));
        Assert.NotEqual((providerTitle, providerMessage), (title, message));
        Assert.DoesNotContain("reviewed AI roadmap", message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 5: review rejected ---------------------------------------------

    [Fact]
    public void ReviewRejected_StatesQualityFailure_FallbackNotCalledAiGenerated()
    {
        var outcome = Describe(RoadmapOutcome.ReviewRejected, RoadmapOutcomeStyle.Warning,
            isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 8);

        Assert.Contains("Quality Review Failed", title, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("did not pass quality review", message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("fallback roadmap with 8 phase(s) was generated", message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 6: provider failure ---------------------------------------------

    [Fact]
    public void ProviderFailure_StatesUnavailabilitySafely_NoRawExceptionText_NoAcceptedAiWording()
    {
        var outcome = Describe(RoadmapOutcome.ProviderFailure, RoadmapOutcomeStyle.Warning,
            isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 3);

        Assert.Contains("Provider Unavailable", title, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("fallback", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("Exception", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("reviewed AI roadmap", message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 7: invalid/unknown outcome fails safely --------------------------

    [Fact]
    public void InvalidResponse_NeverClaimsAGeneratedOrAcceptedResult()
    {
        var outcome = Describe(RoadmapOutcome.InvalidResponse, RoadmapOutcomeStyle.Error,
            isAiGenerated: false, isFallbackDisplayed: false);

        var (_, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 0);

        Assert.DoesNotContain("was generated", message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("AI roadmap", message, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 8: exact old wording regression -----------------------------------

    [Theory]
    [InlineData(RoadmapOutcome.DeterministicFallback, RoadmapOutcomeStyle.Warning)]
    [InlineData(RoadmapOutcome.WriterDeadlineExceeded, RoadmapOutcomeStyle.Warning)]
    [InlineData(RoadmapOutcome.ReviewUnavailable, RoadmapOutcomeStyle.Warning)]
    [InlineData(RoadmapOutcome.ReviewRejected, RoadmapOutcomeStyle.Warning)]
    [InlineData(RoadmapOutcome.ProviderFailure, RoadmapOutcomeStyle.Warning)]
    public void NonAcceptedOutcomes_NeverProduceThePreviousUnconditionalSupervisorWording(
        RoadmapOutcome outcomeKind, RoadmapOutcomeStyle style)
    {
        var outcome = Describe(outcomeKind, style, isAiGenerated: false, isFallbackDisplayed: true);

        var (title, message) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 9);

        Assert.DoesNotContain(PreviousUnconditionalWording, message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(PreviousUnconditionalWording, title, StringComparison.OrdinalIgnoreCase);
    }

    // ---- Test 9: same outcome drives both student and supervisor wording -------

    [Theory]
    [InlineData(RoadmapOutcome.AcceptedAi, RoadmapOutcomeStyle.Success, true, false)]
    [InlineData(RoadmapOutcome.DeterministicFallback, RoadmapOutcomeStyle.Warning, false, true)]
    [InlineData(RoadmapOutcome.WriterDeadlineExceeded, RoadmapOutcomeStyle.Warning, false, true)]
    public void SameOutcomeInstance_DrivesConsistentStudentStyleAndSupervisorWording(
        RoadmapOutcome outcomeKind, RoadmapOutcomeStyle style, bool isAiGenerated, bool isFallbackDisplayed)
    {
        // The exact same RoadmapOutcomeDescription instance OnPostGenerateAsync
        // constructs once (via ClassifyRoadmapOutcome) is what would drive
        // BOTH the TempData style switch and this notification mapping --
        // asserting they agree on the SAME instance is what prevents the two
        // channels from drifting apart again.
        var outcome = Describe(outcomeKind, style, isAiGenerated, isFallbackDisplayed);

        var studentTempDataKey = outcome.Style switch
        {
            RoadmapOutcomeStyle.Success => "Success",
            RoadmapOutcomeStyle.Warning => "Warning",
            _ => "Error",
        };

        var (_, supervisorMessage) = RoadmapModel.BuildSupervisorRoadmapNotification(outcome, phaseCount: 2);

        if (outcomeKind == RoadmapOutcome.AcceptedAi)
        {
            Assert.Equal("Success", studentTempDataKey);
            Assert.DoesNotContain("fallback", supervisorMessage, StringComparison.OrdinalIgnoreCase);
        }
        else
        {
            Assert.Equal("Warning", studentTempDataKey);
            Assert.Contains("fallback", supervisorMessage, StringComparison.OrdinalIgnoreCase);
        }
    }
}
