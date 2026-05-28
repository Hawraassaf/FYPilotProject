using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

/// <summary>
/// Proxy controller that forwards analytics/ML requests to the Python DS service.
/// All endpoints require a valid JWT — it is forwarded to the Python service unchanged.
/// </summary>
[ApiController]
[Route("api/ds")]
[Authorize]
public class DataScienceController(DataScienceService ds) : ControllerBase
{
    private string Token => Request.Headers.Authorization.ToString().Replace("Bearer ", "");

    // ── Analytics ─────────────────────────────────────────────────────────────

    /// <summary>ML risk score, completion probability and risk factors for a project.</summary>
    [HttpGet("analytics/risk/{projectId}")]
    public async Task<IActionResult> Risk(int projectId)
    {
        var result = await ds.GetRiskAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Burndown chart data and sprint velocity.</summary>
    [HttpGet("analytics/burndown/{projectId}")]
    public async Task<IActionResult> Burndown(int projectId)
    {
        var result = await ds.GetBurndownAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Predicted final grade and contributing factors.</summary>
    [HttpGet("analytics/grade/{projectId}")]
    public async Task<IActionResult> GradePrediction(int projectId)
    {
        var result = await ds.GetGradePredictionAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Anomaly detection — identifies stalled, at-risk or unusual project patterns.</summary>
    [HttpGet("analytics/anomalies/{projectId}")]
    public async Task<IActionResult> Anomalies(int projectId)
    {
        var result = await ds.GetAnomaliesAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Full analytics dashboard: trends, task distribution, productivity score.</summary>
    [HttpGet("analytics/dashboard/{projectId}")]
    public async Task<IActionResult> Dashboard(int projectId)
    {
        var result = await ds.GetAnalyticsDashboardAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    // ── Intelligence ─────────────────────────────────────────────────────────

    /// <summary>Generate a detailed, phased project roadmap using GPT.</summary>
    [HttpPost("intelligence/roadmap")]
    public async Task<IActionResult> Roadmap([FromBody] RoadmapRequest request)
    {
        var payload = new
        {
            project_title = request.ProjectTitle,
            project_description = request.ProjectDescription,
            technologies = request.Technologies,
            start_date = request.StartDate,
            end_date = request.EndDate,
            experience_level = request.ExperienceLevel ?? "intermediate",
        };
        var result = await ds.GenerateRoadmapAsync(payload, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Check how similar a project is to others in the system (originality check).</summary>
    [HttpGet("intelligence/similarity/{projectId}")]
    public async Task<IActionResult> Similarity(int projectId)
    {
        var result = await ds.GetSimilarityAsync(projectId, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Find the best-matched supervisors for a project based on expertise and workload.</summary>
    [HttpPost("intelligence/supervisor-match")]
    public async Task<IActionResult> SupervisorMatch([FromBody] SupervisorMatchRequest request)
    {
        var payload = new
        {
            project_title = request.ProjectTitle,
            project_description = request.ProjectDescription,
            technologies = request.Technologies,
        };
        var result = await ds.MatchSupervisorsAsync(payload, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }

    /// <summary>Identify skill gaps between student knowledge and project requirements.</summary>
    [HttpPost("intelligence/skill-gap")]
    public async Task<IActionResult> SkillGap([FromBody] SkillGapRequest request)
    {
        var payload = new
        {
            current_skills = request.CurrentSkills,
            project_technologies = request.ProjectTechnologies,
            target_role = request.TargetRole ?? "software engineer",
        };
        var result = await ds.AnalyzeSkillGapAsync(payload, Token);
        return result.HasValue ? Ok(result) : StatusCode(503, new { error = "DS service unavailable" });
    }
}

// ── Request DTOs ──────────────────────────────────────────────────────────────

public record RoadmapRequest(
    string ProjectTitle,
    string ProjectDescription,
    string Technologies,
    string? StartDate,
    string? EndDate,
    string? ExperienceLevel
);

public record SupervisorMatchRequest(
    string ProjectTitle,
    string ProjectDescription,
    string Technologies
);

public record SkillGapRequest(
    string CurrentSkills,
    string ProjectTechnologies,
    string? TargetRole
);
