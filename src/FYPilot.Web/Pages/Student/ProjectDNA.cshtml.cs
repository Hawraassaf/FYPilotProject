using System.Security.Claims;
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

    public record RiskItem(string Category, string Level, string Description, string Mitigation);

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
        await LoadPageDataAsync(userId, ideaId);
    }

    public async Task<IActionResult> OnPostAnalyzeAsync(int ideaId)
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        await LoadPageDataAsync(userId, ideaId);

        if (Idea == null)
        {
            return Page();
        }

        var profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        var request = BuildProjectDnaRequest(profile);

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

        return Page();
    }

    private async Task LoadPageDataAsync(int userId, int? ideaId)
    {
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas
                  .Where(i => i.UserId == userId)
                  .OrderByDescending(i => i.CreatedAt)
                  .FirstOrDefaultAsync();

        if (Idea == null)
        {
            return;
        }

        StudentSkills = await db.StudentSkills
            .Where(s => s.UserId == userId)
            .ToListAsync();

        DnaDimensions.Clear();
        Risks.Clear();
        RequiredSkillsAnalysis.Clear();

        BuildFallbackViewData();
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
            DifficultyLevel: Idea?.DifficultyLevel ?? "3",
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
            ("Overall Score", analysis.OverallScore, "#111827"),
            ("Technical Fit", analysis.TechnicalFitScore, "#3b82f6"),
            ("Skill Match", analysis.SkillMatchScore, "#10b981"),
            ("Innovation", analysis.InnovationScore, "#06b6d4"),
            ("Feasibility", analysis.FeasibilityScore, "#f59e0b"),
            ("Market Relevance", analysis.MarketRelevanceScore, "#8b5cf6"),
            ("Data Readiness", analysis.DataReadinessScore, "#ef4444"),
            ("Scope Clarity", analysis.ScopeClarityScore, "#6366f1"),
            ("Supervisor Fit", analysis.SupervisorFitScore, "#14b8a6"),
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

    private void BuildFallbackViewData()
    {
        if (Idea == null)
        {
            return;
        }

        DnaDimensions =
        [
            ("Innovation Score", Idea.InnovationScore, "#3b82f6"),
            ("Feasibility Score", Idea.FeasibilityScore, "#10b981"),
            ("Market Demand", Idea.MarketDemandScore, "#f59e0b"),
            ("Academic Value", 75, "#8b5cf6"),
            ("AI Complexity", Idea.Domain.Contains("AI", StringComparison.OrdinalIgnoreCase) ? 80 : 30, "#06b6d4"),
            ("Data Dependency", RequiresDataset(Idea.DatasetNeeded) ? 70 : 20, "#ef4444"),
            ("Deployment Complexity", Idea.DifficultyLevel == "advanced" ? 80 : 40, "#6366f1"),
            ("Team Fit Score", Math.Min(StudentSkills.Count * 8, 100), "#14b8a6"),
        ];

        var required = Idea.RequiredSkills
            .Split(',')
            .Select(s => s.Trim())
            .Where(s => s.Length > 0)
            .ToList();

        var missing = required
            .Where(rs => !StudentSkills.Any(ss =>
                ss.SkillName.Contains(rs, StringComparison.OrdinalIgnoreCase) ||
                rs.Contains(ss.SkillName, StringComparison.OrdinalIgnoreCase)))
            .ToList();

        if (missing.Count > 2)
        {
            Risks.Add(new RiskItem(
                "Skill Gap",
                "High",
                $"Missing {missing.Count} skills: {string.Join(", ", missing.Take(3))}",
                "Take online courses or reduce scope"
            ));
        }
        else if (missing.Count > 0)
        {
            Risks.Add(new RiskItem(
                "Skill Gap",
                "Medium",
                $"Missing: {string.Join(", ", missing)}",
                "Allocate 2–3 weeks for skill acquisition"
            ));
        }

        if (Idea.DifficultyLevel == "advanced" && StudentSkills.Count < 5)
        {
            Risks.Add(new RiskItem(
                "Complexity Risk",
                "High",
                "Advanced project with limited skill breadth",
                "Consider intermediate complexity first"
            ));
        }

        if (RequiresDataset(Idea.DatasetNeeded))
        {
            Risks.Add(new RiskItem(
                "Data Risk",
                "Medium",
                $"Dataset required: {Idea.DatasetNeeded}",
                "Identify and validate data source in week 1"
            ));
        }

        if (!Risks.Any())
        {
            Risks.Add(new RiskItem(
                "Overall Risk",
                "Low",
                "Project appears well-suited to your profile",
                "Proceed with confidence"
            ));
        }
    }

    private static bool RequiresDataset(string? datasetNeeded)
    {
        if (string.IsNullOrWhiteSpace(datasetNeeded))
        {
            return false;
        }

        var text = datasetNeeded.ToLowerInvariant();

        if (text.Contains("no for mvp") ||
            text == "no" ||
            text.Contains("not required") ||
            text.Contains("optional"))
        {
            return false;
        }

        return text.Contains("yes") ||
               text.Contains("dataset") ||
               text.Contains("data") ||
               text.Contains("records") ||
               text.Contains("logs");
    }
}