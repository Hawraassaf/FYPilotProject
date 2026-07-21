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
public class RoadmapModel(ApplicationDbContext db, IAiServiceClient aiService) : PageModel
{
    public ProjectIdea? Idea { get; private set; }
    public List<RoadmapPhase> Phases { get; private set; } = [];

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

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = UserId();
        await LoadPageDataAsync(userId, ideaId);
    }

    public async Task<IActionResult> OnPostGenerateAsync(int ideaId)
    {
        var userId = UserId();

        await LoadPageDataAsync(userId, ideaId);

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

        var response = await aiService.GenerateProjectRoadmapAsync(request);

        if (response?.Roadmap == null)
        {
            ErrorMessage = "AI roadmap could not be generated. Make sure the Python AI service is running.";
            return Page();
        }

        LlmUsed = response.LlmUsed;
        Source = response.Source;

        var existingRoadmaps = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .Where(r => r.IdeaId == ideaId && r.UserId == userId)
            .ToListAsync();

        db.ProjectRoadmaps.RemoveRange(existingRoadmaps);

        var phases = ConvertAiRoadmapToPhases(
            response.Roadmap,
            Idea.RequiredTechnologies
        );

        var roadmap = new ProjectRoadmap
        {
            IdeaId = ideaId,
            UserId = userId,
            Phases = phases
        };

        db.ProjectRoadmaps.Add(roadmap);

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
                AgentName = "ProjectRoadmapAgent",
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

        await db.SaveChangesAsync();

        TempData["Success"] = $"AI roadmap with {phases.Count} phases generated.";
        return RedirectToPage(new { ideaId });
    }

    public async Task<IActionResult> OnPostCompleteAsync(
       int phaseId,
       int ideaId)
    {
        var userId = UserId();

        var updatedRows = await db.RoadmapPhases
            .Where(p =>
                p.Id == phaseId &&
                p.Roadmap != null &&
                p.Roadmap.UserId == userId &&
                p.Roadmap.IdeaId == ideaId)
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
                ideaId
            });
        }

        TempData["Success"] =
            "Phase marked as completed.";

        return RedirectToPage(new
        {
            ideaId
        });
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

        Phases = roadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .ToList() ?? [];
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

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}