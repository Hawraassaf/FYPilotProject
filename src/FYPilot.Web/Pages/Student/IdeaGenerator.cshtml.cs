using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class IdeaGeneratorModel(
    ApplicationDbContext db,
    IAiServiceClient aiServiceClient,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    ILogger<IdeaGeneratorModel> logger) : PageModel
{
    private const int IdeasPerView = 2;
    private const int IdeasPerGeneration = 4;
    private const int SharedCandidatePoolSize = 12;
    private const int MaximumCollaborators = 20;
    private const int MaximumProjectMembers =
        MaximumCollaborators + 1;

    private string IdeaGroupSessionKey =>
        $"IdeaGroupIndex:{ProjectId}";

    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    [BindProperty]
    public InputModel Input { get; set; } = new();

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    [BindProperty(SupportsGet = true)]
    public int? OpenIdeaId { get; set; }

    public Project? CurrentProject { get; private set; }

    public ProjectAccessResult? ProjectAccess { get; private set; }
    public int CurrentUserId { get; private set; }
    public int? SelectedIdeaId =>
        CurrentProject?.ProjectIdeaId;

    public string CurrentProjectTitle =>
        string.IsNullOrWhiteSpace(CurrentProject?.Title)
            ? "Untitled Project"
            : CurrentProject.Title.Trim();

    public List<ProjectIdea> GeneratedIdeas { get; private set; } = [];
    public List<StudentSkill> AssessedSkills { get; private set; } = [];

    /// <summary>
    /// Evidence-transparency summary for the AI response that produced
    /// <see cref="GeneratedIdeas"/> in THIS request/render cycle only --
    /// populated in OnPostAsync/OnPostRegenerateAsync right where
    /// GeneratedIdeas itself is set, from the same GenerateIdeasResponse.
    /// Both handlers return Page() (not a redirect), so this plain
    /// PageModel property survives to the Razor render without needing
    /// TempData, session, or database persistence. Null on a normal page
    /// load (OnGetAsync never sets it) -- the indicator only ever shows
    /// immediately after a fresh generate/regenerate call.
    /// </summary>
    public IdeaEvidenceSummary? EvidenceSummary { get; private set; }

    /// <summary>Latest Regional Demand Footprint snapshot per visible idea (ProjectIdeaId -> snapshot).</summary>
    public Dictionary<int, MarketOpportunitySnapshot> LatestMarketInsights { get; private set; } = [];

    /// <summary>
    /// AI Quality Passport for the most recent Market Footprint analysis per
    /// visible idea (ProjectIdeaId -> review). Reuses the same AiOutputReview
    /// entity as every other agent, keyed by ProjectIdeaId like Roadmap/SE
    /// Documentation/Project DNA since Market Footprint analyzes one
    /// existing idea at a time.
    /// </summary>
    public Dictionary<int, AiOutputReview> LatestMarketFootprintReviews { get; private set; } = [];

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    public bool HasAssessedSkills => AssessedSkills.Any();

    /// <summary>
    /// The AI Quality Passport for the most recently generated idea batch.
    /// Unlike Roadmap/SE Documentation, one idea-generation review covers a
    /// batch of 4 NEW ideas at once (there is no single existing
    /// ProjectIdeaId to key off before they're created), so this is scoped
    /// by UserId + AgentName only -- reuses the same AiOutputReview entity,
    /// no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe ideas"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    public sealed record MarketFootprintSourceView(
        string Title,
        string Url,
        string Publisher,
        bool IsVerified);

    /// <summary>View helper: parses a snapshot/region's simple string-list JSON columns.</summary>
    public static List<string> ParseStringList(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];

        try
        {
            return JsonSerializer.Deserialize<List<string>>(json) ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }

    /// <summary>View helper: parses a snapshot's SourcesJson column for display.</summary>
    public static List<MarketFootprintSourceView> ParseSources(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];

        try
        {
            using var document = JsonDocument.Parse(json);
            var results = new List<MarketFootprintSourceView>();

            foreach (var item in document.RootElement.EnumerateArray())
            {
                results.Add(new MarketFootprintSourceView(
                    item.TryGetProperty("title", out var title) ? title.GetString() ?? "" : "",
                    item.TryGetProperty("url", out var url) ? url.GetString() ?? "" : "",
                    item.TryGetProperty("publisher", out var publisher) ? publisher.GetString() ?? "" : "",
                    item.TryGetProperty("isVerified", out var verified) && verified.ValueKind == JsonValueKind.True));
            }

            return results;
        }
        catch (JsonException)
        {
            return [];
        }
    }

    public class InputModel
    {
        [Required(ErrorMessage = "Major is required.")]
        [StringLength(100)]
        public string Major { get; set; } = "Computer Science";

        [Required(ErrorMessage = "Experience level is required.")]
        public string ExperienceLevel { get; set; } = "intermediate";

        [Required(ErrorMessage = "Preferred domain is required.")]
        public string PreferredDomain { get; set; } = "General Software System";

        [Required(ErrorMessage = "Target difficulty is required.")]
        public string TargetDifficulty { get; set; } = "intermediate";

        [Range(1, 60, ErrorMessage = "Available hours must be between 1 and 60.")]
        public int AvailableHours { get; set; } = 20;

    }

    /// <summary>
    /// Request-scoped, display-safe summary of ProjectIdeaAgent's web-search
    /// evidence for the most recent generation/regeneration call. Never
    /// carries firewall rule names, provider keys, internal error details,
    /// prompts, or raw retrieved content -- only a precomputed, student-safe
    /// status line and a validated, capped list of (title, URL) sources.
    /// </summary>
    public sealed class IdeaEvidenceSummary
    {
        public required string Status { get; init; }

        /// <summary>Count of the VALIDATED safe sources actually displayed, not the raw sourceCount the AI service reported.</summary>
        public int SourceCount { get; init; }

        public List<IdeaEvidenceSourceDto> Sources { get; init; } = [];
    }

    public async Task<IActionResult> OnGetAsync(
       CancellationToken cancellationToken)
    {


        var userId = UserId();

        /*
         * Use projectId from the URL when available.
         * Otherwise restore the student's active project.
         */
        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a project before opening "
                + "the Idea Generator.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        var projectLoaded =
            await LoadProjectContextAsync(
                userId,
                cancellationToken);

        if (!projectLoaded)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        /*
         * Personal profile and skills remain private to
         * the logged-in student.
         */
        await LoadProfileIntoInputAsync(userId);

        await LoadPageDataAsync(userId);

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/IdeaGenerator",
            cancellationToken);

        return Page();
    }

    public async Task<IActionResult> OnPostAsync(
    CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken) ||
            !await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a valid project before generating ideas.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadPageDataAsync(userId);

        if (!AssessedSkills.Any())
        {
            ErrorMessage =
                "Please complete your Skill Assessment "
                + "before generating project ideas.";

            return Page();
        }

        if (!ModelState.IsValid)
        {
            ErrorMessage =
                "Please correct the generation inputs "
                + "before continuing.";

            return Page();
        }

        var project = await db.Projects
            .FirstOrDefaultAsync(
                item => item.Id == ProjectId,
                cancellationToken);

        if (project == null)
        {
            TempData["Error"] =
                "The selected project could not be found.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        CurrentProject = project;

        /*
         * Team capacity is managed only from Team Management.
         * Idea Generator reads the project's saved capacity
         * as AI context and never changes it.
         */
        var aiRequest = BuildAiRequest(
            regenerate: false,
            previousIdeaTitles: []);

        GenerateIdeasResponse? aiResponse;

        try
        {
            aiResponse =
                await aiServiceClient.GenerateIdeasAsync(
                    aiRequest);
        }
        catch (HttpRequestException exception)
        {
            logger.LogWarning(
                exception,
                "The idea-generation AI service could not be reached "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "The AI idea generator is temporarily unavailable. "
                + "Your project and existing ideas were not changed. "
                + "Please try again when the AI service is running.";

            return Page();
        }
        catch (TaskCanceledException exception)
            when (!cancellationToken.IsCancellationRequested)
        {
            logger.LogWarning(
                exception,
                "The idea-generation AI request timed out "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "The AI idea generator took too long to respond. "
                + "Your project and existing ideas were not changed. "
                + "Please try again.";

            return Page();
        }
        catch (OperationCanceledException)
            when (cancellationToken.IsCancellationRequested)
        {
            return new EmptyResult();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected idea-generation failure "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "An unexpected error occurred while generating ideas. "
                + "Your project and existing ideas were not changed.";

            return Page();
        }

        if (aiResponse?.Ideas == null ||
            !aiResponse.Ideas.Any())
        {
            ErrorMessage =
                "The AI service did not return any project ideas. "
                + "Your existing ideas were not changed. "
                + "Please try again.";

            return Page();
        }

        var entities = await SaveGeneratedIdeasAsync(
            userId,
            aiResponse.Ideas);

        await PersistReviewAsync(
            userId,
            aiResponse);

        HttpContext.Session.SetInt32(
            IdeaGroupSessionKey,
            0);

        GeneratedIdeas = entities
            .OrderBy(idea => idea.Id)
            .Take(IdeasPerView)
            .ToList();

        EvidenceSummary = BuildEvidenceSummary(aiResponse);

        var sourceText = aiResponse.LlmUsed
            ? "using the local AI model"
            : "using the AI fallback engine";

        SuccessMessage =
            $"{entities.Count} project idea(s) generated "
            + $"and saved successfully {sourceText}.";

        await LoadLatestReviewAsync(userId);

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/IdeaGenerator",
            cancellationToken);

        return Page();
    }
    public async Task<IActionResult> OnPostShuffleAsync(
       CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken) ||
            !await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        var candidateCount = await db.ProjectIdeas
            .AsNoTracking()
            .Where(idea =>
                idea.GeneratedForProjectId == ProjectId)
            .OrderByDescending(idea => idea.CreatedAt)
            .ThenByDescending(idea => idea.Id)
            .Take(SharedCandidatePoolSize)
            .CountAsync(cancellationToken);

        if (candidateCount == 0)
        {
            TempData["Error"] =
                "Generate ideas first before shuffling.";

            return RedirectToGenerator();
        }

        var currentIndex =
            HttpContext.Session.GetInt32(
                IdeaGroupSessionKey) ?? 0;

        var maximumGroups = Math.Max(
            1,
            (int)Math.Ceiling(
                candidateCount /
                (double)IdeasPerView));

        var nextIndex =
            (currentIndex + 1) % maximumGroups;

        HttpContext.Session.SetInt32(
            IdeaGroupSessionKey,
            nextIndex);

        TempData["Success"] =
            "Showing another group of project ideas.";

        return RedirectToGenerator();
    }

    public async Task<IActionResult> OnPostRegenerateAsync(
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken) ||
            !await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadProfileIntoInputAsync(userId);

        await LoadPageDataAsync(userId);

        if (!AssessedSkills.Any())
        {
            ErrorMessage =
                "Please complete your Skill Assessment "
                + "before regenerating project ideas.";

            return Page();
        }

        /*
         * Avoid repeating ideas generated by any member
         * inside this project workspace.
         */
        var previousTitles = await db.ProjectIdeas
            .AsNoTracking()
            .Where(idea =>
                idea.GeneratedForProjectId == ProjectId)
            .OrderByDescending(idea => idea.CreatedAt)
            .ThenByDescending(idea => idea.Id)
            .Take(30)
            .Select(idea => idea.Title)
            .ToListAsync(cancellationToken);

        var aiRequest = BuildAiRequest(
            regenerate: true,
            previousIdeaTitles: previousTitles);

        GenerateIdeasResponse? aiResponse;

        try
        {
            aiResponse =
                await aiServiceClient.GenerateIdeasAsync(
                    aiRequest);
        }
        catch (HttpRequestException exception)
        {
            logger.LogWarning(
                exception,
                "The idea-regeneration AI service could not be reached "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "The AI idea generator is temporarily unavailable. "
                + "Your existing ideas were kept unchanged. "
                + "Please try again when the AI service is running.";

            return Page();
        }
        catch (TaskCanceledException exception)
            when (!cancellationToken.IsCancellationRequested)
        {
            logger.LogWarning(
                exception,
                "The idea-regeneration AI request timed out "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "The AI idea generator took too long to respond. "
                + "Your existing ideas were kept unchanged. "
                + "Please try again.";

            return Page();
        }
        catch (OperationCanceledException)
            when (cancellationToken.IsCancellationRequested)
        {
            return new EmptyResult();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected idea-regeneration failure "
                + "for project {ProjectId}, user {UserId}.",
                ProjectId,
                userId);

            ErrorMessage =
                "An unexpected error occurred while regenerating ideas. "
                + "Your existing ideas were kept unchanged.";

            return Page();
        }

        if (aiResponse?.Ideas == null ||
            !aiResponse.Ideas.Any())
        {
            ErrorMessage =
                "The AI service did not return any new project ideas. "
                + "Your existing ideas were kept unchanged. "
                + "Please try again.";

            return Page();
        }

        var entities = await SaveGeneratedIdeasAsync(
            userId,
            aiResponse.Ideas);

        await PersistReviewAsync(
            userId,
            aiResponse);

        HttpContext.Session.SetInt32(
            IdeaGroupSessionKey,
            0);

        GeneratedIdeas = entities
            .OrderBy(idea => idea.Id)
            .Take(IdeasPerView)
            .ToList();

        EvidenceSummary = BuildEvidenceSummary(aiResponse);

        var sourceText = aiResponse.LlmUsed
            ? "using the local AI model"
            : "using the AI fallback engine";

        SuccessMessage =
            $"{entities.Count} new project idea(s) "
            + $"regenerated successfully {sourceText}.";

        await LoadLatestReviewAsync(userId);

        return Page();
    }
    public async Task<IActionResult> OnPostSelectAsync(
     int ideaId,
     CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a project before selecting an idea.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        /*
         * The official shared project idea is selected
         * by the project owner.
         */
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

        if (!access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can select "
                + "the official project idea.";

            return RedirectToGenerator(ideaId);
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                System.Data.IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            /*
             * During this stage, the owner can select one
             * of their own generated candidates.
             *
             * In the next database stage, candidates will
             * be scoped to the project so the owner can also
             * select a collaborator's project candidate.
             */
            var idea = await db.ProjectIdeas
     .FirstOrDefaultAsync(
         item =>
             item.Id == ideaId &&
             item.UserId == userId &&
             item.GeneratedForProjectId ==
                 ProjectId,
         cancellationToken);

            if (idea == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
    "The selected idea was not found in "
    + "this project or does not belong "
    + "to your account.";

                return RedirectToGenerator();
            }

            /*
             * The same ProjectIdea cannot be connected as
             * the official idea of two different projects.
             */
            var linkedProjectId =
                await db.Projects
                    .AsNoTracking()
                    .Where(project =>
                        project.ProjectIdeaId == ideaId)
                    .Select(project =>
                        (int?)project.Id)
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (linkedProjectId.HasValue &&
                linkedProjectId.Value != ProjectId)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This idea is already connected "
                    + "to another project.";

                return RedirectToGenerator(ideaId);
            }

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

            /*
             * Clicking the already selected idea should not
             * create another project or duplicate activity.
             */
            if (project.ProjectIdeaId == idea.Id)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Success"] =
                    "This idea is already selected "
                    + "for the project.";

                return RedirectToPage(
                    "/Student/Dashboard",
                    new
                    {
                        projectId = ProjectId
                    });
            }

            var previousIdeaId =
                project.ProjectIdeaId;

            var previousIdeaTitle =
                project.ProjectIdea?.Title;

            var now = DateTime.UtcNow;

            /*
             * This is the essential correction:
             * update the existing project.
             *
             * Do not create a new Project here.
             */
            project.ProjectIdeaId = idea.Id;

            if (string.Equals(
                    project.Status,
                    "draft",
                    StringComparison.OrdinalIgnoreCase))
            {
                project.Status = "planning";
            }

            /*
             * Preserve a title manually chosen by the user.
             * Only replace an empty or automatic title.
             */
            if (string.IsNullOrWhiteSpace(project.Title) ||
                string.Equals(
                    project.Title.Trim(),
                    "Untitled Project",
                    StringComparison.OrdinalIgnoreCase))
            {
                project.Title = idea.Title;
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

            /*
             * Keep the existing project-specific capacity.
             * Do not hardcode it as three.
             */
            project.MaximumMembers =
                Math.Clamp(
                    project.MaximumMembers,
                    1,
                    MaximumProjectMembers);

            project.UpdatedAt = now;

            /*
             * Temporary compatibility for older pages.
             * Project.ProjectIdeaId remains the real source
             * of truth for the current project.
             */
            idea.IsSelected = true;

            var actorName =
                User.FindFirst(
                    ClaimTypes.Name)?.Value
                ?? "The project owner";

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

            await activeProjectService
                .ActivateProjectAsync(
                    userId,
                    ProjectId,
                    "/Student/Dashboard",
                    cancellationToken);

            TempData["Success"] = replacingIdea
                ? "The project idea was replaced successfully."
                : "The project idea was selected successfully.";

            return RedirectToPage(
                "/Student/Dashboard",
                new
                {
                    projectId = ProjectId
                });
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to select idea {IdeaId} for "
                + "project {ProjectId}, user {UserId}.",
                ideaId,
                ProjectId,
                userId);

            TempData["Error"] =
                "The idea could not be connected "
                + "to this project.";

            return RedirectToGenerator(ideaId);
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Unexpected error while selecting idea "
                + "{IdeaId} for project {ProjectId}, "
                + "user {UserId}.",
                ideaId,
                ProjectId,
                userId);

            TempData["Error"] =
                "An unexpected error occurred while "
                + "selecting the project idea.";

            return RedirectToGenerator(ideaId);
        }
    }
    public async Task<IActionResult> OnPostAnalyzeMarketAsync(
        int ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (!await ResolveProjectIdAsync(
                userId,
                cancellationToken) ||
            !await LoadProjectContextAsync(
                userId,
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        var idea = await db.ProjectIdeas
            .FirstOrDefaultAsync(
                item =>
                    item.Id == ideaId &&
                    item.UserId == userId &&
                    item.GeneratedForProjectId ==
                        ProjectId,
                cancellationToken);

        if (idea == null)
        {
            // Idea does not exist or does not belong to this user — reject
            // without exposing whether the ID exists for someone else.
            TempData["Error"] =
                "The selected idea was not found or does not belong to your account.";

            return RedirectToPage();
        }

        var request = new MarketFootprintRequest(
            ProjectTitle: SafeText(idea.Title, 300),
            ProblemStatement: SafeText(idea.ProblemStatement, 5000),
            TargetUsers: SafeText(idea.TargetUsers, 2000),
            Domain: SafeText(idea.Domain, 200),
            Technologies: SafeText(idea.RequiredTechnologies, 2000),
            UseSearch: true);

        try
        {
            var response = await aiServiceClient.AnalyzeMarketFootprintAsync(
                request,
                cancellationToken);

            if (response == null)
            {
                TempData["Error"] =
                    "Regional evidence could not be verified right now. The " +
                    "preliminary idea-generation score is still available, but " +
                    "it is not a grounded regional analysis.";

                return RedirectToGenerator(ideaId);
            }

            if (!string.Equals(response.Status, "ready", StringComparison.OrdinalIgnoreCase)
                || response.OverallOpportunityScore == null)
            {
                // Do not persist an empty/unavailable snapshot — leave any
                // previous valid snapshot in place and just report the issue.
                TempData["Error"] =
                    "Regional evidence could not be verified right now. The " +
                    "preliminary idea-generation score is still available, but " +
                    "it is not a grounded regional analysis.";

                return RedirectToGenerator(ideaId);
            }

            var snapshot = BuildSnapshot(response, userId, ideaId);

            await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);

            db.MarketOpportunitySnapshots.Add(snapshot);

            var review = response.Review;

            if (review != null)
            {
                db.AiOutputReviews.Add(new AiOutputReview
                {
                    ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                        ? reviewRunId
                        : Guid.NewGuid(),
                    UserId = userId,
                    ProjectIdeaId = ideaId,
                    MentorChatSessionId = null,
                    AgentName = "MarketFootprintAgent",
                    Status = review.Status,
                    Usable = review.Usable,
                    WasRewritten = review.Attempts > 1,
                    Attempts = review.Attempts,
                    QualityScore = review.QualityScore,
                    DecisionReason = review.DecisionReason,
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
            await transaction.CommitAsync(cancellationToken);

            TempData["Success"] =
                $"Regional demand footprint updated — {snapshot.Regions.Count} region(s) analyzed " +
                $"with {response.Sources.Count} source(s).";

            return RedirectToGenerator(ideaId);
        }
        catch (HttpRequestException exception)
        {
            logger.LogError(
                exception,
                "Market footprint request failed for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "Regional evidence could not be verified right now. The " +
                "preliminary idea-generation score is still available, but " +
                "it is not a grounded regional analysis.";
        }
        catch (InvalidOperationException exception)
        {
            logger.LogError(
                exception,
                "Market footprint response could not be processed for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "Regional evidence could not be verified right now. The " +
                "preliminary idea-generation score is still available, but " +
                "it is not a grounded regional analysis.";
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return new EmptyResult();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected market footprint error for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] = "An unexpected error occurred while analyzing regional demand.";
        }

        return RedirectToGenerator(ideaId);
    }

    private static MarketOpportunitySnapshot BuildSnapshot(
        MarketFootprintResponse response,
        int userId,
        int ideaId)
    {
        var snapshot = new MarketOpportunitySnapshot
        {
            UserId = userId,
            ProjectIdeaId = ideaId,
            Status = SafeText(response.Status, 40),
            OverallOpportunityScore = response.OverallOpportunityScore.HasValue
                ? Math.Clamp(response.OverallOpportunityScore.Value, 0, 100)
                : null,
            OverallConfidenceScore = Math.Clamp(response.OverallConfidenceScore, 0, 100),
            OverallDemandLevel = SafeText(response.OverallDemandLevel, 30),
            BestLaunchMarket = SafeText(response.BestLaunchMarket, 120),
            BestLaunchReason = SafeText(response.BestLaunchReason, 2000),
            ExpansionPathJson = JsonSerializer.Serialize(CleanList(response.ExpansionPath, 3), JsonOpts),
            WhyDemandedJson = JsonSerializer.Serialize(CleanList(response.WhyDemanded, 3), JsonOpts),
            StrategicRecommendation = SafeText(response.StrategicRecommendation, 4000),
            LimitationsJson = JsonSerializer.Serialize(CleanList(response.Limitations, 4), JsonOpts),
            SourcesJson = JsonSerializer.Serialize(
                (response.Sources ?? [])
                    .Where(s => IsSafeHttpUrl(s.Url) && !string.IsNullOrWhiteSpace(s.Title))
                    .Take(20)
                    .Select(s => new
                    {
                        title = SafeText(s.Title, 300),
                        url = SafeText(s.Url, 2000),
                        publisher = SafeText(s.Publisher, 250),
                        relevance = SafeText(s.Relevance, 800),
                        relevanceScore = Math.Clamp(s.RelevanceScore, 0, 100),
                        isVerified = s.IsVerified,
                        regions = s.Regions ?? []
                    }),
                JsonOpts),
            GroundedInLiveData = response.GroundedInLiveData,
            Provider = SafeText(response.Provider, 120),
            ModelUsed = string.IsNullOrWhiteSpace(response.ModelUsed) ? null : SafeText(response.ModelUsed, 200),
            AnalyzedAt = response.AnalyzedAt == default ? DateTime.UtcNow : response.AnalyzedAt.ToUniversalTime(),
            CreatedAt = DateTime.UtcNow
        };

        foreach (var region in response.Regions ?? [])
        {
            snapshot.Regions.Add(new MarketOpportunityRegion
            {
                RegionKey = SafeText(region.RegionKey, 20),
                RegionName = SafeText(region.RegionName, 50),
                OpportunityScore = region.OpportunityScore.HasValue
                    ? Math.Clamp(region.OpportunityScore.Value, 0, 100)
                    : null,
                ConfidenceScore = Math.Clamp(region.ConfidenceScore, 0, 100),
                DemandLevel = SafeText(region.DemandLevel, 30),
                CompetitionPressure = NormalizeCompetitionPressure(region.CompetitionPressure),
                EvidenceSummary = SafeText(region.EvidenceSummary, 800),
                ScoreBreakdownJson = region.ScoreBreakdown == null
                    ? "{}"
                    : JsonSerializer.Serialize(
                        new
                        {
                            problemUrgency = Math.Clamp(region.ScoreBreakdown.ProblemUrgency, 0, 100),
                            geographicFit = Math.Clamp(region.ScoreBreakdown.GeographicFit, 0, 100),
                            adoptionReadiness = Math.Clamp(region.ScoreBreakdown.AdoptionReadiness, 0, 100),
                            competitionGap = Math.Clamp(region.ScoreBreakdown.CompetitionGap, 0, 100),
                            targetUserReachability = Math.Clamp(region.ScoreBreakdown.TargetUserReachability, 0, 100),
                            technologyMomentum = Math.Clamp(region.ScoreBreakdown.TechnologyMomentum, 0, 100),
                            evidenceStrength = Math.Clamp(region.ScoreBreakdown.EvidenceStrength, 0, 100)
                        },
                        JsonOpts),
                SourceUrlsJson = JsonSerializer.Serialize(
                    (region.SourceUrls ?? []).Where(IsSafeHttpUrl).Take(4),
                    JsonOpts)
            });
        }

        return snapshot;
    }

    private static string NormalizeCompetitionPressure(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "low" => "low",
        "high" => "high",
        _ => "medium"
    };

    private static List<string> CleanList(IEnumerable<string>? values, int maximum) =>
        (values ?? [])
        .Where(value => !string.IsNullOrWhiteSpace(value))
        .Select(value => SafeText(value, 500))
        .Take(maximum)
        .ToList();

    private static bool IsSafeHttpUrl(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

    private static string SafeText(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var clean = value.Trim();
        return clean.Length <= maximumLength ? clean : clean[..maximumLength];
    }

    private const int MaximumEvidenceSourcesDisplayed = 5;

    /// <summary>
    /// Builds a student-safe evidence-transparency summary from the raw AI
    /// response. Status precedence (highest first): firewall-blocked ->
    /// search-failed -> grounded-with-validated-sources -> preliminary.
    /// SourceCount reflects the VALIDATED safe-source list actually shown,
    /// never the raw, unvalidated sourceCount the AI service reported.
    /// </summary>
    private static IdeaEvidenceSummary BuildEvidenceSummary(GenerateIdeasResponse aiResponse)
    {
        var safeSources = (aiResponse.Sources ?? [])
            .Where(source => IsSafeHttpUrl(source.Url))
            .Select(source => new IdeaEvidenceSourceDto(
                SafeText(source.Title, 200),
                source.Url))
            .Take(MaximumEvidenceSourcesDisplayed)
            .ToList();

        var status = aiResponse switch
        {
            { SearchFirewallBlocked: true } =>
                "Live evidence excluded for safety · preliminary estimate",
            { SearchFailed: true } =>
                "Live evidence unavailable · preliminary estimate",
            { GroundedInLiveData: true } when safeSources.Count > 0 =>
                $"Evidence-grounded · {safeSources.Count} source(s)",
            _ =>
                "Preliminary estimate · no live evidence",
        };

        return new IdeaEvidenceSummary
        {
            Status = status,
            SourceCount = safeSources.Count,
            Sources = safeSources,
        };
    }

    private GenerateIdeasRequest BuildAiRequest(bool regenerate, List<string> previousIdeaTitles)
    {
        return new GenerateIdeasRequest(
            Major: Input.Major.Trim(),
            ExperienceLevel: Input.ExperienceLevel.Trim().ToLowerInvariant(),
            PreferredDomain: Input.PreferredDomain.Trim(),
            TargetDifficulty: Input.TargetDifficulty.Trim().ToLowerInvariant(),
            PreferredStack: "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
            AvailableHoursPerWeek: Input.AvailableHours,
            TeamMembers: Math.Clamp(
                CurrentProject?.MaximumMembers ?? 1,
                1,
                MaximumProjectMembers),
            ProjectGoals: "Build a useful final year project based on the student's skills and preferred domain.",
            Regenerate: regenerate,
            PreviousIdeaTitles: previousIdeaTitles,
            Skills: AssessedSkills
                .OrderByDescending(s => s.Rating)
                .Select(s => new GenerateIdeaSkillDto(
                    SkillName: s.SkillName,
                    Rating: Math.Clamp(s.Rating, 1, 5),
                    ProficiencyLevel: Math.Clamp(s.Rating, 1, 5)
                ))
                .ToList()
        );
    }

    private async Task PersistReviewAsync(int userId, GenerateIdeasResponse aiResponse)
    {
        var review = aiResponse.Review;

        if (review == null)
        {
            return;
        }

        db.AiOutputReviews.Add(new AiOutputReview
        {
            ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                ? reviewRunId
                : Guid.NewGuid(),
            UserId = userId,
            ProjectIdeaId = null,
            MentorChatSessionId = null,
            AgentName = "ProjectIdeaAgent",
            Status = review.Status,
            Usable = review.Usable,
            WasRewritten = review.Attempts > 1,
            Attempts = review.Attempts,
            QualityScore = review.QualityScore,
            DecisionReason = review.DecisionReason,
            GeneratorProvider = aiResponse.Provider,
            GeneratorModel = aiResponse.ModelUsed,
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

        await db.SaveChangesAsync();
    }

    private async Task LoadLatestReviewAsync(int userId)
    {
        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.UserId == userId && r.AgentName == "ProjectIdeaAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();
    }

    private async Task<List<ProjectIdea>>
     SaveGeneratedIdeasAsync(
         int userId,
         List<GeneratedIdeaDto> ideas)
    {
        var entities = new List<ProjectIdea>();

        foreach (var idea in ideas.Take(
                     IdeasPerGeneration))
        {
            var entity = new ProjectIdea
            {
                /*
                 * UserId identifies the member who generated
                 * the candidate.
                 */
                UserId = userId,

                /*
                 * GeneratedForProjectId identifies the shared
                 * project workspace containing the candidate.
                 */
                GeneratedForProjectId = ProjectId,

                Title = idea.Title,
                ProblemStatement = idea.ProblemStatement,
                TargetUsers = idea.TargetUsers,
                WhyUseful = idea.WhyUseful,

                LebaneseMarketRelevance =
                    idea.LebaneseMarketRelevance,

                RequiredTechnologies =
                    idea.RequiredTechnologies,

                RequiredSkills = idea.RequiredSkills,
                MissingSkills = idea.MissingSkills,

                DifficultyLevel =
                    idea.DifficultyLevel.ToString(),

                InnovationScore =
                    (int)Math.Round(
                        idea.InnovationScore),

                FeasibilityScore =
                    (int)Math.Round(
                        idea.FeasibilityScore),

                MarketDemandScore =
                    (int)Math.Round(
                        idea.MarketDemandScore),

                ExpectedDurationWeeks =
                    idea.ExpectedDurationWeeks,

                SupervisorCategory =
                    idea.SupervisorCategory,

                DatasetNeeded = idea.DatasetNeeded,

                FinalDeliverables =
                    idea.FinalDeliverables,

                Domain = idea.Domain,
                LebanesesSector = idea.LebaneseSector,
                IsSelected = false,
                CreatedAt = DateTime.UtcNow
            };

            db.ProjectIdeas.Add(entity);
            entities.Add(entity);
        }

        await db.SaveChangesAsync();

        return entities;
    }

    private async Task LoadProfileIntoInputAsync(
      int userId)
    {
        var profile = await db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(
                item => item.UserId == userId);

        if (profile == null)
        {
            return;
        }

        Input = new InputModel
        {
            Major =
                string.IsNullOrWhiteSpace(profile.Major)
                    ? "Computer Science"
                    : profile.Major,

            ExperienceLevel =
                string.IsNullOrWhiteSpace(
                    profile.ExperienceLevel)
                    ? "intermediate"
                    : profile.ExperienceLevel
                        .ToLowerInvariant(),

            PreferredDomain =
                string.IsNullOrWhiteSpace(
                    profile.PreferredDomain)
                    ? "General Software System"
                    : profile.PreferredDomain,

            TargetDifficulty =
                string.IsNullOrWhiteSpace(
                    profile.TargetDifficulty)
                    ? "intermediate"
                    : profile.TargetDifficulty
                        .ToLowerInvariant(),

            AvailableHours =
                profile.AvailableHoursPerWeek <= 0
                    ? 20
                    : profile.AvailableHoursPerWeek
        };
    }

    private async Task LoadPageDataAsync(
     int userId)
    {
        /*
         * Skills remain private to the logged-in member.
         */
        AssessedSkills = await db.StudentSkills
            .AsNoTracking()
            .Where(skill =>
                skill.UserId == userId)
            .OrderByDescending(skill =>
                skill.Rating)
            .ThenBy(skill =>
                skill.SkillName)
            .ToListAsync();

        /*
         * Candidate ideas are shared inside the project.
         * Do not filter them by the current user's ID.
         */
        var recentCandidates = await db.ProjectIdeas
            .AsNoTracking()
            .Include(idea => idea.User)
            .Where(idea =>
                idea.GeneratedForProjectId == ProjectId)
            .OrderByDescending(idea =>
                idea.CreatedAt)
            .ThenByDescending(idea =>
                idea.Id)
            .Take(SharedCandidatePoolSize)
            .ToListAsync();

        var orderedCandidates = recentCandidates
            .OrderBy(idea => idea.Id)
            .ToList();

        var groupIndex =
            HttpContext.Session.GetInt32(
                IdeaGroupSessionKey) ?? 0;

        var maximumGroups = Math.Max(
            1,
            (int)Math.Ceiling(
                orderedCandidates.Count /
                (double)IdeasPerView));

        if (groupIndex >= maximumGroups)
        {
            groupIndex = 0;

            HttpContext.Session.SetInt32(
                IdeaGroupSessionKey,
                groupIndex);
        }

        GeneratedIdeas = orderedCandidates
            .Skip(groupIndex * IdeasPerView)
            .Take(IdeasPerView)
            .ToList();

        await LoadLatestMarketInsightsAsync(userId);

        await LoadLatestMarketFootprintReviewsAsync(
            userId);

        await LoadLatestReviewAsync(userId);
    }

    /// <summary>
    /// Loads only the latest Regional Demand Footprint snapshot per visible
    /// idea, in a single query (no N+1), so the Idea Generator never runs
    /// live research automatically.
    /// </summary>
    private async Task LoadLatestMarketInsightsAsync(int userId)
    {
        LatestMarketInsights = [];

        var ideaIds = GeneratedIdeas.Select(i => i.Id).ToList();

        if (ideaIds.Count == 0)
        {
            return;
        }

        var snapshots = await db.MarketOpportunitySnapshots
            .AsNoTracking()
            .Include(s => s.Regions)
            .Where(s => s.UserId == userId && ideaIds.Contains(s.ProjectIdeaId))
            .OrderByDescending(s => s.AnalyzedAt)
            .ThenByDescending(s => s.Id)
            .ToListAsync();

        LatestMarketInsights = snapshots
            .GroupBy(s => s.ProjectIdeaId)
            .ToDictionary(group => group.Key, group => group.First());
    }

    /// <summary>Latest Market Footprint AI Quality Passport per visible idea.</summary>
    private async Task LoadLatestMarketFootprintReviewsAsync(int userId)
    {
        LatestMarketFootprintReviews = [];

        var ideaIds = GeneratedIdeas.Select(i => i.Id).ToList();

        if (ideaIds.Count == 0)
        {
            return;
        }

        var reviews = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.UserId == userId &&
                r.AgentName == "MarketFootprintAgent" &&
                r.ProjectIdeaId != null &&
                ideaIds.Contains(r.ProjectIdeaId.Value))
            .OrderByDescending(r => r.CreatedAt)
            .ToListAsync();

        LatestMarketFootprintReviews = reviews
            .GroupBy(r => r.ProjectIdeaId!.Value)
            .ToDictionary(group => group.Key, group => group.First());
    }
    private async Task<bool> ResolveProjectIdAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        if (ProjectId > 0)
        {
            return true;
        }

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

        return true;
    }

    private async Task<bool> LoadProjectContextAsync(
    int userId,
    CancellationToken cancellationToken)
    {
        CurrentUserId = userId;
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
            .FirstOrDefaultAsync(
                project => project.Id == ProjectId,
                cancellationToken);

        return CurrentProject != null;
    }
    private RedirectToPageResult RedirectToGenerator(
    int? openIdeaId = null)
    {
        return RedirectToPage(
            "/Student/IdeaGenerator",
            new
            {
                projectId = ProjectId,
                openIdeaId
            });
    }

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}