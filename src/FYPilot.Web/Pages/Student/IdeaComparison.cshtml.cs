using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.Common;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using FYPilot.Infrastructure.Services.Finalizers;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class IdeaComparisonModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    IAiAgentJobService jobService,
    IAiJobsPythonClient pythonClient,
    INotificationService notificationService,
    ILogger<IdeaComparisonModel> logger
) : PageModel
{
    private const string AgentName = "IdeaComparisonAgent";
    private const int MaximumProjectMembers = 21;

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public Project? CurrentProject { get; private set; }

    public ProjectAccessResult? ProjectAccess { get; private set; }

    public List<ProjectIdea> Ideas { get; private set; } = [];

    /// <summary>
    /// The most recent Generate/Regenerate batch (see
    /// IdeaGeneratorModel.SaveGeneratedIdeasAsync's GenerationBatchId) --
    /// what the AI comparison selection defaults to. Ideas saved before
    /// GenerationBatchId existed have no batch to group by, so the default
    /// falls back to the single most recent idea by CreatedAt for those.
    /// </summary>
    public HashSet<int> LatestBatchIdeaIds { get; private set; } = [];

    public const int MinimumIdeasToCompare = 2;
    public const int RecommendedMaximumIdeasToCompare = 4;
    public const int HardMaximumIdeasToCompare = 5;

    /// <summary>
    /// Ideas the student picked to send to the AI comparison -- bound from
    /// the checkboxes on the Compare form. A normal comparison is the
    /// latest batch (~3-4 ideas); this lets the student swap in older
    /// ideas instead, but never send the AI service everything the project
    /// has ever generated (see ValidateAndAuthorizeSelection's count
    /// enforcement). The 45-second generation deadline itself is now owned
    /// by the Python worker (see app/jobs/workers/idea_comparison_worker.py).
    /// </summary>
    [BindProperty]
    public List<int> SelectedIdeaIds { get; set; } = [];

    /// <summary>
    /// Stored generated-score data used by the original comparison table.
    /// The table reads Innovation, Feasibility and Market Demand directly
    /// from ProjectIdea, plus a C# overall average. Hover/tap explanations
    /// are rendered from the saved reason columns. No AI call is made to
    /// populate these score cells.
    /// </summary>
    public List<IdeaScorecardView> Scorecards { get; private set; } = [];

    public sealed record ScoreCardView(
        string Key,
        string Name,
        string Icon,
        int? Score,
        string Label,
        string Reason);

    /// <summary>JSON body OnPostStartCompareJobAsync's JS caller posts -- see wwwroot/js/ai-agent-progress.js's start(startBody).</summary>
    public sealed record StartCompareJobRequestBody(List<int>? SelectedIdeaIds);

    /// <summary>
    /// Single view model per idea, reused by the winner spotlight, the
    /// compact ranking cards, the expandable details, and the existing
    /// saved-score comparison table -- so the same idea never shows a
    /// different value in two places (see BuildScorecards for the saved
    /// scores and ApplyAiComparison for the Ai* fields).
    /// </summary>
    public sealed record IdeaScorecardView(
        int IdeaId,
        string Title,
        List<ScoreCardView> Cards,
        int? OverallScore,
        string OverallLabel,
        int EvaluatedCount,
        bool IsLegacyScoring)
    {
        /// <summary>
        /// Set only when OverallScore was computed from fewer than all 3
        /// metrics, so the UI never implies a full evaluation happened.
        /// </summary>
        public string? OverallPartialNote =>
            OverallScore.HasValue && EvaluatedCount < 3
                ? $"Based on {EvaluatedCount} of 3 evaluated metrics."
                : null;

        // AI qualitative ranking (see ApplyAiComparison) -- null for every
        // idea until "Generate Recommendation" succeeds, and null for any
        // idea the AI response did not rank or that failed the IdeaId
        // authorization check. Never a numeric score -- the AI comparison
        // has no score fields at all.
        public int? AiRank { get; init; }
        public string? AiContextSummary { get; init; }
        public string? AiWhyThisRank { get; init; }
        public string? AiMainStrength { get; init; }
        public string? AiMainRisk { get; init; }
        public string? AiBestFor { get; init; }
        public string? AiComparisonAdvantage { get; init; }
        public string? AiRequiredValidation { get; init; }
        public string? AiRiskLevel { get; init; }
        public string? AiRecommendation { get; init; }
    }

    public StudentProfile? Profile { get; private set; }

    public List<StudentSkill> Skills { get; private set; } = [];

    public IdeaComparisonServiceResponse? ComparisonResponse { get; private set; }

    public IdeaComparisonDto? Comparison => ComparisonResponse?.Comparison;

    public string? ErrorMessage { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated comparison.
    /// Unlike Project DNA (one existing idea), one comparison covers a
    /// batch of the student's own already-generated ideas at once (there is
    /// no single ProjectIdeaId to key off), so this is scoped by UserId +
    /// AgentName only -- reuses the same AiOutputReview entity, no new
    /// column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "reviewed" => ("bg-success", "Reviewed"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe comparison"),
        "review_rejected" => ("bg-danger", "Rejected · showing safe comparison"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Semantic review unavailable"),
        // Deterministic checks (firewall + structural) passed and the
        // ranking below is this batch's real AI output -- semantic review
        // was skipped because too little of the request's time budget
        // remained, not because anything failed. See
        // routers/idea_comparison.py's _MIN_SECONDS_FOR_SYNC_REVIEW.
        "automated_checks_passed" => ("bg-info text-dark", "Automated checks passed"),
        "review_pending" => ("bg-info text-dark", "Semantic review pending"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        // Job-based rewrite-on-rejection statuses (see
        // app/jobs/workers/idea_comparison_worker.py) -- the first semantic
        // review rejected the output, one rewrite was attempted using the
        // reviewer's own feedback, and it was then approved on re-review.
        "approved_after_revision" => ("bg-success", "Reviewed · revised after feedback"),
        // The job path's own looser blocking policy (see
        // _classify_idea_comparison_issues in the worker) -- the Reviewer
        // found only non-blocking warnings (no validated high/critical
        // issue), so the comparison shown is real AI output, not a fallback.
        "approved_with_warnings" => ("bg-success", "Reviewed · minor notes"),
        "approved_after_revision_with_warnings" => ("bg-success", "Reviewed · revised after feedback, minor notes"),
        // Rejected again after a rewrite, or no usable rewrite was
        // possible at all (no actionable feedback) -- the safe fallback is
        // shown, never a second rewrite attempt.
        "review_rejected_safe_fallback" => ("bg-danger", "Rejected · showing safe comparison"),
        // The first review rejected the output and a rewrite was
        // warranted, but too little of the job's 90s global deadline
        // remained to attempt it.
        "rewrite_unavailable_deadline" => ("bg-secondary", "Rejected · revision skipped (time limit)"),
        // The first review rejected the output, a rewrite was attempted,
        // but every provider failed during the rewrite call.
        "rewrite_provider_unavailable" => ("bg-secondary", "Rejected · revision unavailable"),
        _ => ("bg-secondary", review.Status),
    };

    /// <summary>
    /// Set when an active (queued/running/etc.) job already exists for the
    /// page's current default selection (LatestBatchIdeaIds) -- embedded
    /// into the view as data-active-job-id so ai-agent-progress.js can
    /// resume watching it on load without an extra AJAX round trip. See
    /// §7's centralized reconnect design.
    /// </summary>
    public Guid? ActiveJobId { get; private set; }

    /// <summary>
    /// The client-facing stage list for AiAgentProgress.start/attach --
    /// MUST stay in sync with app/jobs/plans.IDEA_COMPARISON_PLAN on the
    /// Python side (labels only; stage state itself always comes from the
    /// server, never guessed here).
    /// </summary>
    public static readonly (string Key, string Label)[] StagePlan =
    [
        ("prepare_context", "Preparing comparison context"),
        ("generate", "Generating recommendation"),
        ("review", "Reviewing quality"),
        ("rewrite", "Refining based on feedback"),
        ("final_checks", "Final quality checks"),
        ("save", "Saving recommendation"),
    ];

    public async Task<IActionResult> OnGetAsync(
        Guid? completedJobId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a project before opening "
                + "Idea Comparison.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadPageDataAsync(
            userId,
            cancellationToken);

        /*
         * Reconnect to the latest active comparison job for this
         * user and project.
         *
         * This is intentionally not limited to the current default
         * checkbox selection. A student may have started a comparison
         * using older ideas, moved to another page, logged out, or
         * refreshed before it completed. The durable job must remain
         * discoverable when they return.
         */
        var activeJob =
            await jobService.FindActiveJobAsync(
                userId,
                ProjectId,
                AgentName,
                cancellationToken);

        if (activeJob != null)
        {
            ActiveJobId =
                activeJob.JobId;
        }

        /*
         * First prefer the exact job supplied by the completion
         * redirect. It must belong to this user, this project, and
         * this agent.
         */
        AiAgentJob? completedJob =
            null;

        if (completedJobId.HasValue)
        {
            var requestedJob =
                await jobService.GetAuthorizedJobAsync(
                    completedJobId.Value,
                    userId,
                    cancellationToken);

            if (requestedJob != null &&
                requestedJob.ProjectId == ProjectId &&
                string.Equals(
                    requestedJob.AgentName,
                    AgentName,
                    StringComparison.Ordinal) &&
                requestedJob.Status ==
                    AiAgentJobStatus.Completed)
            {
                completedJob =
                    requestedJob;
            }
        }

        /*
         * A normal navigation, refresh, browser restart, or new login
         * no longer contains completedJobId in the URL.
         *
         * AiAgentJob.ResultJson is the durable database copy of the
         * finished AI response, so load the latest completed comparison
         * for this exact user and project directly from the database.
         *
         * There is deliberately no 24-hour cutoff here. The latest saved
         * comparison remains available until a newer comparison replaces
         * it as the latest result.
         */
        completedJob ??=
            await db.AiAgentJobs
                .AsNoTracking()
                .Where(job =>
                    job.UserId == userId &&
                    job.ProjectId == ProjectId &&
                    job.AgentName == AgentName &&
                    job.Status ==
                        AiAgentJobStatus.Completed &&
                    job.ResultJson != null &&
                    job.ResultJson != "")
                .OrderByDescending(job =>
                    job.CompletedAtUtc)
                .ThenByDescending(job =>
                    job.UpdatedAtUtc)
                .ThenByDescending(job =>
                    job.Id)
                .FirstOrDefaultAsync(
                    cancellationToken);

        if (completedJob != null)
        {
            var restored =
                await RestoreComparisonFromJobAsync(
                    completedJob,
                    cancellationToken);

            if (restored)
            {
                await LoadReviewForJobAsync(
                    completedJob.JobId,
                    userId,
                    cancellationToken);
            }
        }

        return Page();
    }

    private string ComputeRequestHash(IEnumerable<int> authorizedIdeaIds) =>
        AiAgentJobRequestHasher.ComputeHash(AgentName, ProjectId, authorizedIdeaIds.OrderBy(id => id).Select(id => id.ToString()));

    /// <summary>
    /// AJAX start handler for the centralized AI Agent Loading System --
    /// replaces the old blocking OnPostCompareAsync. Reuses the exact same
    /// authorization/validation as before (ValidateAndAuthorizeSelection);
    /// on success, creates/reuses the AiAgentJob and hands off to Python,
    /// then returns {jobId} for the browser to watch via
    /// /api/ai-agent-jobs/* (see wwwroot/js/ai-agent-progress.js). Actual
    /// generation, review, and persistence happen entirely server-side via
    /// AiAgentJobCoordinator/IdeaComparisonJobFinalizer -- this handler
    /// never blocks on the AI call itself.
    /// </summary>
    public async Task<IActionResult> OnPostStartCompareJobAsync(
        [FromBody] StartCompareJobRequestBody body,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await LoadProjectContextAsync(userId, cancellationToken))
        {
            return new JsonResult(new { message = "You do not have access to that project." }) { StatusCode = StatusCodes.Status403Forbidden };
        }

        await LoadPageDataAsync(userId, cancellationToken);

        // The JS client posts a JSON body (not form data), so this handler
        // reads the selection from [FromBody] rather than the [BindProperty]
        // SelectedIdeaIds the old form-post handler used -- same downstream
        // validation either way.
        SelectedIdeaIds = body.SelectedIdeaIds ?? [];

        var (selectedIdeas, validationError) = ValidateAndAuthorizeSelection();
        if (selectedIdeas is null)
        {
            return new JsonResult(new { message = validationError }) { StatusCode = StatusCodes.Status400BadRequest };
        }

        var requestDto = BuildComparisonRequest(selectedIdeas);
        var requestJson = JsonSerializer.Serialize(requestDto, PythonRequestJsonOptions);
        var requestHash = ComputeRequestHash(selectedIdeas.Select(idea => idea.Id));

        var startResult = await jobService.StartJobAsync(userId, ProjectId, AgentName, requestHash, requestJson, cancellationToken);

        if (startResult.CreatedNew)
        {
            var accepted = await pythonClient.StartJobAsync(AgentName, startResult.Job.JobId, requestJson, cancellationToken);

            if (accepted.Accepted)
            {
                await jobService.MarkPythonAcceptedAsync(startResult.Job.JobId, cancellationToken);
            }
            else
            {
                logger.LogWarning(
                    "Idea comparison job {JobId} could not be accepted by the AI service: {Error}",
                    startResult.Job.JobId,
                    accepted.ErrorMessage);

                // The browser still gets a jobId and will observe this
                // failure through the normal snapshot/events flow --
                // AiAgentJobCoordinator's recovery pass also retries this
                // automatically in case it was a transient hiccup.
                await jobService.FailJobAsync(
                    startResult.Job.JobId,
                    "ai_service_unreachable",
                    "The AI recommendation service could not be reached. Please try again.",
                    cancellationToken);
            }
        }

        return new JsonResult(new { jobId = startResult.Job.JobId });
    }

    private static readonly JsonSerializerOptions PythonRequestJsonOptions = new(JsonSerializerDefaults.Web);

    /// <summary>
    /// Shared authorization/validation, byte-for-byte what the old
    /// OnPostCompareAsync enforced before calling the AI service -- never
    /// trusts posted IDs blindly, only ideas already loaded for this
    /// project/user are eligible, falls back to the latest batch if the
    /// form posted no selection at all.
    /// </summary>
    private (List<ProjectIdea>? SelectedIdeas, string? ErrorMessage) ValidateAndAuthorizeSelection()
    {
        if (Ideas.Count < MinimumIdeasToCompare)
        {
            return (null, "You need at least two generated ideas before comparing.");
        }

        var authorizedIdeaIds = Ideas.Select(idea => idea.Id).ToHashSet();

        var requestedIds = SelectedIdeaIds.Count > 0
            ? SelectedIdeaIds
            : LatestBatchIdeaIds.ToList();

        var selectedIdeas = Ideas
            .Where(idea => requestedIds.Contains(idea.Id) && authorizedIdeaIds.Contains(idea.Id))
            .ToList();

        if (selectedIdeas.Count < MinimumIdeasToCompare)
        {
            return (null, $"Select at least {MinimumIdeasToCompare} ideas to compare.");
        }

        if (selectedIdeas.Count > HardMaximumIdeasToCompare)
        {
            return (null, $"Select at most {HardMaximumIdeasToCompare} ideas to compare -- " +
                $"{RecommendedMaximumIdeasToCompare} or fewer is recommended for the fastest result.");
        }

        return (selectedIdeas, null);
    }

    /// <summary>
    /// Trust boundary (see requirement to never trust AI-returned IdeaIds):
    /// only merges AI ranking fields for ideas that are both present in the
    /// AI response AND already in the authorized Ideas list loaded for this
    /// project/user. An idea ID the AI invents or a stale ID from another
    /// project is silently ignored, never rendered.
    /// </summary>
    private void ApplyAiComparison(IdeaComparisonDto comparison)
    {
        if (!comparison.Available || comparison.Ideas.Count == 0)
        {
            return;
        }

        var authorizedIds = Ideas.Select(idea => idea.Id).ToHashSet();

        var authorizedRankedIdeas = comparison.Ideas
            .Where(idea => authorizedIds.Contains(idea.IdeaId))
            .OrderBy(idea => idea.Rank)
            .ToList();

        if (authorizedRankedIdeas.Count == 0 || !authorizedIds.Contains(comparison.BestIdeaId))
        {
            logger.LogWarning(
                "Idea comparison response referenced no ideas from the authorized set for project {ProjectId}; discarding AI ranking.",
                ProjectId);

            return;
        }

        var byId = comparison.Ideas.ToDictionary(idea => idea.IdeaId);

        Scorecards = Scorecards
            .Select(card => byId.TryGetValue(card.IdeaId, out var ranked) && authorizedIds.Contains(ranked.IdeaId)
                ? card with
                {
                    AiRank = ranked.Rank,
                    AiContextSummary = ranked.ContextSummary,
                    AiWhyThisRank = ranked.WhyThisRank,
                    AiMainStrength = ranked.MainStrength,
                    AiMainRisk = ranked.MainRisk,
                    AiBestFor = ranked.BestFor,
                    AiComparisonAdvantage = ranked.ComparisonAdvantage,
                    AiRequiredValidation = ranked.RequiredValidation,
                    AiRiskLevel = ranked.RiskLevel,
                    AiRecommendation = ranked.Recommendation,
                }
                : card)
            .ToList();
    }

    /// <summary>
    /// Rebuilds the persisted comparison view from the durable
    /// AiAgentJob.ResultJson value.
    ///
    /// The response is never trusted to authorize idea access. Any idea
    /// referenced by the stored result is loaded only when it still belongs
    /// to this project, and ApplyAiComparison performs the final authorized
    /// ID check before merging AI reasoning into the scorecards.
    /// </summary>
    private async Task<bool> RestoreComparisonFromJobAsync(
        AiAgentJob job,
        CancellationToken cancellationToken)
    {
        var response =
            IdeaComparisonJobFinalizer
                .ReconstructComparisonResponse(
                    job.ResultJson);

        if (response == null)
        {
            logger.LogWarning(
                "Completed Idea Comparison job {JobId} "
                + "contains an unreadable result for project "
                + "{ProjectId}, user {UserId}.",
                job.JobId,
                ProjectId,
                UserId());

            return false;
        }

        /*
         * The normal page query intentionally displays only the newest
         * twelve ideas. A valid saved comparison may reference an older
         * project idea that has since fallen outside that window.
         *
         * Load only those missing IDs that still belong to this project.
         * This keeps an older persisted comparison renderable without
         * weakening project authorization.
         */
        var comparedIdeaIds =
            response.Comparison.Ideas
                .Select(item =>
                    item.IdeaId)
                .Append(
                    response.Comparison.BestIdeaId)
                .Where(ideaId =>
                    ideaId > 0)
                .Distinct()
                .ToList();

        var loadedIdeaIds =
            Ideas
                .Select(idea =>
                    idea.Id)
                .ToHashSet();

        var missingIdeaIds =
            comparedIdeaIds
                .Where(ideaId =>
                    !loadedIdeaIds.Contains(
                        ideaId))
                .ToList();

        if (missingIdeaIds.Count > 0)
        {
            var selectedIdeaId =
                CurrentProject?.ProjectIdeaId;

            var missingIdeas =
                await db.ProjectIdeas
                    .AsNoTracking()
                    .Where(idea =>
                        missingIdeaIds.Contains(
                            idea.Id) &&
                        (
                            idea.GeneratedForProjectId ==
                                ProjectId ||
                            (
                                selectedIdeaId.HasValue &&
                                idea.Id ==
                                    selectedIdeaId.Value
                            )
                        ))
                    .OrderByDescending(idea =>
                        idea.CreatedAt)
                    .ThenByDescending(idea =>
                        idea.Id)
                    .ToListAsync(
                        cancellationToken);

            if (missingIdeas.Count > 0)
            {
                Ideas =
                    Ideas
                        .Concat(
                            missingIdeas)
                        .GroupBy(idea =>
                            idea.Id)
                        .Select(group =>
                            group.First())
                        .ToList();

                Scorecards =
                    BuildScorecards(
                        Ideas);

                LatestBatchIdeaIds =
                    ComputeLatestBatchIdeaIds(
                        Ideas);
            }
        }

        ComparisonResponse =
            response;

        ApplyAiComparison(
            response.Comparison);

        return true;
    }

    /// <summary>
    /// Loads the quality passport belonging to the exact comparison job
    /// currently displayed. This avoids showing a review from a different
    /// project or a different comparison run.
    /// </summary>
    private async Task LoadReviewForJobAsync(
        Guid jobId,
        int userId,
        CancellationToken cancellationToken)
    {
        LatestReview =
            await db.AiOutputReviews
                .AsNoTracking()
                .Where(review =>
                    review.JobId == jobId &&
                    review.UserId == userId &&
                    review.AgentName ==
                        AgentName)
                .OrderByDescending(review =>
                    review.CreatedAt)
                .FirstOrDefaultAsync(
                    cancellationToken);
    }

    public async Task<IActionResult> OnPostSelectAsync(
        int ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        if (ProjectAccess?.CanEdit != true)
        {
            TempData["Error"] =
                "Restore this project before selecting "
                + "the official project idea.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var idea = await db.ProjectIdeas
            .FirstOrDefaultAsync(
                item =>
                    item.Id == ideaId &&
                    item.GeneratedForProjectId ==
                        ProjectId,
                cancellationToken);

        if (idea == null)
        {
            TempData["Error"] =
                "The selected idea was not found "
                + "inside this project.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            var project = await db.Projects
                .Include(item => item.ProjectIdea)
                .FirstOrDefaultAsync(
                    item => item.Id == ProjectId,
                    cancellationToken);

            if (project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The selected project could not be found.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            var previousIdeaId =
                project.ProjectIdeaId;

            var previousIdeaTitle =
                project.ProjectIdea?.Title;

            var alreadySelected =
                previousIdeaId == idea.Id;

            /*
             * Project.ProjectIdeaId is the authoritative selected
             * idea. Keep the older IsSelected flags synchronized
             * so legacy pages and stored records cannot show two
             * selected ideas at the same time.
             */
            await ProjectIdeaSelectionSync.SyncSelectedFlagAsync(
                db,
                ProjectId,
                idea.Id,
                previousIdeaId,
                cancellationToken);

            if (alreadySelected)
            {
                await db.SaveChangesAsync(
                    cancellationToken);

                await transaction.CommitAsync(
                    cancellationToken);

                await activeProjectService
                    .ActivateProjectAsync(
                        userId,
                        ProjectId,
                        "/Student/IdeaComparison",
                        cancellationToken);

                TempData["Success"] =
                    "This idea is already selected "
                    + "for the project.";

                return RedirectToPage(
                    new
                    {
                        projectId = ProjectId
                    });
            }

            var now = DateTime.UtcNow;

            project.ProjectIdeaId = idea.Id;

            if (string.Equals(
                    project.Status,
                    "draft",
                    StringComparison.OrdinalIgnoreCase))
            {
                project.Status = "planning";
            }

            /*
             * The official selected idea defines the project title
             * (matches IdeaGenerator.OnPostSelectAsync). The very
             * first idea ever attached always claims the title, even
             * over a name the student picked beforehand. After that,
             * a manual rename (IsTitleCustom) is respected and future
             * idea replacements no longer touch the title.
             */
            if (!previousIdeaId.HasValue || !project.IsTitleCustom)
            {
                project.Title = idea.Title;
                project.IsTitleCustom = false;
            }

            if (string.IsNullOrWhiteSpace(
                    project.Description))
            {
                project.Description =
                    !string.IsNullOrWhiteSpace(
                        idea.ProblemStatement)
                        ? idea.ProblemStatement
                        : idea.WhyUseful;
            }

            if (string.IsNullOrWhiteSpace(
                    project.Technologies))
            {
                project.Technologies =
                    idea.RequiredTechnologies;
            }

            project.UpdatedAt = now;

            var actorName =
                User.FindFirst(
                    ClaimTypes.Name)?.Value
                ?? "A project member";

            var replacingIdea =
                previousIdeaId.HasValue;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId,
                    ActionType = replacingIdea
                        ? "idea_replaced"
                        : "idea_selected",
                    Description = replacingIdea
                        ? $"{actorName} replaced "
                          + $"\"{previousIdeaTitle ?? "the previous idea"}\" "
                          + $"with \"{idea.Title}\"."
                        : $"{actorName} selected "
                          + $"\"{idea.Title}\" as the "
                          + "official project idea.",
                    PreviousIdeaId = previousIdeaId,
                    NewIdeaId = idea.Id,
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            /*
             * The project change is already committed. Notification
             * delivery is intentionally best-effort and must never
             * roll back a successfully selected official idea.
             */
            try
            {
                await NotifyProjectObserversAsync(
                    actorUserId: userId,
                    actorName: actorName,
                    projectTitle: project.Title,
                    ideaId: idea.Id,
                    ideaTitle: idea.Title,
                    replacingIdea: replacingIdea,
                    supervisorId: project.SupervisorId,
                    supervisorAssignmentStatus:
                        project.SupervisorAssignmentStatus,
                    cancellationToken: cancellationToken);
            }
            catch (Exception notificationException)
            {
                logger.LogWarning(
                    notificationException,
                    "The official idea was saved for project "
                    + "{ProjectId}, but its notifications "
                    + "could not be prepared.",
                    ProjectId);
            }

            await activeProjectService
                .ActivateProjectAsync(
                    userId,
                    ProjectId,
                    "/Student/IdeaComparison",
                    cancellationToken);

            TempData["Success"] = replacingIdea
                ? "The project idea was changed successfully. "
                  + "The project title was updated."
                : "The project idea was selected successfully. "
                  + "The project title was updated.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
        catch
        {
            await transaction.RollbackAsync(
                cancellationToken);

            TempData["Error"] =
                "The project idea could not be selected. "
                + "Please try again.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }
    }

    private async Task NotifyProjectObserversAsync(
        int actorUserId,
        string actorName,
        string? projectTitle,
        int ideaId,
        string? ideaTitle,
        bool replacingIdea,
        int? supervisorId,
        string? supervisorAssignmentStatus,
        CancellationToken cancellationToken)
    {
        var safeActorName =
            string.IsNullOrWhiteSpace(actorName)
                ? "A project member"
                : actorName.Trim();

        var safeProjectTitle =
            string.IsNullOrWhiteSpace(projectTitle)
                ? "Untitled Project"
                : projectTitle.Trim();

        var safeIdeaTitle =
            string.IsNullOrWhiteSpace(ideaTitle)
                ? "the selected idea"
                : ideaTitle.Trim();

        var title = replacingIdea
            ? "Official project idea changed"
            : "Official project idea selected";

        var message = replacingIdea
            ? $"{safeActorName} changed the official idea "
              + $"for \"{safeProjectTitle}\" to "
              + $"\"{safeIdeaTitle}\"."
            : $"{safeActorName} selected "
              + $"\"{safeIdeaTitle}\" as the official "
              + $"idea for \"{safeProjectTitle}\".";

        var type = replacingIdea
            ? "project_idea_replaced"
            : "project_idea_selected";

        /*
         * Notify every other active student member. Archived
         * memberships remain active and read-only, so they still
         * receive project updates.
         */
        var memberRecipientIds =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId == ProjectId &&
                    member.Status == "active" &&
                    member.UserId != actorUserId)
                .Select(member => member.UserId)
                .Distinct()
                .ToListAsync(cancellationToken);

        foreach (var recipientId in memberRecipientIds)
        {
            await TryNotifyAsync(
                recipientUserId: recipientId,
                title: title,
                message: message,
                type: type,
                url:
                    $"/Student/IdeaComparison"
                    + $"?projectId={ProjectId}",
                projectId: ProjectId,
                actorUserId: actorUserId,
                cancellationToken: cancellationToken);
        }

        var supervisorIsActive =
            supervisorId.HasValue &&
            supervisorId.Value > 0 &&
            supervisorId.Value != actorUserId &&
            string.Equals(
                supervisorAssignmentStatus,
                "active",
                StringComparison.OrdinalIgnoreCase);

        if (!supervisorIsActive ||
            memberRecipientIds.Contains(
                supervisorId!.Value))
        {
            return;
        }

        await TryNotifyAsync(
            recipientUserId: supervisorId.Value,
            title: title,
            message: message,
            type: type,
            url:
                $"/Supervisor/IdeaReview"
                + $"?projectId={ProjectId}"
                + $"&ideaId={ideaId}",
            projectId: ProjectId,
            actorUserId: actorUserId,
            cancellationToken: cancellationToken);
    }

    private async Task TryNotifyAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url,
        int projectId,
        int actorUserId,
        CancellationToken cancellationToken)
    {
        if (recipientUserId <= 0 ||
            recipientUserId == actorUserId)
        {
            return;
        }

        try
        {
            await notificationService.NotifyUserAsync(
                recipientUserId:
                    recipientUserId,
                title:
                    title,
                message:
                    message,
                type:
                    type,
                url:
                    url,
                sendEmail:
                    false,
                projectId:
                    projectId,
                actorUserId:
                    actorUserId,
                cancellationToken:
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "The official idea changed for project "
                + "{ProjectId}, but notification delivery "
                + "failed for user {RecipientUserId}.",
                projectId,
                recipientUserId);
        }
    }

    private async Task<bool> LoadProjectContextAsync(
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
                return false;
            }

            ProjectId = activeProjectId.Value;
        }

        ProjectAccess =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (ProjectAccess == null)
        {
            return false;
        }

        CurrentProject = await db.Projects
            .AsNoTracking()
            .Include(item => item.ProjectIdea)
            .FirstOrDefaultAsync(
                item => item.Id == ProjectId,
                cancellationToken);

        if (CurrentProject == null)
        {
            return false;
        }

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/IdeaComparison",
            cancellationToken);

        return true;
    }

    private async Task LoadPageDataAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        Profile = await db.StudentProfiles
            .FirstOrDefaultAsync(
                item => item.UserId == userId,
                cancellationToken);

        Skills = await db.StudentSkills
            .Where(item => item.UserId == userId)
            .ToListAsync(cancellationToken);

        var selectedIdeaId =
            CurrentProject!.ProjectIdeaId;

        Ideas = await db.ProjectIdeas
            .Where(item =>
                item.GeneratedForProjectId == ProjectId ||
                (
                    selectedIdeaId.HasValue &&
                    item.Id ==
                        selectedIdeaId.Value
                ))
            /*
             * Always keep the official selected idea in the
             * visible comparison list, even when it is older
             * than the newest twelve generated ideas.
             */
            .OrderByDescending(item =>
                selectedIdeaId.HasValue &&
                item.Id == selectedIdeaId.Value)
            .ThenByDescending(item => item.CreatedAt)
            .Take(12)
            .ToListAsync(cancellationToken);

        Scorecards = BuildScorecards(Ideas);
        LatestBatchIdeaIds = ComputeLatestBatchIdeaIds(Ideas);
    }

    /// <summary>
    /// The default comparison selection: every idea saved in the same
    /// Generate/Regenerate click as the most recently created idea. Falls
    /// back to just that single most recent idea when it predates
    /// GenerationBatchId (legacy rows), rather than grouping unrelated
    /// ideas together by guessing from timestamps.
    /// </summary>
    private static HashSet<int> ComputeLatestBatchIdeaIds(List<ProjectIdea> ideas)
    {
        var mostRecent = ideas.OrderByDescending(idea => idea.CreatedAt).FirstOrDefault();

        if (mostRecent == null)
        {
            return [];
        }

        if (mostRecent.GenerationBatchId is not { } batchId)
        {
            return [mostRecent.Id];
        }

        return ideas
            .Where(idea => idea.GenerationBatchId == batchId)
            .Take(HardMaximumIdeasToCompare)
            .Select(idea => idea.Id)
            .ToHashSet();
    }

    /// <summary>
    /// Single source of truth for the score-card grid: reads only the
    /// saved InnovationScore/FeasibilityScore/MarketDemandScore (and their
    /// Reason columns) already on ProjectIdea. A score of 0 means the AI
    /// service never returned a value for it -- shown as "Not evaluated",
    /// never substituted with a placeholder number.
    /// </summary>
    private static List<IdeaScorecardView> BuildScorecards(List<ProjectIdea> ideas) =>
        ideas.Select(idea =>
        {
            var cards = new List<ScoreCardView>
            {
                BuildScoreCard("innovation", "Innovation", "bi-lightbulb", idea.InnovationScore, idea.InnovationScoreReason),
                BuildScoreCard("feasibility", "Feasibility", "bi-bar-chart-line", idea.FeasibilityScore, idea.FeasibilityScoreReason),
                BuildScoreCard("market", "Market Demand", "bi-shop", idea.MarketDemandScore, idea.MarketDemandScoreReason),
            };

            var evaluatedCount = new[] { idea.InnovationScore, idea.FeasibilityScore, idea.MarketDemandScore }
                .Count(ScoreHelper.IsEvaluated);

            var overall = ScoreHelper.Overall(idea.InnovationScore, idea.FeasibilityScore, idea.MarketDemandScore);
            var overallLabel = overall.HasValue ? ScoreHelper.Label(overall.Value) : "Not evaluated";

            // "v2" is the only value new saves ever write (IdeaGenerator's
            // SaveGeneratedIdeasAsync); anything else -- including the
            // pre-migration NULL rows the AddIdeaScoreVersion migration
            // backfilled to "legacy" -- is scored by the old formula.
            var isLegacyScoring = idea.ScoreVersion != "v2";

            return new IdeaScorecardView(idea.Id, idea.Title, cards, overall, overallLabel, evaluatedCount, isLegacyScoring);
        }).ToList();

    private static ScoreCardView BuildScoreCard(string key, string name, string icon, int rawScore, string? reason)
    {
        var evaluated = ScoreHelper.IsEvaluated(rawScore);

        var reasonText = evaluated
            ? (string.IsNullOrWhiteSpace(reason)
                ? "Explanation was not saved when this idea was generated."
                : reason)
            : "This score was not evaluated for this idea.";

        return new ScoreCardView(
            key,
            name,
            icon,
            evaluated ? rawScore : null,
            evaluated ? ScoreHelper.Label(rawScore) : "Not evaluated",
            reasonText);
    }

    private IdeaComparisonRequest BuildComparisonRequest(List<ProjectIdea> selectedIdeas)
    {
        var studentSkills = Skills
            .Select(s => GetString(s, "SkillName", "Name", "Title"))
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct()
            .ToList();

        var skillRatings = new Dictionary<string, int>();

        foreach (var skill in Skills)
        {
            var skillName = GetString(skill, "SkillName", "Name", "Title");

            if (string.IsNullOrWhiteSpace(skillName))
            {
                continue;
            }

            var rating = GetInt(skill, 3, "Rating", "Level", "SkillLevel", "Score");
            skillRatings[skillName] = rating;
        }

        // Scores/reasons are sent as read-only evidence only (see the
        // prompt built in IdeaComparisonAgent._build_prompt) -- 0 already
        // means "not evaluated" throughout this page (ScoreHelper), so no
        // separate "missing" sentinel is needed here; the AI is explicitly
        // told a 0 is unknown, not a real low score.
        var ideaDtos = selectedIdeas
            .Select(i => new IdeaComparisonInputDto(
                Id: i.Id,
                Title: GetString(i, "Title", "IdeaTitle", "Name"),
                ProblemStatement: GetString(i, "ProblemStatement", "Problem", "Description"),
                WhyUseful: GetString(i, "WhyUseful"),
                TargetUsers: GetString(i, "TargetUsers"),
                RequiredTechnologies: GetString(i, "RequiredTechnologies", "Technologies", "TechStack"),
                RequiredSkills: GetString(i, "RequiredSkills"),
                MissingSkills: GetString(i, "MissingSkills"),
                DifficultyLevel: GetString(i, "DifficultyLevel", "Difficulty"),
                ExpectedDurationWeeks: GetInt(i, 10, "ExpectedDurationWeeks", "DurationWeeks"),
                DatasetNeeded: GetString(i, "DatasetNeeded", "DatasetRequirement", "Dataset"),
                Domain: GetString(i, "Domain", "ProjectDomain"),
                LebaneseMarketRelevance: GetString(i, "LebaneseMarketRelevance", "LebanesesMarketRelevance", "LocalMarketRelevance"),
                InnovationScore: GetDouble(i, 0, "InnovationScore"),
                InnovationScoreReason: GetNullableString(i, "InnovationScoreReason"),
                FeasibilityScore: GetDouble(i, 0, "FeasibilityScore"),
                FeasibilityScoreReason: GetNullableString(i, "FeasibilityScoreReason"),
                MarketDemandScore: GetDouble(i, 0, "MarketDemandScore", "MarketRelevanceScore"),
                MarketDemandScoreReason: GetNullableString(i, "MarketDemandScoreReason"),
                CreatedAt: GetDateString(i, "CreatedAt")
            ))
            .ToList();

        return new IdeaComparisonRequest(
            StudentMajor: GetString(Profile, "Major"),
            ExperienceLevel: GetString(Profile, "ExperienceLevel"),
            TeamSize: Math.Clamp(
                CurrentProject?.MaximumMembers ?? 1,
                1,
                MaximumProjectMembers),
            AvailableHoursPerWeek: GetInt(Profile, 10, "AvailableHoursPerWeek"),
            StudentSkills: studentSkills,
            SkillRatings: skillRatings,
            Ideas: ideaDtos
        );
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

    private static string GetString(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            return value.ToString() ?? "";
        }

        return "";
    }

    private static string? GetNullableString(object? obj, params string[] propertyNames)
    {
        var value = GetString(obj, propertyNames);
        return string.IsNullOrWhiteSpace(value) ? null : value;
    }

    private static int GetInt(object? obj, int defaultValue, params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (value is double doubleValue)
            {
                return Convert.ToInt32(doubleValue);
            }

            if (value is decimal decimalValue)
            {
                return Convert.ToInt32(decimalValue);
            }

            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static double GetDouble(object? obj, double defaultValue, params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is double doubleValue)
            {
                return doubleValue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (value is decimal decimalValue)
            {
                return Convert.ToDouble(decimalValue);
            }

            if (double.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static string GetDateString(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is DateTime dateTime)
            {
                return dateTime.ToString("O");
            }

            return value.ToString() ?? "";
        }

        return "";
    }
}