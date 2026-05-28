using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/roadmap")]
[Authorize]
public class RoadmapController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    [HttpGet("{ideaId}")]
    public async Task<IActionResult> Get(int ideaId)
    {
        var roadmap = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .FirstOrDefaultAsync(r => r.IdeaId == ideaId && r.UserId == UserId);
        if (roadmap == null) return Ok(null);
        return Ok(MapRoadmap(roadmap));
    }

    [HttpPost("{ideaId}/generate")]
    public async Task<IActionResult> Generate(int ideaId)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == UserId);
        if (idea == null) return NotFound();

        var existing = await db.ProjectRoadmaps.Include(r => r.Phases)
            .FirstOrDefaultAsync(r => r.IdeaId == ideaId && r.UserId == UserId);
        if (existing != null)
        {
            db.RoadmapPhases.RemoveRange(existing.Phases);
            db.ProjectRoadmaps.Remove(existing);
        }

        var roadmap = new ProjectRoadmap { IdeaId = ideaId, UserId = UserId };
        db.ProjectRoadmaps.Add(roadmap);
        await db.SaveChangesAsync();

        var phases = RoadmapGenerator.Generate(idea);
        foreach (var phase in phases)
        {
            phase.RoadmapId = roadmap.Id;
            db.RoadmapPhases.Add(phase);
        }
        await db.SaveChangesAsync();

        var full = await db.ProjectRoadmaps.Include(r => r.Phases)
            .FirstAsync(r => r.Id == roadmap.Id);
        return Ok(MapRoadmap(full));
    }

    [HttpPatch("phases/{phaseId}/complete")]
    public async Task<IActionResult> MarkComplete(int phaseId)
    {
        var phase = await db.RoadmapPhases.Include(p => p.Roadmap)
            .FirstOrDefaultAsync(p => p.Id == phaseId && p.Roadmap!.UserId == UserId);
        if (phase == null) return NotFound();
        phase.IsCompleted = !phase.IsCompleted;
        await db.SaveChangesAsync();
        return Ok(new { phase.Id, phase.IsCompleted });
    }

    private static RoadmapResponse MapRoadmap(ProjectRoadmap r) => new(
        r.Id, r.IdeaId,
        r.Phases.OrderBy(p => p.PhaseNumber).Select(p => new RoadmapPhaseResponse(
            p.Id, p.PhaseNumber, p.Name, p.Objective,
            JsonSerializer.Deserialize<List<string>>(p.TasksJson) ?? [],
            p.ExpectedOutput, p.ToolsNeeded, p.EstimatedWeeks,
            p.Dependencies, p.Risks, p.SuccessCriteria, p.IsCompleted
        )).ToList(),
        r.CreatedAt
    );
}
