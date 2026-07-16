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