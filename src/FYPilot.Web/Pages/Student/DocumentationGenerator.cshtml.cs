using System.Reflection;
using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.Mvc.Rendering;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DocumentationGeneratorModel : PageModel
{
    private readonly IDocumentationGeneratorService _documentationGeneratorService;
    private readonly ApplicationDbContext _db;
    private readonly IProjectAccessService _projectAccessService;
    private readonly IActiveProjectService _activeProjectService;

    public DocumentationGeneratorModel(
        IDocumentationGeneratorService documentationGeneratorService,
        ApplicationDbContext db,
        IProjectAccessService projectAccessService,
        IActiveProjectService activeProjectService)
    {
        _documentationGeneratorService = documentationGeneratorService;
        _db = db;
        _projectAccessService = projectAccessService;
        _activeProjectService = activeProjectService;
    }

    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    [BindProperty]
    public new GenerateDocumentationRequest Request { get; set; } = new();

    [BindProperty]
    public int SelectedProjectIdeaId { get; set; }

    public List<SelectListItem> ProjectIdeaOptions { get; private set; } = [];

    public List<ProjectIdea> ProjectIdeas { get; private set; } = [];

    public ProjectIdea? SelectedIdea { get; private set; }

    public GeneratedDocumentationDto? GeneratedDocumentation { get; private set; }

    public string Message { get; private set; } = string.Empty;

    public string? ErrorMessage { get; private set; }

    public bool HasSelectedIdea => SelectedIdea != null;

    public bool HasGeneratedDocumentation => GeneratedDocumentation != null;

    /// <summary>
    /// The AI Quality Passport for the most recently generated SE
    /// documentation for this idea, loaded from the database so it survives
    /// the Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as Mentor Chat and Roadmap -- linked via
    /// ProjectIdeaId, no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Approved"),
        "approved_with_minor_warnings" => ("bg-success", "Approved with warnings"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe documentation"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Structural validation completed; semantic review unavailable"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    /// <summary>
    /// Which stage actually produced the persisted document's text. Derived
    /// from AiOutputReview.WasRewritten rather than a dedicated column --
    /// "structural_repair" as a distinct origin is not currently persisted,
    /// so a repaired-then-kept candidate reads as "Writer" here. See the
    /// remaining-risks note in the SE Documentation reliability writeup.
    /// </summary>
    public string DescribeOutputOrigin(AiOutputReview review) =>
        review.WasRewritten ? "Writer, revised after review" : "Writer";

    /// <summary>
    /// Generation provenance for the currently displayed document -- either
    /// freshly produced by this request (right after a successful Generate)
    /// or loaded from the last persisted AiOutputReview for this idea. One
    /// shape so the Razor page has a single rendering path either way.
    /// </summary>
    public sealed record ProvenanceView(
        string? Source,
        string? Provider,
        string? Model,
        string ReviewLevelLabel,
        string ReviewLevelCssClass,
        string OutputOriginLabel,
        string? Warning,
        DateTime? GeneratedAt);

    public ProvenanceView? Provenance { get; private set; }

    /// <summary>
    /// True when the most recent generation attempt failed but a previously
    /// persisted valid document for this idea is still being shown.
    /// </summary>
    public bool PreviousDocumentPreserved { get; private set; }

    /// <summary>
    /// Compact, presentation-safe quality/review summary for the currently
    /// displayed document -- built entirely from AiOutputReview's own
    /// persisted fields (never re-derived transiently), so it reads exactly
    /// the same right after a fresh Generate and after a plain page reload.
    /// A document with any core-section fallback content is never persisted
    /// at all (see the Python/​.NET strict acceptance gate), so this panel
    /// does not need to separately track that case -- only "semantic review
    /// completed" and "unresolved findings" can legitimately qualify a
    /// numeric score here.
    /// </summary>
    public sealed record QualityPanelView(
        int? OverallScore,
        string ReviewStatusLabel,
        string ReviewStatusCssClass,
        string OutputReviewLevelLabel,
        bool SemanticReviewCompleted,
        string ProvenanceSummary,
        int ImportantWarningCount,
        DateTime? LastGeneratedAt);

    public QualityPanelView? QualityPanel { get; private set; }

    /// <summary>
    /// The only two AiOutputReview.Status values ReviewPipeline.run() ever
    /// emits for a genuinely accepted candidate -- see
    /// services/FYPilot.AI/app/review/pipeline.py line ~292:
    /// `status = "approved" if not findings.issues else "approved_with_minor_warnings"`.
    /// This is the ONLY place in the pipeline a status is assigned for the
    /// accepted path; every other status ("unresolved", "rejected",
    /// "firewall_blocked", "review_unavailable", "provider_unavailable",
    /// "schema_invalid") is a non-accepted outcome, even when it also
    /// happens to report Usable=true (see IsAcceptedForPrint's docstring
    /// for why "unresolved" is exactly this trap). Deliberately an
    /// explicit allowlist, not "anything that isn't rejected" -- an
    /// unrecognized/future status must fail safely toward manual review,
    /// never toward silently being treated as accepted.
    /// </summary>
    private static readonly HashSet<string> AcceptedReviewStatuses = new(StringComparer.Ordinal)
    {
        "approved",
        "approved_with_minor_warnings",
    };

    /// <summary>
    /// Pure, testable check for whether the currently displayed document
    /// may be described as fully accepted AI output in the print/PDF
    /// status summary (DocumentationGenerator.cshtml's .doc-print-status
    /// section, added to fix that panel being silently absent from
    /// exported PDFs). Not a new review classifier -- every input is one
    /// of the exact same already-persisted/computed fields the on-screen
    /// Quality Passport already reads.
    ///
    /// All four conditions are required:
    /// - review.Usable: the single most authoritative "was this really
    ///   accepted" field on its own is NOT sufficient -- "unresolved"
    ///   reports Usable=true too (it means "shown as-is", not "rejected"),
    ///   so Usable alone would wrongly accept a document with known
    ///   unresolved findings.
    /// - quality.SemanticReviewCompleted: rules out review_unavailable/
    ///   provider_unavailable (see BuildQualityPanel).
    /// - review.Status is an EXPLICIT member of AcceptedReviewStatuses:
    ///   the actual fix for the contradiction above -- "unresolved" has
    ///   Usable=true AND SemanticReviewCompleted=true (it completed
    ///   semantic review; the findings just weren't fully resolved), so
    ///   without this explicit allowlist check the first two conditions
    ///   alone would still misclassify it as accepted. Only "approved"/
    ///   "approved_with_minor_warnings" pass this check.
    /// </summary>
    public static bool IsAcceptedForPrint(AiOutputReview? review, QualityPanelView? quality) =>
        review is not null
        && quality is not null
        && review.Usable
        && quality.SemanticReviewCompleted
        && AcceptedReviewStatuses.Contains(review.Status);

    private static string DescribeOutputReviewLevel(string status) => status switch
    {
        "approved" => "Fully reviewed, no findings",
        "approved_with_minor_warnings" => "Fully reviewed, minor warnings only",
        "unresolved" => "Reviewed -- unresolved findings remain",
        "rejected" => "Reviewed -- rejected candidate replaced with a safe version",
        "review_unavailable" => "Structural checks only -- semantic review did not complete",
        "provider_unavailable" => "Not reviewed -- no AI provider responded",
        "schema_invalid" => "Not reviewed -- output never became structurally valid",
        _ => status,
    };

    private static int CountImportantWarnings(string issuesJson)
    {
        try
        {
            var issues = JsonSerializer.Deserialize<List<ReviewerIssueDto>>(
                issuesJson,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true });

            return issues?.Count(issue =>
                issue.Severity.Equals("high", StringComparison.OrdinalIgnoreCase) ||
                issue.Severity.Equals("critical", StringComparison.OrdinalIgnoreCase)) ?? 0;
        }
        catch (JsonException)
        {
            return 0;
        }
    }

    private static QualityPanelView BuildQualityPanel(AiOutputReview review, DescribeReviewDelegate describeReview)
    {
        var (cssClass, label) = describeReview(review);
        var providerParts = new[] { review.GeneratorProvider, review.GeneratorModel }
            .Where(part => !string.IsNullOrWhiteSpace(part));

        return new QualityPanelView(
            OverallScore: review.QualityScore,
            ReviewStatusLabel: label,
            ReviewStatusCssClass: cssClass,
            OutputReviewLevelLabel: DescribeOutputReviewLevel(review.Status),
            SemanticReviewCompleted: review.Status != "review_unavailable" && review.Status != "provider_unavailable",
            ProvenanceSummary: providerParts.Any() ? string.Join(" · ", providerParts) : "Unknown",
            ImportantWarningCount: CountImportantWarnings(review.IssuesJson),
            LastGeneratedAt: review.CreatedAt);
    }

    private delegate (string CssClass, string Label) DescribeReviewDelegate(AiOutputReview review);

    private void SetProvenanceFromLatestReview(DateTime? generatedAt)
    {
        if (LatestReview == null)
        {
            Provenance = null;
            return;
        }

        var (cssClass, label) = DescribeReview(LatestReview);

        Provenance = new ProvenanceView(
            Source: LatestReview.GeneratorProvider,
            Provider: LatestReview.GeneratorProvider,
            Model: LatestReview.GeneratorModel,
            ReviewLevelLabel: label,
            ReviewLevelCssClass: cssClass,
            OutputOriginLabel: DescribeOutputOrigin(LatestReview),
            Warning: null,
            GeneratedAt: generatedAt);
    }

    private async Task<IActionResult?> LoadProjectContextAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        /*
         * Use projectId from the URL or posted form when
         * available. Otherwise restore the student's active
         * project workspace.
         */
        if (ProjectId <= 0)
        {
            var activeProjectId =
                await _activeProjectService
                    .GetActiveProjectIdAsync(
                        userId,
                        cancellationToken);

            if (!activeProjectId.HasValue)
            {
                TempData["Error"] =
                    "Choose a project before opening "
                    + "SE Documentation.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            ProjectId = activeProjectId.Value;
        }

        /*
         * Never trust projectId directly from the request.
         */
        var access =
            await _projectAccessService.GetAccessAsync(
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

        var projectExists =
            await _db.Projects
                .AsNoTracking()
                .AnyAsync(
                    item => item.Id == ProjectId,
                    cancellationToken);

        if (!projectExists)
        {
            TempData["Error"] =
                "The selected project could not be found.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await _activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/DocumentationGenerator",
            cancellationToken);

        return null;
    }

    private async Task LoadLatestReviewAsync(int ideaId, int userId)
    {
        LatestReview = await _db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.ProjectIdeaId == ideaId &&
                r.UserId == userId &&
                r.AgentName == "SEDocumentationAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();

        QualityPanel = LatestReview != null
            ? BuildQualityPanel(LatestReview, DescribeReview)
            : null;
    }

    public async Task<IActionResult> OnGetAsync(
        int? projectIdeaId,
        CancellationToken cancellationToken)
    {
        var userId = GetCurrentUserId();

        var contextResult =
            await LoadProjectContextAsync(
                userId,
                cancellationToken);

        if (contextResult != null)
        {
            return contextResult;
        }

        await LoadProjectIdeasAsync(
            userId,
            cancellationToken);

        /*
         * Documentation is scoped to the active project.
         * A supplied idea ID is accepted only when it is the
         * idea selected for this project.
         */
        var ideaIdToLoad =
            projectIdeaId.HasValue &&
            ProjectIdeas.Any(idea =>
                idea.Id == projectIdeaId.Value)
                ? projectIdeaId
                : ProjectIdeas
                    .Select(idea => (int?)idea.Id)
                    .FirstOrDefault();

        if (ideaIdToLoad.HasValue &&
            ideaIdToLoad.Value > 0)
        {
            await LoadSelectedIdeaIntoFormAsync(
                ideaIdToLoad.Value,
                userId,
                cancellationToken);
        }
        else
        {
            Request = new GenerateDocumentationRequest
            {
                UserId = userId
            };
        }

        BuildProjectIdeaOptions();

        return Page();
    }

    public async Task<IActionResult> OnPostAsync(
        CancellationToken cancellationToken)
    {
        return await OnPostGenerateAsync(
            cancellationToken);
    }

    public async Task<IActionResult> OnPostGenerateAsync(
        CancellationToken cancellationToken)
    {
        var userId = GetCurrentUserId();

        var contextResult =
            await LoadProjectContextAsync(
                userId,
                cancellationToken);

        if (contextResult != null)
        {
            return contextResult;
        }

        await LoadProjectIdeasAsync(
            userId,
            cancellationToken);

        if (SelectedProjectIdeaId <= 0 &&
            Request.ProjectIdeaId > 0)
        {
            SelectedProjectIdeaId =
                Request.ProjectIdeaId;
        }

        if (SelectedProjectIdeaId <= 0 &&
            ProjectIdeas.Count == 1)
        {
            SelectedProjectIdeaId =
                ProjectIdeas[0].Id;
        }

        if (SelectedProjectIdeaId <= 0)
        {
            ErrorMessage = "Please select a saved project idea before generating software documentation.";
            ModelState.AddModelError(nameof(SelectedProjectIdeaId), ErrorMessage);
            BuildProjectIdeaOptions();
            return Page();
        }

        var idea = await GetUserProjectIdeaAsync(
            SelectedProjectIdeaId,
            userId,
            cancellationToken);

        if (idea == null)
        {
            ErrorMessage = "The selected project idea was not found or does not belong to your account.";
            ModelState.AddModelError(nameof(SelectedProjectIdeaId), ErrorMessage);
            BuildProjectIdeaOptions();
            return Page();
        }

        SelectedIdea = idea;
        Request = BuildRequestFromIdea(idea);
        Request.UserId = userId;
        Request.ProjectIdeaId = SelectedProjectIdeaId;

        ModelState.Clear();

        if (string.IsNullOrWhiteSpace(Request.ProjectTitle))
        {
            ModelState.AddModelError("Request.ProjectTitle", "Project title is required.");
        }

        if (string.IsNullOrWhiteSpace(Request.ProjectDescription))
        {
            ModelState.AddModelError("Request.ProjectDescription", "Project description is required.");
        }

        if (!ModelState.IsValid)
        {
            ErrorMessage = "The selected project idea does not contain enough information to generate documentation.";
            BuildProjectIdeaOptions();
            return Page();
        }

        var result = await _documentationGeneratorService.GenerateAsync(Request);

        if (result.Succeeded && result.Documentation is not null)
        {
            GeneratedDocumentation = result.Documentation;
            await LoadLatestReviewAsync(SelectedProjectIdeaId, userId);

            var (cssClass, label) = LatestReview != null
                ? DescribeReview(LatestReview)
                : ("bg-success", result.ReviewStatus ?? "Reviewed");

            Provenance = new ProvenanceView(
                Source: result.Source,
                Provider: result.Provider,
                Model: result.Model,
                ReviewLevelLabel: label,
                ReviewLevelCssClass: cssClass,
                OutputOriginLabel: LatestReview != null ? DescribeOutputOrigin(LatestReview) : "Writer",
                Warning: string.IsNullOrWhiteSpace(result.Warning) ? null : result.Warning,
                GeneratedAt: result.Documentation.CreatedAt);

            Message = string.IsNullOrWhiteSpace(result.Warning)
                ? "AI software engineering documentation generated successfully for the selected project idea."
                : $"AI software engineering documentation generated successfully, with a review note: {result.Warning}";
        }
        else
        {
            // No fallback is ever created or persisted -- GenerateAsync only
            // returns Succeeded=false when nothing usable was produced.
            // Whatever was already saved for this idea is untouched; reload
            // it so the page keeps showing real, previously-generated content
            // instead of going blank.
            ErrorMessage = result.UserMessage
                ?? "AI documentation could not be generated. No fallback documentation was saved.";

            GeneratedDocumentation = await _documentationGeneratorService.GetLatestForIdeaAsync(
                SelectedProjectIdeaId,
                cancellationToken);
            await LoadLatestReviewAsync(SelectedProjectIdeaId, userId);

            if (GeneratedDocumentation != null)
            {
                PreviousDocumentPreserved = true;
                SetProvenanceFromLatestReview(GeneratedDocumentation.CreatedAt);
            }
        }

        BuildProjectIdeaOptions();

        return Page();
    }

    private async Task LoadProjectIdeasAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        /*
         * Load only the idea selected for the active project.
         * This also supports collaborators because ownership of
         * the idea is not used as the project-access rule.
         */
        var project = await _db.Projects
            .AsNoTracking()
            .Include(item => item.ProjectIdea)
            .FirstOrDefaultAsync(
                item =>
                    item.Id == ProjectId &&
                    item.Members.Any(member =>
                        member.UserId == userId &&
                        member.Status == "active"),
                cancellationToken);

        ProjectIdeas =
            project?.ProjectIdea == null
                ? []
                : [project.ProjectIdea];

        BuildProjectIdeaOptions();
    }

    private void BuildProjectIdeaOptions()
    {
        ProjectIdeaOptions = new List<SelectListItem>
        {
            new SelectListItem
            {
                Value = "",
                Text = "-- Select saved project idea --",
                Selected = SelectedProjectIdeaId <= 0
            }
        };

        foreach (var idea in ProjectIdeas)
        {
            var ideaId = GetIntValue(idea, "Id") ?? 0;

            var title = GetStringValue(idea, "Title", "ProjectTitle", "Name", "IdeaTitle");

            if (string.IsNullOrWhiteSpace(title))
            {
                title = $"Project Idea #{ideaId}";
            }

            ProjectIdeaOptions.Add(new SelectListItem
            {
                Value = ideaId.ToString(),
                Text = title,
                Selected = SelectedProjectIdeaId == ideaId
            });
        }
    }

    private async Task LoadSelectedIdeaIntoFormAsync(
        int ideaId,
        int userId,
        CancellationToken cancellationToken)
    {
        var idea = await GetUserProjectIdeaAsync(
            ideaId,
            userId,
            cancellationToken);

        if (idea == null)
        {
            ErrorMessage = "The selected project idea was not found.";
            Request = new GenerateDocumentationRequest
            {
                UserId = userId
            };
            return;
        }

        SelectedIdea = idea;
        SelectedProjectIdeaId = ideaId;
        Request = BuildRequestFromIdea(idea);
        Request.UserId = userId;
        Request.ProjectIdeaId = ideaId;

        await LoadLatestReviewAsync(ideaId, userId);

        // Show the last real, persisted document for this idea on a plain
        // page load too -- not only right after a fresh Generate click.
        // GetLatestForIdeaAsync only ever returns genuine AI output (see
        // DocumentationGeneratorService.GenerateAsync), never fallback.
        GeneratedDocumentation =
        await _documentationGeneratorService
        .GetLatestForIdeaAsync(
            ideaId,
            cancellationToken);

        if (GeneratedDocumentation != null)
        {
            SetProvenanceFromLatestReview(GeneratedDocumentation.CreatedAt);
        }
    }

    private async Task<ProjectIdea?> GetUserProjectIdeaAsync(
        int ideaId,
        int userId,
        CancellationToken cancellationToken)
    {
        var project = await _db.Projects
            .AsNoTracking()
            .Include(item => item.ProjectIdea)
            .FirstOrDefaultAsync(
                item =>
                    item.Id == ProjectId &&
                    item.ProjectIdeaId == ideaId &&
                    item.Members.Any(member =>
                        member.UserId == userId &&
                        member.Status == "active"),
                cancellationToken);

        return project?.ProjectIdea;
    }

    private GenerateDocumentationRequest BuildRequestFromIdea(ProjectIdea idea)
    {
        var title = GetStringValue(
            idea,
            "Title",
            "ProjectTitle",
            "Name",
            "IdeaTitle"
        );

        var description = GetStringValue(
            idea,
            "ProjectDescription",
            "Description",
            "IdeaDescription",
            "Summary",
            "ProblemStatement",
            "Problem"
        );

        var problemStatement = GetStringValue(
            idea,
            "ProblemStatement",
            "Problem"
        );

        var targetUsers = GetStringValue(
            idea,
            "TargetUsers",
            "Users"
        );

        var whyUseful = GetStringValue(
            idea,
            "WhyUseful",
            "Usefulness",
            "Value"
        );

        if (string.IsNullOrWhiteSpace(description))
        {
            var descriptionParts = new List<string>();

            if (!string.IsNullOrWhiteSpace(problemStatement))
            {
                descriptionParts.Add($"Problem: {problemStatement}");
            }

            if (!string.IsNullOrWhiteSpace(targetUsers))
            {
                descriptionParts.Add($"Target users: {targetUsers}");
            }

            if (!string.IsNullOrWhiteSpace(whyUseful))
            {
                descriptionParts.Add($"Usefulness: {whyUseful}");
            }

            description = string.Join("\n", descriptionParts);
        }

        var domain = GetStringValue(
            idea,
            "Domain",
            "ProjectDomain",
            "Category",
            "Sector"
        );

        var techStack = GetStringValue(
            idea,
            "TechStack",
            "Technologies",
            "SuggestedTechnologies",
            "TechnologyStack",
            "RequiredTechnologies"
        );

        var combinedText = $"{title} {description} {domain} {techStack}".ToLowerInvariant();

        var containsAi =
            combinedText.Contains("ai") ||
            combinedText.Contains("artificial intelligence") ||
            combinedText.Contains("machine learning") ||
            combinedText.Contains("ml") ||
            combinedText.Contains("prediction") ||
            combinedText.Contains("recommendation") ||
            combinedText.Contains("nlp") ||
            combinedText.Contains("data science") ||
            combinedText.Contains("llm") ||
            combinedText.Contains("chatbot");

        return new GenerateDocumentationRequest
        {
            UserId = GetCurrentUserId(),
            ProjectIdeaId = GetIntValue(idea, "Id") ?? 0,
            ProjectTitle = title,
            ProjectDescription = description,
            Domain = domain,
            TechStack = techStack,
            ContainsAi = containsAi
        };
    }

    private int GetCurrentUserId()
    {
        var userIdClaim = User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdClaim, out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException("Unable to identify the current logged-in user.");
    }

    private static string GetStringValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source)?.ToString();

            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        return string.Empty;
    }

    private static int? GetIntValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return null;
    }

    private static bool GetBoolValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is bool boolValue)
            {
                return boolValue;
            }

            if (bool.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return false;
    }

    private static DateTime? GetDateTimeValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is DateTime dateTimeValue)
            {
                return dateTimeValue;
            }

            if (DateTime.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return null;
    }
}