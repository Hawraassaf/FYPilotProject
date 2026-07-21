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
public class ProjectDNAModel(ApplicationDbContext db, IAiServiceClient aiService) : PageModel
{
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

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = UserId();
        await LoadPageDataAsync(userId, ideaId);
    }

    public async Task<IActionResult> OnPostAnalyzeAsync(int ideaId)
    {
        var userId = UserId();

        await LoadPageDataAsync(userId, ideaId);

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

            ApplyAiAnalysis(response.Analysis);
            await PersistReviewAsync(userId, ideaId, response);
        }
        catch
        {
            ErrorMessage = "AI Project DNA analysis could not be generated. Make sure the Python AI service is running.";
        }

        return Page();
    }

    private async Task PersistReviewAsync(int userId, int ideaId, ProjectDnaServiceResponse response)
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

        await db.SaveChangesAsync();

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.ProjectIdeaId == ideaId && r.UserId == userId && r.AgentName == "ProjectDNAAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();
    }

    private async Task LoadPageDataAsync(int userId, int? ideaId)
    {
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected);

        StudentSkills = await db.StudentSkills
            .AsNoTracking()
            .Where(s => s.UserId == userId)
            .OrderByDescending(s => s.Rating)
            .ThenBy(s => s.SkillName)
            .ToListAsync();

        DnaDimensions.Clear();
        Risks.Clear();
        RequiredSkillsAnalysis.Clear();
        Analysis = null;
        LlmUsed = false;
        Source = null;

        LatestReview = Idea == null
            ? null
            : await db.AiOutputReviews
                .AsNoTracking()
                .Where(r => r.ProjectIdeaId == Idea.Id && r.UserId == userId && r.AgentName == "ProjectDNAAgent")
                .OrderByDescending(r => r.CreatedAt)
                .FirstOrDefaultAsync();
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

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}
