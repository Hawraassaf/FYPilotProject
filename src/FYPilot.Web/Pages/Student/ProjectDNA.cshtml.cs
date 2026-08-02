using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProjectDNAModel(
    ApplicationDbContext db,
    IAiServiceClient aiService,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService)
    : PageModel
{
    private static readonly JsonSerializerOptions
    DnaJsonOptions =
        new(JsonSerializerDefaults.Web)
        {
            PropertyNameCaseInsensitive = true
        };
    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public ProjectIdea? Idea { get; private set; }
    public List<StudentSkill> StudentSkills { get; private set; } = [];
    public List<(string Label, int Value, string Color)> DnaDimensions { get; private set; } = [];
    public List<RiskItem> Risks { get; private set; } = [];
    public List<ProjectDnaSkillDto> RequiredSkillsAnalysis { get; private set; } = [];
    public ProjectDnaAnalysisDto? Analysis { get; private set; }

    public bool LlmUsed { get; private set; }
    public string? Source { get; private set; }
    public string? ErrorMessage { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated DNA analysis
    /// for this idea, loaded from the database so it survives the
    /// Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as Roadmap/SE Documentation -- linked via
    /// ProjectIdeaId, no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe analysis"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    public record RiskItem(string Category, string Level, string Description, string Mitigation);

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

        await LoadPageDataAsync(
     userId,
     cancellationToken);

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/ProjectDNA",
            cancellationToken);

        return Page();
    }

    public async Task<IActionResult> OnPostAnalyzeAsync(
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

        await LoadPageDataAsync(
     userId,
     cancellationToken);

        if (Idea == null)
        {
            ErrorMessage = "No selected project idea was found. Please select an idea first.";
            return Page();
        }

        var profile = await db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.UserId == userId);

        var request = BuildProjectDnaRequest(profile);

        try
        {
            var response = await aiService.AnalyzeProjectDnaAsync(request);

            if (response?.Analysis == null)
            {
                ErrorMessage = "AI Project DNA analysis could not be generated. Make sure the Python AI service is running.";
                return Page();
            }

            Analysis = response.Analysis;
            LlmUsed = response.LlmUsed;
            Source = response.Source;

            ApplyAiAnalysis(
                response.Analysis);

            await PersistAnalysisAsync(
                userId,
                Idea.Id,
                response,
                cancellationToken);

            await PersistReviewAsync(
                userId,
                Idea.Id,
                response,
                cancellationToken);
        }
        catch
        {
            ErrorMessage = "AI Project DNA analysis could not be generated. Make sure the Python AI service is running.";
        }

        return Page();
    }
    private async Task PersistAnalysisAsync(
    int userId,
    int ideaId,
    ProjectDnaServiceResponse response,
    CancellationToken cancellationToken)
    {
        var record =
            new ProjectDnaAnalysisRecord
            {
                ProjectId = ProjectId,
                ProjectIdeaId = ideaId,
                GeneratedByUserId = userId,

                AnalysisJson =
                    JsonSerializer.Serialize(
                        response.Analysis,
                        DnaJsonOptions),

                LlmUsed = response.LlmUsed,
                Source = response.Source,
                Provider = response.Provider,
                ModelUsed = response.ModelUsed,
                GeneratedAtUtc = DateTime.UtcNow
            };

        db.ProjectDnaAnalyses.Add(
            record);

        await db.SaveChangesAsync(
            cancellationToken);
    }
    private async Task PersistReviewAsync(
    int userId,
    int ideaId,
    ProjectDnaServiceResponse response,
    CancellationToken cancellationToken)
    {
        var review = response.Review;

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
            ProjectIdeaId = ideaId,
            MentorChatSessionId = null,
            AgentName = "ProjectDNAAgent",
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

        await db.SaveChangesAsync(
    cancellationToken);

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.ProjectIdeaId == ideaId && r.UserId == userId && r.AgentName == "ProjectDNAAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync(
    cancellationToken);
    }

  
  private async Task LoadPageDataAsync(
    int userId,
    CancellationToken cancellationToken)
    {
        /*
         * The active project's official idea is the
         * source of truth for this page.
         */
        Idea = await db.Projects
            .AsNoTracking()
            .Where(project =>
                project.Id == ProjectId)
            .Select(project =>
                project.ProjectIdea)
            .FirstOrDefaultAsync(
                cancellationToken);

        StudentSkills = await db.StudentSkills
            .AsNoTracking()
            .Where(skill =>
                skill.UserId == userId)
            .OrderByDescending(skill =>
                skill.Rating)
            .ThenBy(skill =>
                skill.SkillName)
            .ToListAsync(
                cancellationToken);

        DnaDimensions.Clear();
        Risks.Clear();
        RequiredSkillsAnalysis.Clear();

        Analysis = null;
        LlmUsed = false;
        Source = null;

        /*
         * Reload the latest saved DNA analysis so it survives
         * refreshes, navigation and application restarts.
         */
        if (Idea != null)
        {
            var savedAnalysis =
                await db.ProjectDnaAnalyses
                    .AsNoTracking()
                    .Where(record =>
                        record.ProjectId ==
                            ProjectId &&
                        record.ProjectIdeaId ==
                            Idea.Id)
                    .OrderByDescending(record =>
                        record.GeneratedAtUtc)
                    .ThenByDescending(record =>
                        record.Id)
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (savedAnalysis != null)
            {
                try
                {
                    Analysis =
                        JsonSerializer.Deserialize<
                            ProjectDnaAnalysisDto>(
                                savedAnalysis.AnalysisJson,
                                DnaJsonOptions);

                    LlmUsed =
                        savedAnalysis.LlmUsed;

                    Source =
                        savedAnalysis.Source;

                    if (Analysis != null)
                    {
                        ApplyAiAnalysis(
                            Analysis);
                    }
                }
                catch (JsonException)
                {
                    Analysis = null;

                    ErrorMessage =
                        "The saved Project DNA report "
                        + "could not be read. Generate "
                        + "the report again.";
                }
            }
        }

        LatestReview = Idea == null
            ? null
            : await db.AiOutputReviews
                .AsNoTracking()
                .Where(review =>
                    review.ProjectIdeaId ==
                        Idea.Id &&
                    review.UserId ==
                        userId &&
                    review.AgentName ==
                        "ProjectDNAAgent")
                .OrderByDescending(review =>
                    review.CreatedAt)
                .FirstOrDefaultAsync(
                    cancellationToken);
    }

    private ProjectDnaRequest BuildProjectDnaRequest(StudentProfile? profile)
    {
        var studentSkillNames = StudentSkills
            .Select(s => s.SkillName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var skillRatings = StudentSkills
            .Where(s => !string.IsNullOrWhiteSpace(s.SkillName))
            .GroupBy(s => s.SkillName.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                g => g.Key,
                g => Math.Clamp(g.First().Rating, 1, 5),
                StringComparer.OrdinalIgnoreCase
            );

        return new ProjectDnaRequest(
            IdeaTitle: Idea?.Title ?? "",
            ProblemStatement: Idea?.ProblemStatement ?? "",
            TargetUsers: Idea?.TargetUsers ?? "",
            WhyUseful: Idea?.WhyUseful ?? "",
            LebaneseMarketRelevance: Idea?.LebaneseMarketRelevance ?? "",
            RequiredTechnologies: Idea?.RequiredTechnologies ?? "",
            RequiredSkills: Idea?.RequiredSkills ?? "",
            MissingSkills: Idea?.MissingSkills ?? "",
            DifficultyLevel: Idea?.DifficultyLevel ?? "intermediate",
            DatasetNeeded: Idea?.DatasetNeeded ?? "",
            FinalDeliverables: Idea?.FinalDeliverables ?? "",
            Domain: Idea?.Domain ?? "",
            LebaneseSector: Idea?.LebanesesSector ?? "",
            StudentMajor: profile?.Major ?? "Computer Science",
            ExperienceLevel: profile?.ExperienceLevel ?? "intermediate",
            AvailableHoursPerWeek: profile?.AvailableHoursPerWeek ?? 10,
            TeamSize: profile?.TeamMembers ?? 1,
            StudentSkills: studentSkillNames,
            SkillRatings: skillRatings
        );
    }

    private void ApplyAiAnalysis(ProjectDnaAnalysisDto analysis)
    {
        DnaDimensions =
        [
            ("Overall Score", analysis.OverallScore, "#28385E"),
            ("Technical Fit", analysis.TechnicalFitScore, "#304163"),
            ("Skill Match", analysis.SkillMatchScore, "#516C8D"),
            ("Innovation", analysis.InnovationScore, "#3F5A78"),
            ("Feasibility", analysis.FeasibilityScore, "#5E7693"),
            ("Market Relevance", analysis.MarketRelevanceScore, "#7489A3"),
            ("Data Readiness", analysis.DataReadinessScore, "#8A9AAF"),
            ("Scope Clarity", analysis.ScopeClarityScore, "#6B7F98"),
            ("Supervisor Fit", analysis.SupervisorFitScore, "#405577")
        ];

        Risks = (analysis.RiskProfile ?? [])
            .Select(r => new RiskItem(
                r.Title,
                r.Level,
                r.Explanation,
                r.Mitigation
            ))
            .ToList();

        if (!Risks.Any())
        {
            Risks.Add(new RiskItem(
                "Overall Risk",
                analysis.RiskLevel,
                "The AI agent did not identify major risks.",
                "Continue with a controlled MVP and review risks with your supervisor."
            ));
        }

        RequiredSkillsAnalysis = analysis.RequiredSkillsAnalysis ?? [];
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
                    + "Project DNA.";

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
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}