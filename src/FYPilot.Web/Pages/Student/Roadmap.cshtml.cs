using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

/// <summary>
/// Display-only view of one task inside a persisted RoadmapPhase.TasksJson
/// blob. Parsed defensively: TasksJson may hold either the new structured
/// task shape (RoadmapTaskDto[]) or, for roadmaps saved before this
/// improvement, a plain string[] -- ParseTasks below handles both so old
/// saved roadmaps keep rendering instead of erroring.
/// </summary>
public sealed record RoadmapTaskView(
    string Title,
    int? EstimatedHours,
    int? EstimatedWorkingDays,
    List<string> AssignedMembers,
    List<string> Dependencies,
    string? Complexity,
    string? Priority,
    List<RoadmapMemberAllocationDto> MemberAllocations
);

public sealed record RoadmapMemberWorkloadView(
    string Member,
    int AssignedTaskCount,
    int AssignedHours,
    double UtilizationPercentage
);

/// <summary>
/// Optional/medium-priority task deferred by the overload-resolution pass
/// at generation time, persisted inside a synthetic RoadmapPhase (see
/// RoadmapModel.DeferredScopePhaseMarker) so no migration is needed and no
/// task is ever silently deleted.
/// </summary>
public sealed record RoadmapDeferredTaskView(
    string Title,
    string Description,
    int EstimatedHours,
    string ReasonDeferred,
    string OriginalPhase,
    string Priority
);

public sealed record RoadmapWorkloadSummaryView(
    int TotalWeeks,
    int TeamSize,
    int HoursPerWeekPerMember,
    int TotalCapacityHours,
    int TotalPlannedHours,
    double UtilizationPercentage,
    List<RoadmapMemberWorkloadView> WorkloadByMember,
    List<string> Warnings,
    string ScheduleFeasibility,
    int OriginalPlannedHours,
    int AdjustedPlannedHours,
    int DeferredHours,
    int OverloadHours,
    int RecommendedAdditionalWeeks
);

/// <summary>
/// Where a phase's INTENDED duration (RoadmapPhase.EstimatedWeeks, the real
/// scheduler-computed duration -- never re-derived from unrelated task
/// estimates) places it relative to the student's requested target
/// duration. Display-only classification: never persisted, never fed back
/// into Python or the database. See RoadmapModel.ClassifyPhaseTimeline.
/// </summary>
public enum RoadmapPhaseTimelineStatus
{
    /// <summary>Fully fits inside the requested target duration.</summary>
    WithinTarget,

    /// <summary>Starts inside the target duration but its intended
    /// duration runs past it.</summary>
    PartiallyBeyondTarget,

    /// <summary>Starts entirely after the requested target duration.</summary>
    BeyondTarget,

    /// <summary>EstimatedWeeks is missing/non-positive -- cannot be placed
    /// on a timeline at all. Never silently forced into the last target
    /// week.</summary>
    NeedsReview,
}

/// <summary>
/// One phase's display-only position on the timeline, computed purely from
/// the cumulative sum of preceding phases' EstimatedWeeks (never clamped to
/// the target duration) -- see RoadmapModel.ClassifyPhaseTimeline's
/// docstring for why this replaced the old clamped-cumulative-cursor
/// approach that made unrelated overflowing phases collapse onto the same
/// final target week.
/// </summary>
public sealed record RoadmapPhaseTimelineView(
    RoadmapPhase Phase,
    int DisplayStartWeek,
    int DisplayEndWeek,
    /// <summary>DisplayEndWeek clamped to the target duration -- only
    /// meaningful (and only used for rendering) when Status is WithinTarget
    /// or PartiallyBeyondTarget.</summary>
    int WithinTargetEndWeek,
    RoadmapPhaseTimelineStatus Status
);

/// <summary>
/// What a Roadmap generation attempt actually produced -- distinct from
/// RoadmapPhaseTimelineStatus (which classifies WHERE a phase sits on the
/// calendar, not whether the plan itself is real AI output). See
/// RoadmapModel.ClassifyRoadmapOutcome's docstring for the full evidence
/// priority this is derived from.
/// </summary>
public enum RoadmapOutcome
{
    /// <summary>A real, provider-generated candidate that review accepted
    /// (usable=true, not a fallback).</summary>
    AcceptedAi,

    /// <summary>Every provider failed, was rejected, or was skipped, and
    /// no more specific reason below applies -- Python's deterministic,
    /// non-AI phase catalog was used instead.</summary>
    DeterministicFallback,

    /// <summary>The Writer's own reserved slice of the pipeline deadline
    /// ran out before any provider produced a usable candidate.</summary>
    WriterDeadlineExceeded,

    /// <summary>The Writer produced a candidate, but the semantic Reviewer
    /// could not be reached/complete in time.</summary>
    ReviewUnavailable,

    /// <summary>The Writer produced a candidate, but review found it
    /// structurally invalid or a blocking quality issue survived
    /// correction.</summary>
    ReviewRejected,

    /// <summary>No more specific reason is known -- a provider-level
    /// failure (unreachable, rate-limited, malformed response, etc.).
    /// Never used when a more precise typed reason exists.</summary>
    ProviderFailure,

    /// <summary>No usable response was received from the AI service at
    /// all (network/deserialize failure, or a response with no Roadmap
    /// payload).</summary>
    InvalidResponse,
}

public enum RoadmapOutcomeStyle
{
    Success,
    Warning,
    Error,
}

/// <summary>
/// The honest, user-facing description of a RoadmapOutcome -- the single
/// source of both the POST-time flash message and the persisted-reload
/// banner, so the two can never contradict each other. Never built from
/// raw backend exception text.
/// </summary>
public sealed record RoadmapOutcomeDescription(
    RoadmapOutcome Outcome,
    RoadmapOutcomeStyle Style,
    string Title,
    string Message,
    /// <summary>True only for AcceptedAi -- every other outcome must never
    /// be described to the student as AI-generated.</summary>
    bool IsAiGenerated,
    /// <summary>True whenever the roadmap currently displayed/persisted is
    /// the deterministic (non-AI) fallback, so the UI can keep the
    /// prominent fallback banner visible.</summary>
    bool IsFallbackDisplayed
);

[Authorize(Roles = "student")]
public class RoadmapModel(
    ApplicationDbContext db,
    IAiServiceClient aiService,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    INotificationService notificationService,
    ILogger<RoadmapModel> logger)
    : PageModel
{
    /// <summary>
    /// Marks the one synthetic RoadmapPhase row (if any) that holds
    /// deferred/future-enhancement tasks instead of a real project phase --
    /// reuses the existing Name/TasksJson/EstimatedWeeks(=0) columns so no
    /// migration is needed. Never shown as a normal phase card; excluded
    /// from Phases and surfaced instead via DeferredTasks.
    /// </summary>
    public const string DeferredScopePhaseMarker = "__DEFERRED_SCOPE__";

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public ProjectIdea? Idea { get; private set; }
    public List<RoadmapPhase> Phases { get; private set; } = [];

    /// <summary>
    /// Tasks the overload-resolution pass deferred to keep the plan within
    /// team capacity -- never silently deleted, parsed from the synthetic
    /// deferred-scope phase's TasksJson.
    /// </summary>
    public List<RoadmapDeferredTaskView> DeferredTasks { get; private set; } = [];

    /// <summary>
    /// Recomputed on every page load from the persisted per-task JSON
    /// already stored in each phase's TasksJson (hours + assignedMembers),
    /// combined with the student's current team size/available hours --
    /// deliberately NOT persisted as its own column, so no migration is
    /// needed and the figures always reflect the student's latest profile.
    /// </summary>
    public RoadmapWorkloadSummaryView? WorkloadSummary { get; private set; }

    public string? ErrorMessage { get; private set; }
    public bool LlmUsed { get; private set; }
    public string? Source { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated roadmap for
    /// this idea, loaded from the database so it survives the
    /// Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as Mentor Chat -- linked via ProjectIdeaId
    /// rather than a chat session, no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe roadmap"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    /// <summary>
    /// The single, centralized Roadmap result classifier -- both
    /// OnPostGenerateAsync's flash message and the persisted-reload banner
    /// (Roadmap.cshtml) call this instead of each re-deriving their own
    /// "was this really AI-generated" logic, so the two can never disagree.
    ///
    /// Evidence priority (strongest first -- a weaker field can never
    /// override a stronger one that says otherwise, see Test 6 in
    /// RoadmapOutcomeClassificationTests for the exact conflicting-fields
    /// case this guards):
    ///   1. OutputOrigin        -- "deterministic_fallback" is authoritative
    ///   2. FallbackUsed        -- explicit boolean from the same source
    ///   3. LlmUsed             -- false means no provider ever produced output
    ///   4. Usable (review)     -- false means review rejected/never accepted it
    ///   5. Review status       -- narrows WHY (rejected/unavailable/etc.)
    ///   6. FallbackReasonCode  -- narrows WHICH fallback reason, when known
    ///   7. Response availability -- no response/Roadmap at all
    ///
    /// Only reachable immediately after POST (this overload), since
    /// FallbackReasonCode/OutputOrigin are not persisted as their own
    /// columns -- see the AiOutputReview overload below for the
    /// necessarily coarser classification available after a page reload.
    /// </summary>
    public static RoadmapOutcomeDescription ClassifyRoadmapOutcome(ProjectRoadmapServiceResponse? response)
    {
        if (response?.Roadmap == null)
        {
            return DescribeRoadmapOutcome(RoadmapOutcome.InvalidResponse);
        }

        var review = response.Review;

        var isFallback =
            response.OutputOrigin == "deterministic_fallback"
            || response.FallbackUsed
            || !response.LlmUsed
            || review?.Usable == false;

        if (!isFallback)
        {
            return DescribeRoadmapOutcome(RoadmapOutcome.AcceptedAi, review?.Status);
        }

        return response.FallbackReasonCode switch
        {
            "writer_deadline_exceeded" => DescribeRoadmapOutcome(RoadmapOutcome.WriterDeadlineExceeded),
            "reviewer_unavailable" => DescribeRoadmapOutcome(RoadmapOutcome.ReviewUnavailable),
            "schema_invalid" or "semantic_rewrite_failed" or "final_review_failed"
                or "blocked_content" or "insufficient_usable_phases" or "lifecycle_coverage_failed"
                => DescribeRoadmapOutcome(RoadmapOutcome.ReviewRejected),
            "provider_unavailable" or "provider_authentication_failed" or "provider_rate_limited"
                or "provider_timeout" or "provider_payload_invalid" or "json_parse_failed"
                => DescribeRoadmapOutcome(RoadmapOutcome.ProviderFailure),
            _ => ClassifyFallbackFromReviewStatus(review?.Status),
        };
    }

    /// <summary>
    /// Reload-path overload: only Status/Usable/DecisionReason survive
    /// into the persisted AiOutputReview (no OutputOrigin/FallbackUsed/
    /// FallbackReasonCode column exists -- adding one is out of scope, see
    /// class docs). This necessarily cannot distinguish
    /// WriterDeadlineExceeded from a generic ProviderFailure on a cold
    /// reload the way the POST-time overload can; the flash message shown
    /// immediately after generation (which DOES have the precise reason)
    /// is the authoritative one for that distinction.
    /// </summary>
    public static RoadmapOutcomeDescription ClassifyRoadmapOutcome(AiOutputReview review)
    {
        ArgumentNullException.ThrowIfNull(review);

        if (!review.Usable)
        {
            return ClassifyFallbackFromReviewStatus(review.Status);
        }

        return DescribeRoadmapOutcome(RoadmapOutcome.AcceptedAi, review.Status);
    }

    private static RoadmapOutcomeDescription ClassifyFallbackFromReviewStatus(string? reviewStatus) => reviewStatus switch
    {
        "review_unavailable" => DescribeRoadmapOutcome(RoadmapOutcome.ReviewUnavailable),
        "rejected" or "schema_invalid" or "firewall_blocked" or "unresolved"
            => DescribeRoadmapOutcome(RoadmapOutcome.ReviewRejected),
        "provider_unavailable" => DescribeRoadmapOutcome(RoadmapOutcome.ProviderFailure),
        _ => DescribeRoadmapOutcome(RoadmapOutcome.DeterministicFallback),
    };

    private static RoadmapOutcomeDescription DescribeRoadmapOutcome(RoadmapOutcome outcome, string? reviewStatus = null) => outcome switch
    {
        RoadmapOutcome.AcceptedAi => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Success, "AI accepted",
            reviewStatus == "approved_with_minor_warnings"
                ? "Your AI roadmap was generated and saved. The reviewer noted minor, non-blocking notes."
                : "Your AI roadmap was generated, reviewed, and saved successfully.",
            IsAiGenerated: true, IsFallbackDisplayed: false),

        RoadmapOutcome.WriterDeadlineExceeded => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Warning, "Generation timed out",
            "AI generation exceeded the available processing time. A deterministic fallback roadmap is displayed, and no result is being presented as accepted AI output.",
            IsAiGenerated: false, IsFallbackDisplayed: true),

        RoadmapOutcome.ReviewUnavailable => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Warning, "Review unavailable",
            "The roadmap could not be fully reviewed within the available time. It has not been presented as an accepted AI roadmap.",
            IsAiGenerated: false, IsFallbackDisplayed: true),

        RoadmapOutcome.ReviewRejected => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Warning, "Review rejected",
            "The generated roadmap did not pass quality review. A safe deterministic roadmap is displayed instead.",
            IsAiGenerated: false, IsFallbackDisplayed: true),

        RoadmapOutcome.ProviderFailure => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Warning, "AI service unavailable",
            "The AI provider was temporarily unavailable. A deterministic fallback roadmap is displayed instead.",
            IsAiGenerated: false, IsFallbackDisplayed: true),

        RoadmapOutcome.DeterministicFallback => new RoadmapOutcomeDescription(
            outcome, RoadmapOutcomeStyle.Warning, "Deterministic fallback",
            "AI generation could not produce an accepted roadmap. A deterministic fallback roadmap is displayed instead.",
            IsAiGenerated: false, IsFallbackDisplayed: true),

        _ => new RoadmapOutcomeDescription(
            RoadmapOutcome.InvalidResponse, RoadmapOutcomeStyle.Error, "Roadmap unavailable",
            "The AI roadmap service did not return a usable response. Please try again.",
            IsAiGenerated: false, IsFallbackDisplayed: false),
    };

    /// <summary>
    /// Maps the SAME centralized classification used for the student flash
    /// message/banner (see ClassifyRoadmapOutcome/DescribeRoadmapOutcome)
    /// into supervisor-notification wording -- a second, audience-
    /// appropriate rendering of one outcome, never a second classifier.
    /// Pure and database-free (no DI, no I/O), so it can never itself
    /// throw inside NotifySupervisorAsync's existing try/catch, and is
    /// directly unit-testable without mocking anything.
    ///
    /// The returned Message deliberately does NOT include the project
    /// title -- NotifySupervisorAsync already appends
    /// `Project: "{projectTitle}"` to whatever message it's given (see its
    /// own body), so embedding it here too would duplicate it.
    ///
    /// Exhaustive over every RoadmapOutcome value; a case this switch
    /// doesn't specifically recognize falls through to safe, neutral
    /// wording that never claims a generated/accepted result -- see the
    /// InvalidResponse arm's comment for why that branch is unreachable
    /// from OnPostGenerateAsync's actual call site today.
    /// </summary>
    public static (string Title, string Message) BuildSupervisorRoadmapNotification(
        RoadmapOutcomeDescription outcome, int phaseCount) => outcome.Outcome switch
    {
        RoadmapOutcome.AcceptedAi => (
            "AI Roadmap Generated",
            $"A reviewed AI roadmap with {phaseCount} phase(s) was generated."),

        RoadmapOutcome.DeterministicFallback => (
            "Fallback Roadmap Generated",
            $"A deterministic fallback roadmap with {phaseCount} phase(s) was generated because an accepted AI roadmap was not available."),

        RoadmapOutcome.WriterDeadlineExceeded => (
            "Roadmap Generation Timed Out",
            $"AI roadmap generation exceeded the available processing time. A deterministic fallback roadmap with {phaseCount} phase(s) was generated instead."),

        RoadmapOutcome.ReviewUnavailable => (
            "Roadmap Review Unavailable",
            $"The generated roadmap could not complete AI quality review. A deterministic fallback roadmap with {phaseCount} phase(s) was generated instead."),

        RoadmapOutcome.ReviewRejected => (
            "Roadmap Quality Review Failed",
            $"The AI-generated roadmap did not pass quality review. A deterministic fallback roadmap with {phaseCount} phase(s) was generated instead."),

        RoadmapOutcome.ProviderFailure => (
            "AI Provider Unavailable",
            $"The AI provider was unavailable while generating the roadmap. A deterministic fallback roadmap with {phaseCount} phase(s) was generated instead."),

        // InvalidResponse, and any future/unrecognized outcome: never
        // reachable from OnPostGenerateAsync today (it already returns
        // before persistence/notification whenever response.Roadmap is
        // null), kept only so this mapping stays exhaustive and safe if
        // reused elsewhere -- never claims a roadmap was generated.
        _ => (
            "Roadmap Generation Issue",
            "A roadmap generation attempt did not produce a confirmed result."),
    };

    public async Task<IActionResult> OnGetAsync(
        int? ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        var contextResult =
            await ResolveProjectContextAsync(
                userId,
                cancellationToken);

        if (contextResult != null)
        {
            return contextResult;
        }

        await LoadPageDataAsync(userId);

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/Roadmap",
            cancellationToken);

        return Page();
    }

    public async Task<IActionResult> OnPostGenerateAsync(
        int ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        var contextResult =
            await ResolveProjectContextAsync(
                userId,
                cancellationToken);

        if (contextResult != null)
        {
            return contextResult;
        }

        await LoadPageDataAsync(userId);

        if (Idea == null)
        {
            TempData["Error"] = "Project idea was not found.";
            return RedirectToPage();
        }

        var studentSkills = await db.StudentSkills
            .Where(s => s.UserId == userId)
            .ToListAsync();

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        var request = BuildRoadmapRequest(Idea, profile, studentSkills);

        // Correlation id for this .NET-side attempt -- logged alongside every
        // failure branch below so a support/debug session can find the
        // matching AI-service-side logs even when no response (and therefore
        // no Python requestId) was ever received. AiServiceClient
        // deliberately THROWS on transport/HTTP/deserialize failure (see its
        // own header comment) rather than returning null, so every one of
        // those cases must be caught here explicitly -- an uncaught throw
        // would otherwise surface as an unhandled 500 instead of the
        // existing roadmap page with a friendly error, and would never reach
        // the persistence code below (so nothing would be at risk of being
        // overwritten either way, but the user would see a crash, not this
        // page).
        var correlationId = Guid.NewGuid();
        ProjectRoadmapServiceResponse? response;

        try
        {
            response = await aiService.GenerateProjectRoadmapAsync(request);
        }
        catch (TaskCanceledException exception) when (!cancellationToken.IsCancellationRequested)
        {
            // The request's OWN cancellationToken did not fire, so this is
            // AiServiceClient's HttpClient timeout (600s), not the user
            // navigating away -- a genuine timeout deserves a specific
            // message, not the generic one below.
            logger.LogWarning(
                exception,
                "Roadmap generation timed out for idea {IdeaId} (correlation {CorrelationId}).",
                Idea.Id,
                correlationId);

            ErrorMessage =
                "Roadmap generation took too long and timed out. Your previous roadmap, if any, has been kept. Please try again.";
            return Page();
        }
        catch (HttpRequestException exception)
        {
            logger.LogWarning(
                exception,
                "Roadmap AI service call failed for idea {IdeaId} (correlation {CorrelationId}).",
                Idea.Id,
                correlationId);

            ErrorMessage =
                "The AI roadmap service could not be reached. Your previous roadmap, if any, has been kept. Please try again shortly.";
            return Page();
        }
        catch (InvalidOperationException exception)
        {
            // AiServiceClient wraps a JSON deserialize failure in this type
            // (see its PostAsync catch block) -- the AI service responded,
            // but not with a shape .NET could understand.
            logger.LogWarning(
                exception,
                "Roadmap AI service returned an unreadable response for idea {IdeaId} (correlation {CorrelationId}).",
                Idea.Id,
                correlationId);

            ErrorMessage =
                "The AI service returned a response that could not be understood. Your previous roadmap, if any, has been kept. Please try again.";
            return Page();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected error generating roadmap for idea {IdeaId} (correlation {CorrelationId}).",
                Idea.Id,
                correlationId);

            ErrorMessage =
                "Something went wrong while generating the roadmap. Your previous roadmap, if any, has been kept.";
            return Page();
        }

        if (response?.Roadmap == null)
        {
            ErrorMessage = "AI roadmap could not be generated. Make sure the Python AI service is running.";
            return Page();
        }

        LlmUsed = response.LlmUsed;
        Source = response.Source;

        var existingRoadmaps = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .Where(r => r.IdeaId == Idea.Id && r.UserId == userId)
            .ToListAsync();

        db.ProjectRoadmaps.RemoveRange(existingRoadmaps);

        var phases = ConvertAiRoadmapToPhases(
            response.Roadmap,
            Idea.RequiredTechnologies
        );

        var roadmap = new ProjectRoadmap
        {
            IdeaId = Idea.Id,
            UserId = userId,
            Phases = phases
        };

        db.ProjectRoadmaps.Add(roadmap);

        var review = response.Review;

        if (review != null)
        {
            // review.Usable is the authoritative "was this AI-generated?"
            // signal (false means the Python router used
            // agent.build_safe_fallback() -- see routers/roadmap.py's
            // outputOrigin/fallbackUsed derivation). Some non-usable
            // pipeline statuses (provider_unavailable, firewall_blocked)
            // never set a DecisionReason at the Writer stage -- see
            // ReviewPipeline._result()'s "decision" param, which stays None
            // there -- so without this backfill the review badge's tooltip
            // would be blank for the single most common fallback case.
            var decisionReason =
                !review.Usable && string.IsNullOrWhiteSpace(review.DecisionReason)
                    ? response.FallbackReasonMessage ?? "AI roadmap generation was not usable; a safe fallback roadmap was shown instead."
                    : review.DecisionReason;

            db.AiOutputReviews.Add(new AiOutputReview
            {
                ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                    ? reviewRunId
                    : Guid.NewGuid(),
                UserId = userId,
                ProjectIdeaId = Idea.Id,
                MentorChatSessionId = null,
                AgentName = "ProjectRoadmapAgent",
                Status = review.Status,
                Usable = review.Usable,
                WasRewritten = review.Attempts > 1,
                Attempts = review.Attempts,
                QualityScore = review.QualityScore,
                DecisionReason = decisionReason,
                GeneratorProvider = response.Provider,
                GeneratorModel = response.ModelUsed,
                ReviewerProvider = review.ReviewerProvider,
                ReviewerModel = review.ReviewerModel,
                FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
                FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
                FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
                IssuesJson = JsonSerializer.Serialize(review.Issues),
                StrengthsJson = JsonSerializer.Serialize(review.Strengths),
                AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
                ReviewerVersion = review.ReviewerVersion,
                CreatedAt = DateTime.UtcNow,
                CompletedAt = DateTime.UtcNow
            });
        }

        await db.SaveChangesAsync(cancellationToken);

        var realPhaseCount = phases.Count(p => p.Name != DeferredScopePhaseMarker);

        // Classified ONCE here and reused for both audiences below -- was
        // previously computed only for the student flash message, AFTER
        // NotifySupervisorAsync had already been called with unconditional
        // "AI roadmap ... generated" wording regardless of whether AI
        // generation actually happened. That let a deterministic fallback,
        // a rejected/unusable candidate, or a writer-deadline/review-
        // unavailable case notify the supervisor as if a real AI roadmap
        // had been accepted, while the student simultaneously saw an
        // honest fallback warning. ClassifyRoadmapOutcome is the single
        // source of truth for this wording; the persisted-reload banner
        // (Roadmap.cshtml) derives the same wording from LatestReview via
        // the AiOutputReview overload, so all three surfaces can never
        // disagree.
        var outcome = ClassifyRoadmapOutcome(response);

        var (supervisorTitle, supervisorMessage) = BuildSupervisorRoadmapNotification(outcome, realPhaseCount);
        await NotifySupervisorAsync(
            userId,
            supervisorTitle,
            supervisorMessage,
            "roadmap_generated",
            cancellationToken);

        var outcomeMessage = outcome.Outcome == RoadmapOutcome.AcceptedAi
            ? $"{outcome.Message} ({realPhaseCount} phase(s).)"
            : outcome.Message;

        TempData[outcome.Style switch
        {
            RoadmapOutcomeStyle.Success => "Success",
            RoadmapOutcomeStyle.Warning => "Warning",
            _ => "Error",
        }] = outcomeMessage;

        return RedirectToPage(
            new
            {
                projectId = ProjectId,
                ideaId = Idea.Id
            });
    }

    public async Task<IActionResult> OnPostCompleteAsync(
       int phaseId,
       int ideaId,
       CancellationToken cancellationToken)
    {
        var userId = UserId();

        var contextResult =
            await ResolveProjectContextAsync(
                userId,
                cancellationToken);

        if (contextResult != null)
        {
            return contextResult;
        }

        await LoadPageDataAsync(userId);

        if (Idea == null)
        {
            TempData["Error"] =
                "Project idea was not found.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var updatedRows = await db.RoadmapPhases
            .Where(p =>
                p.Id == phaseId &&
                p.Roadmap != null &&
                p.Roadmap.UserId == userId &&
                p.Roadmap.IdeaId == Idea.Id)
            .ExecuteUpdateAsync(updates => updates
                .SetProperty(
                    p => p.IsCompleted,
                    true));

        if (updatedRows == 0)
        {
            TempData["Error"] =
                "This roadmap phase was not found or does not belong to your account.";

            return RedirectToPage(new
            {
                projectId = ProjectId,
                ideaId = Idea.Id
            });
        }

        await NotifySupervisorAsync(
            userId,
            "Roadmap phase completed",
            "A roadmap phase was marked as completed by a project member.",
            "roadmap_phase_completed",
            cancellationToken);

        TempData["Success"] =
            "Phase marked as completed.";

        return RedirectToPage(new
        {
            projectId = ProjectId,
            ideaId = Idea.Id
        });
    }

    private async Task NotifySupervisorAsync(
        int actorUserId,
        string title,
        string message,
        string type,
        CancellationToken cancellationToken)
    {
        var projectInfo = await db.Projects
            .AsNoTracking()
            .Where(project =>
                project.Id == ProjectId &&
                project.SupervisorId.HasValue &&
                project.SupervisorAssignmentStatus == "active")
            .Select(project => new
            {
                SupervisorId = project.SupervisorId!.Value,
                project.Title
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (projectInfo == null ||
            projectInfo.SupervisorId == actorUserId)
        {
            return;
        }

        var projectTitle = string.IsNullOrWhiteSpace(projectInfo.Title)
            ? "Untitled Project"
            : projectInfo.Title.Trim();

        try
        {
            await notificationService.NotifyUserAsync(
                recipientUserId: projectInfo.SupervisorId,
                title: title,
                message: $"{message} Project: \"{projectTitle}\".",
                type: type,
                url: $"/Supervisor/ProgressTracking?projectId={ProjectId}",
                sendEmail: false,
                projectId: ProjectId,
                actorUserId: actorUserId,
                cancellationToken: cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Roadmap action succeeded for project {ProjectId}, but supervisor {SupervisorId} could not be notified.",
                ProjectId,
                projectInfo.SupervisorId);
        }
    }

    private async Task LoadPageDataAsync(int userId)
    {
        /*
         * The active project's official idea is the
         * source of truth for this page.
         */
        Idea = await db.Projects
            .Where(project =>
                project.Id == ProjectId)
            .Select(project =>
                project.ProjectIdea)
            .FirstOrDefaultAsync();

        if (Idea == null)
        {
            return;
        }

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.ProjectIdeaId == Idea.Id &&
                r.UserId == userId &&
                r.AgentName == "ProjectRoadmapAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();

        var roadmap = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .FirstOrDefaultAsync(r => r.IdeaId == Idea.Id && r.UserId == userId);

        var allPhases = roadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .ToList() ?? [];

        var deferredPhase = allPhases.FirstOrDefault(p => p.Name == DeferredScopePhaseMarker);
        Phases = allPhases.Where(p => p.Name != DeferredScopePhaseMarker).ToList();
        DeferredTasks = deferredPhase != null ? ParseDeferredTasks(deferredPhase.TasksJson) : [];

        if (Phases.Count > 0)
        {
            var profile = await db.StudentProfiles
                .AsNoTracking()
                .FirstOrDefaultAsync(p => p.UserId == userId);

            WorkloadSummary = ComputeWorkloadSummary(Idea, profile, Phases, DeferredTasks);
        }
    }

    /// <summary>
    /// Parses one phase's TasksJson defensively: the new structured shape
    /// (object per task, with hours/assignedMembers) if present, otherwise
    /// falls back to the old plain string[] shape used by roadmaps saved
    /// before this improvement -- so old saved roadmaps keep rendering
    /// instead of throwing on deserialize.
    /// </summary>
    public static List<RoadmapTaskView> ParseTasks(string tasksJson)
    {
        if (string.IsNullOrWhiteSpace(tasksJson) || tasksJson == "[]")
        {
            return [];
        }

        try
        {
            var structured = JsonSerializer.Deserialize<List<RoadmapTaskDto>>(
                tasksJson,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

            if (structured is { Count: > 0 } && structured[0].Title is { Length: > 0 })
            {
                return structured
                    .Select(t => new RoadmapTaskView(
                        t.Title,
                        t.EstimatedHours,
                        t.EstimatedWorkingDays,
                        t.AssignedMembers ?? [],
                        t.Dependencies ?? [],
                        t.Complexity,
                        t.Priority,
                        t.MemberAllocations ?? []))
                    .ToList();
            }
        }
        catch (JsonException)
        {
            // Falls through to the plain-string-list parse below.
        }

        var plain = JsonSerializer.Deserialize<List<string>>(tasksJson) ?? [];
        return plain
            .Select(title => new RoadmapTaskView(title, null, null, [], [], null, null, []))
            .ToList();
    }

    /// <summary>
    /// Parses the synthetic deferred-scope phase's TasksJson (a
    /// RoadmapDeferredTaskDto[] blob, a different shape from a normal
    /// phase's task list). Defensive: an empty/malformed blob just yields
    /// no deferred tasks rather than throwing.
    /// </summary>
    public static List<RoadmapDeferredTaskView> ParseDeferredTasks(string tasksJson)
    {
        if (string.IsNullOrWhiteSpace(tasksJson) || tasksJson == "[]")
        {
            return [];
        }

        try
        {
            var structured = JsonSerializer.Deserialize<List<RoadmapDeferredTaskDto>>(
                tasksJson,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? [];

            return structured
                .Select(t => new RoadmapDeferredTaskView(
                    t.Title, t.Description, t.EstimatedHours, t.ReasonDeferred,
                    t.OriginalPhase, t.Priority))
                .ToList();
        }
        catch (JsonException)
        {
            return [];
        }
    }

    /// <summary>
    /// Display-only classification of where each phase sits relative to
    /// the requested target duration, using a cumulative-cursor walk over
    /// phases' already-authoritative EstimatedWeeks in canonical
    /// (PhaseNumber) order -- deliberately NEVER clamped to targetWeeks
    /// (the old `Math.Min(weekCursor, totalWeeks)` approach this replaces
    /// made every phase past the target collapse onto the exact same final
    /// week, e.g. three different phases all showing "Week 16"). Pure
    /// function: never mutates orderedPhases or touches the database --
    /// see RoadmapPhaseTimelineView's docstring.
    ///
    /// Deliberately does NOT invent exact post-target week numbers beyond
    /// what EstimatedWeeks already implies -- it only classifies using data
    /// that is already authoritative (the persisted phase duration), never
    /// re-derives duration from task estimates or guesses.
    /// </summary>
    public static List<RoadmapPhaseTimelineView> ClassifyPhaseTimeline(
        IReadOnlyList<RoadmapPhase> orderedPhases,
        int targetWeeks)
    {
        var safeTarget = Math.Max(1, targetWeeks);
        var result = new List<RoadmapPhaseTimelineView>(orderedPhases.Count);
        var cursor = 1;

        foreach (var phase in orderedPhases)
        {
            if (phase.EstimatedWeeks <= 0)
            {
                result.Add(new RoadmapPhaseTimelineView(
                    phase, 0, 0, 0, RoadmapPhaseTimelineStatus.NeedsReview));
                continue;
            }

            var displayStart = cursor;
            var displayEnd = displayStart + phase.EstimatedWeeks - 1;
            cursor = displayEnd + 1;

            var status =
                displayEnd <= safeTarget ? RoadmapPhaseTimelineStatus.WithinTarget :
                displayStart <= safeTarget ? RoadmapPhaseTimelineStatus.PartiallyBeyondTarget :
                RoadmapPhaseTimelineStatus.BeyondTarget;

            result.Add(new RoadmapPhaseTimelineView(
                phase, displayStart, displayEnd, Math.Min(displayEnd, safeTarget), status));
        }

        return result;
    }

    private static RoadmapWorkloadSummaryView ComputeWorkloadSummary(
        ProjectIdea idea,
        StudentProfile? profile,
        List<RoadmapPhase> phases,
        List<RoadmapDeferredTaskView> deferredTasks
    )
    {
        var teamSize = Math.Max(1, profile?.TeamMembers ?? 1);
        var hoursPerWeek = Math.Max(1, profile?.AvailableHoursPerWeek ?? 10);
        var totalWeeks = Math.Max(1, idea.ExpectedDurationWeeks > 0 ? idea.ExpectedDurationWeeks : 10);

        // `phases` here already excludes the deferred-scope phase (see
        // LoadPageDataAsync), so totalPlannedHours below is naturally the
        // ADJUSTED (post-deferral) figure -- what's actually still in the
        // plan -- while originalPlannedHours adds the deferred hours back
        // for comparison.
        var allTasks = phases.SelectMany(p => ParseTasks(p.TasksJson)).ToList();
        var adjustedPlannedHours = allTasks.Sum(t => t.EstimatedHours ?? 0);
        var deferredHours = deferredTasks.Sum(t => t.EstimatedHours);
        var originalPlannedHours = adjustedPlannedHours + deferredHours;

        var totalCapacityHours = totalWeeks * hoursPerWeek * teamSize;
        var utilization = totalCapacityHours > 0
            ? Math.Round(adjustedPlannedHours * 100.0 / totalCapacityHours, 1)
            : 0.0;

        var memberCapacity = totalWeeks * hoursPerWeek;
        var workloadByMember = new List<RoadmapMemberWorkloadView>();

        for (var i = 1; i <= teamSize; i++)
        {
            var label = $"Member {i}";
            var memberTasks = allTasks.Where(t => t.AssignedMembers.Contains(label)).ToList();
            // Use this member's own allocated share (MemberAllocations),
            // never the task's full EstimatedHours -- a collaborative task
            // lists BOTH members in AssignedMembers, so summing full hours
            // per member here would double-count exactly like the bug this
            // batch fixed on the Python side (see roadmap_scheduler.
            // allocate_task_hours). Falls back to full hours only for
            // roadmaps saved before MemberAllocations existed.
            var memberHours = memberTasks.Sum(t =>
            {
                var allocation = t.MemberAllocations.FirstOrDefault(a => a.MemberId == label);
                return allocation?.AllocatedHours ?? (t.EstimatedHours ?? 0);
            });
            var memberUtilization = memberCapacity > 0
                ? Math.Round(memberHours * 100.0 / memberCapacity, 1)
                : 0.0;

            workloadByMember.Add(new RoadmapMemberWorkloadView(
                label, memberTasks.Count, memberHours, memberUtilization));
        }

        var overloadHours = Math.Max(0, adjustedPlannedHours - totalCapacityHours);
        string scheduleFeasibility;
        int recommendedAdditionalWeeks;

        if (overloadHours > 0)
        {
            scheduleFeasibility = "over_capacity";
            var teamWeeklyCapacity = Math.Max(1, hoursPerWeek * teamSize);
            recommendedAdditionalWeeks = (int)Math.Ceiling(overloadHours / (double)teamWeeklyCapacity);
        }
        else if (deferredHours > 0)
        {
            scheduleFeasibility = "feasible_after_scope_reduction";
            recommendedAdditionalWeeks = 0;
        }
        else
        {
            scheduleFeasibility = "feasible";
            recommendedAdditionalWeeks = 0;
        }

        var warnings = new List<string>();
        if (allTasks.Count > 0)
        {
            if (scheduleFeasibility == "over_capacity")
            {
                warnings.Add(
                    $"Essential project scope exceeds available capacity by {overloadHours}h " +
                    $"even after deferring optional work -- add approximately " +
                    $"{recommendedAdditionalWeeks} more week(s) or reduce the mandatory scope.");
            }
            else if (scheduleFeasibility == "feasible_after_scope_reduction")
            {
                warnings.Add(
                    $"{deferredTasks.Count} optional task(s) totalling {deferredHours}h were " +
                    "deferred to future enhancements to fit the available capacity.");
            }
            else if (utilization < 30)
            {
                warnings.Add(
                    $"Planned work only uses {utilization}% of available capacity.");
            }
        }

        return new RoadmapWorkloadSummaryView(
            totalWeeks, teamSize, hoursPerWeek, totalCapacityHours,
            adjustedPlannedHours, utilization, workloadByMember, warnings,
            scheduleFeasibility, originalPlannedHours, adjustedPlannedHours,
            deferredHours, overloadHours, recommendedAdditionalWeeks);
    }

    private static ProjectRoadmapRequest BuildRoadmapRequest(
        ProjectIdea idea,
        StudentProfile? profile,
        List<StudentSkill> studentSkills
    )
    {
        var skillNames = studentSkills
            .Select(s => s.SkillName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var skillRatings = studentSkills
            .Where(s => !string.IsNullOrWhiteSpace(s.SkillName))
            .GroupBy(s => s.SkillName.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                g => g.Key,
                g => Math.Clamp(g.First().Rating, 1, 5),
                StringComparer.OrdinalIgnoreCase
            );

        var expectedWeeks = idea.ExpectedDurationWeeks > 0
            ? idea.ExpectedDurationWeeks
            : 10;

        return new ProjectRoadmapRequest(
            IdeaTitle: idea.Title,
            ProblemStatement: idea.ProblemStatement,
            RequiredTechnologies: idea.RequiredTechnologies,
            RequiredSkills: idea.RequiredSkills,
            MissingSkills: idea.MissingSkills,
            DifficultyLevel: idea.DifficultyLevel,
            ExpectedDurationWeeks: expectedWeeks,
            Domain: idea.Domain,
            FinalDeliverables: idea.FinalDeliverables,
            TeamSize: profile?.TeamMembers ?? 1,
            AvailableHoursPerWeek: profile?.AvailableHoursPerWeek ?? 10,
            StudentSkills: skillNames,
            SkillRatings: skillRatings
        );
    }

    private static List<RoadmapPhase> ConvertAiRoadmapToPhases(
        ProjectRoadmapDto roadmap,
        string requiredTechnologies
    )
    {
        if (roadmap.Phases is { Count: > 0 })
        {
            var weeksByNumber = roadmap.Weeks.ToDictionary(w => w.WeekNumber);

            var phases = roadmap.Phases
                .Select((phase, index) =>
                {
                    // The last week within this phase's span still carries
                    // the risk/checkpoint narrative -- phases[] itself
                    // doesn't repeat those per-phase, so borrow them from
                    // weeks[] for display continuity.
                    weeksByNumber.TryGetValue(phase.EndWeek, out var lastWeek);

                    return new RoadmapPhase
                    {
                        PhaseNumber = index + 1,
                        Name = phase.Name,
                        Objective = phase.Objective,
                        TasksJson = JsonSerializer.Serialize(phase.Tasks ?? []),
                        ExpectedOutput = string.Join("; ", phase.Deliverables ?? []),
                        ToolsNeeded = requiredTechnologies,
                        // The real per-phase duration computed by
                        // roadmap_scheduler -- no longer hardcoded to 1.
                        EstimatedWeeks = phase.DurationWeeks,
                        Dependencies = phase.Dependencies is { Count: > 0 }
                            ? string.Join(", ", phase.Dependencies)
                            : "None",
                        Risks = lastWeek?.RiskWarning ?? "",
                        SuccessCriteria = lastWeek?.Checkpoint ?? "",
                        IsCompleted = false
                    };
                })
                .ToList();

            // Persist deferred tasks as one synthetic phase reusing the
            // existing Name/TasksJson/EstimatedWeeks(=0) columns -- no
            // migration needed, and tasks are never silently dropped.
            // RoadmapModel excludes this from the normal phase list and
            // parses it separately via ParseDeferredTasks.
            if (roadmap.DeferredTasks is { Count: > 0 })
            {
                phases.Add(new RoadmapPhase
                {
                    PhaseNumber = phases.Count + 1,
                    Name = RoadmapModel.DeferredScopePhaseMarker,
                    Objective = "",
                    TasksJson = JsonSerializer.Serialize(roadmap.DeferredTasks),
                    ExpectedOutput = "",
                    ToolsNeeded = requiredTechnologies,
                    EstimatedWeeks = 0,
                    Dependencies = "None",
                    Risks = "",
                    SuccessCriteria = "",
                    IsCompleted = false
                });
            }

            return phases;
        }

        // Backward-compatible fallback: only reachable if the AI service
        // response somehow lacks structured `phases` (older cached
        // response shape) -- keeps existing saved-roadmap replay safe
        // without assuming the new field is always present.
        return roadmap.Weeks
            .OrderBy(w => w.WeekNumber)
            .Select(week => new RoadmapPhase
            {
                PhaseNumber = week.WeekNumber,
                Name = week.PhaseTitle,
                Objective = week.MainGoal,
                TasksJson = JsonSerializer.Serialize(week.Tasks ?? []),
                ExpectedOutput = string.Join("; ", week.Deliverables ?? []),
                ToolsNeeded = requiredTechnologies,
                EstimatedWeeks = 1,
                Dependencies = week.WeekNumber == 1
                    ? "None"
                    : $"Complete Phase {week.WeekNumber - 1}",
                Risks = week.RiskWarning,
                SuccessCriteria = week.Checkpoint,
                IsCompleted = false
            })
            .ToList();
    }

    private async Task<IActionResult?>
        ResolveProjectContextAsync(
            int userId,
            CancellationToken cancellationToken)
    {
        if (ProjectId <= 0)
        {
            var activeProjectId =
                await activeProjectService
                    .GetActiveProjectIdAsync(
                        userId,
                        cancellationToken);

            if (!activeProjectId.HasValue)
            {
                TempData["Error"] =
                    "Choose a project before opening "
                    + "the Roadmap.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            ProjectId = activeProjectId.Value;
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (access == null)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        return null;
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}