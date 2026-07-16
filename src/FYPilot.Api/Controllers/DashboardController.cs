using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/dashboard")]
[Authorize]
public class DashboardController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    private static ProjectResponse MapProject(Project p) => new(
        p.Id, p.Title, p.Description, p.Technologies, p.Status,
        p.StartDate, p.EndDate, p.ProgressPercentage, p.StudentId,
        p.SupervisorId, p.CreatedAt.ToString("o"),
        p.Student?.FullName ?? "Unknown"
    );

    [HttpGet("student")]
    public async Task<IActionResult> StudentDashboard()
    {
        var projects = await db.Projects.Where(p => p.StudentId == UserId).ToListAsync();
        var projectIds = projects.Select(p => p.Id).ToList();

        var allTasks = await db.Tasks.Where(t => projectIds.Contains(t.ProjectId)).ToListAsync();
        var allMilestones = await db.Milestones.Where(m => projectIds.Contains(m.ProjectId)).ToListAsync();

        var pendingTasks = allTasks.Count(t => t.Status != "done");
        var completedTasks = allTasks.Count(t => t.Status == "done");
        var upcomingMilestones = allMilestones
            .Where(m => m.CompletionPercentage < 100)
            .Take(5)
            .Select(m => new MilestoneResponse(m.Id, m.Title, m.Description,
                m.DueDate, m.CompletionPercentage, m.ProjectId, m.CreatedAt.ToString("o")));

        var active = projects.Where(p => p.Status == "in_progress").ToList();
        var overallProgress = projects.Count > 0
            ? (int)projects.Average(p => p.ProgressPercentage) : 0;

        var riskWarnings = new List<string>();
        foreach (var p in active)
        {
            if (p.ProgressPercentage < 20)
                riskWarnings.Add($"\"{p.Title}\" has low progress — consider reviewing your timeline.");
            var todoCount = allTasks.Count(t => t.ProjectId == p.Id && t.Status == "todo");
            if (todoCount > 5)
                riskWarnings.Add($"\"{p.Title}\" has {todoCount} incomplete tasks.");
        }

        return Ok(new StudentDashboardResponse(
            projects.Count,
            active.Count,
            projects.Count(p => p.Status == "completed"),
            pendingTasks,
            completedTasks,
            overallProgress,
            upcomingMilestones,
            riskWarnings.Take(3),
            [
                "Consider breaking down large tasks into smaller, manageable subtasks.",
                "Regular commits and documentation will strengthen your final report.",
                "Schedule weekly check-ins with your supervisor to stay on track.",
            ]
        ));
    }

    [HttpGet("supervisor")]
    [Authorize(Roles = "supervisor")]
    public async Task<IActionResult> SupervisorDashboard()
    {
        var projects = await db.Projects.Include(p => p.Student)
            .OrderByDescending(p => p.CreatedAt).ToListAsync();

        var studentIds = projects.Select(p => p.StudentId).Distinct().ToList();
        var progressBreakdown = new[]
        {
            new ProgressBucket("0-25%", projects.Count(p => p.ProgressPercentage <= 25)),
            new ProgressBucket("26-50%", projects.Count(p => p.ProgressPercentage is > 25 and <= 50)),
            new ProgressBucket("51-75%", projects.Count(p => p.ProgressPercentage is > 50 and <= 75)),
            new ProgressBucket("76-100%", projects.Count(p => p.ProgressPercentage > 75)),
        };

        return Ok(new SupervisorDashboardResponse(
            studentIds.Count,
            projects.Count(p => p.Status == "in_progress"),
            projects.Count(p => p.Status == "planning"),
            projects.Take(5).Select(MapProject),
            progressBreakdown
        ));
    }

    [HttpGet("company")]
    public async Task<IActionResult> CompanyDashboard()
    {
        var challenges = await db.Challenges
            .Include(c => c.Company)
            .Where(c => c.CompanyId == UserId)
            .OrderByDescending(c => c.CreatedAt)
            .ToListAsync();

        var profile = await db.CompanyProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);

        ChallengeResponse MapChallenge(Challenge c) => new(
            c.Id, c.Title, c.Description, c.RequiredSkills, c.DifficultyLevel,
            c.CompanyId, profile?.CompanyName ?? "Company",
            profile?.Industry ?? "Technology", c.CreatedAt.ToString("o")
        );

        var byDifficulty = new[] { "beginner", "intermediate", "advanced" }
            .Select(d => new DifficultyCounts(d, challenges.Count(c => c.DifficultyLevel == d)));

        return Ok(new CompanyDashboardResponse(
            challenges.Count,
            challenges.Count,
            challenges.Take(5).Select(MapChallenge),
            byDifficulty
        ));
    }

    [HttpGet("activity")]
    public async Task<IActionResult> Activity()
    {
        var rows = await db.Activities
            .Include(a => a.User)
            .Where(a => a.UserId == UserId)
            .OrderByDescending(a => a.CreatedAt)
            .Take(20)
            .ToListAsync();

        return Ok(rows.Select(r => new ActivityResponse(
            r.Id, r.Type, r.Message, r.CreatedAt.ToString("o"),
            r.UserId, r.User?.FullName ?? "User"
        )));
    }
}
