using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/ai")]
[Authorize]
public class AiController(ApplicationDbContext db, DataScienceService ds) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);
    private string Token => Request.Headers.Authorization.ToString().Replace("Bearer ", "");

    /// <summary>Rule-based AI chat (works without DS service).</summary>
    [HttpPost("chat")]
    public async Task<IActionResult> Chat([FromBody] ChatRequest request)
    {
        var projects = await db.Projects.Where(p => p.StudentId == UserId).ToListAsync();
        var projectContext = string.Join(", ", projects.Select(p => p.Title));
        var response = GenerateResponse(request.Message, projectContext, projects.Count);
        return Ok(new { message = response, timestamp = DateTime.UtcNow.ToString("o") });
    }

    /// <summary>Smart suggestions enriched with DS anomaly data when available.</summary>
    [HttpGet("suggestions")]
    public async Task<IActionResult> Suggestions()
    {
        var projects = await db.Projects.Where(p => p.StudentId == UserId).ToListAsync();
        var suggestions = new List<string>
        {
            "Break your project into weekly sprints with clear deliverables.",
            "Document your code as you write it — don't leave it for the end.",
            "Set up automated tests early to catch regressions.",
            "Keep a research log to track papers and references.",
            "Schedule regular demos with your supervisor.",
        };

        // Enrich with live anomaly data from DS service
        foreach (var project in projects.Take(2))
        {
            var anomaly = await ds.GetAnomaliesAsync(project.Id, Token);
            if (anomaly.HasValue)
            {
                var elem = anomaly.Value;
                if (elem.TryGetProperty("anomalies", out var anomaliesArr) && anomaliesArr.GetArrayLength() > 0)
                {
                    var first = anomaliesArr[0];
                    if (first.TryGetProperty("suggested_action", out var action))
                        suggestions.Insert(0, $"[{project.Title}] {action.GetString()}");
                }
            }
        }

        if (projects.Any(p => p.ProgressPercentage < 30))
            suggestions.Insert(0, "Some projects have low progress. Focus on completing high-priority tasks first.");

        return Ok(suggestions.Take(5));
    }

    /// <summary>Analyse project health — delegates to DS risk engine.</summary>
    [HttpPost("analyze")]
    public async Task<IActionResult> Analyze([FromBody] AnalyzeRequest request)
    {
        var risk = await ds.GetRiskAsync(request.ProjectId, Token);
        if (risk.HasValue) return Ok(risk);

        // Fallback when DS service is offline
        var project = await db.Projects.FindAsync(request.ProjectId);
        if (project == null) return NotFound(new { error = "Project not found" });
        return Ok(new
        {
            project_id = request.ProjectId,
            risk_level = project.ProgressPercentage < 30 ? "High" : "Medium",
            message = "DS service offline — using basic analysis.",
            recommendations = new[] { "Ensure DS service is running for full ML analysis." },
        });
    }

    /// <summary>Suggest FYP ideas based on current skills and interests.</summary>
    [HttpPost("suggest-ideas")]
    public async Task<IActionResult> SuggestIdeas([FromBody] SuggestIdeasRequest request)
    {
        // Use DS skill-gap endpoint as a proxy for idea suggestions
        var gap = await ds.AnalyzeSkillGapAsync(new
        {
            current_skills = request.Skills,
            project_technologies = request.Technologies ?? request.Skills,
            target_role = "software engineer",
        }, Token);
        if (gap.HasValue) return Ok(gap);

        return Ok(new
        {
            ideas = new[]
            {
                "AI-powered study planner using machine learning",
                "Distributed systems performance monitor",
                "Natural language code review assistant",
                "Peer learning platform with adaptive quizzes",
                "Smart campus resource allocation system",
            }
        });
    }

    /// <summary>Recommend skills for a project — delegates to DS skill gap analyser.</summary>
    [HttpPost("recommend-skills")]
    public async Task<IActionResult> RecommendSkills([FromBody] RecommendSkillsRequest request)
    {
        var result = await ds.AnalyzeSkillGapAsync(new
        {
            current_skills = request.CurrentSkills,
            project_technologies = request.Technologies,
            target_role = request.TargetRole ?? "software engineer",
        }, Token);
        if (result.HasValue) return Ok(result);

        return Ok(new
        {
            recommended_skills = new[] { "Git", "Docker", "REST APIs", "Testing", "CI/CD" },
            message = "DS service offline — showing default recommendations.",
        });
    }

    /// <summary>Get ML risk scores for a project.</summary>
    [HttpGet("risks/{projectId}")]
    public async Task<IActionResult> Risks(int projectId)
    {
        var result = await ds.GetRiskAsync(projectId, Token);
        if (result.HasValue) return Ok(result);

        // Fallback
        var project = await db.Projects.FindAsync(projectId);
        if (project == null) return NotFound(new { error = "Project not found" });
        var tasks = await db.Tasks.Where(t => t.ProjectId == projectId).ToListAsync();
        var risks = new List<object>();
        if (project.ProgressPercentage < 20)
            risks.Add(new { level = "high", message = "Low overall progress." });
        if (tasks.Count(t => t.Status == "todo") > 5)
            risks.Add(new { level = "medium", message = "Many tasks not started." });
        if (!risks.Any())
            risks.Add(new { level = "low", message = "Project is on track." });
        return Ok(risks);
    }

    private static string GenerateResponse(string message, string projectContext, int projectCount)
    {
        var lowerMsg = message.ToLower();
        if (lowerMsg.Contains("task") || lowerMsg.Contains("todo"))
            return "Break tasks into small, focused units. Aim to complete at least 2-3 tasks per day.";
        if (lowerMsg.Contains("milestone"))
            return "Milestones help track major progress points. Make sure each has a clear deliverable and deadline.";
        if (lowerMsg.Contains("deadline") || lowerMsg.Contains("time"))
            return "Time management is key for FYP success. Use the Kanban board to visualise what is in progress.";
        if (lowerMsg.Contains("supervisor"))
            return "Regular check-ins with your supervisor build trust. Prepare a short progress update before each meeting.";
        if (lowerMsg.Contains("risk"))
            return "Use the DS Analytics panel to get ML-powered risk predictions for your project.";
        if (lowerMsg.Contains("grade"))
            return "Your predicted grade is based on task completion, milestone health, and supervisor feedback. Check Analytics.";
        if (projectCount == 0)
            return "You haven't created a project yet. Start by defining your project title and description.";
        return $"You have {projectCount} project(s): {projectContext}. I can help with planning, risks, or any FYP question.";
    }
}

public record AnalyzeRequest(int ProjectId);
public record SuggestIdeasRequest(string Skills, string? Technologies);
public record RecommendSkillsRequest(string CurrentSkills, string Technologies, string? TargetRole);
