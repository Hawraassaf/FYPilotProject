using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/milestones")]
[Authorize]
public class MilestonesController(ApplicationDbContext db) : ControllerBase
{
    private static MilestoneResponse Map(Milestone m) => new(
        m.Id, m.Title, m.Description, m.DueDate,
        m.CompletionPercentage, m.ProjectId, m.CreatedAt.ToString("o")
    );

    [HttpGet("project/{projectId}")]
    public async Task<IActionResult> ListByProject(int projectId)
    {
        var milestones = await db.Milestones.Where(m => m.ProjectId == projectId)
            .OrderBy(m => m.CreatedAt).ToListAsync();
        return Ok(milestones.Select(Map));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateMilestoneRequest request)
    {
        var milestone = new Milestone
        {
            Title = request.Title,
            Description = request.Description ?? "",
            DueDate = request.DueDate,
            CompletionPercentage = request.CompletionPercentage ?? 0,
            ProjectId = request.ProjectId,
        };
        db.Milestones.Add(milestone);
        await db.SaveChangesAsync();
        return StatusCode(201, Map(milestone));
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(int id, [FromBody] UpdateMilestoneRequest request)
    {
        var milestone = await db.Milestones.FindAsync(id);
        if (milestone == null) return NotFound(new { error = "Milestone not found" });

        if (request.Title != null) milestone.Title = request.Title;
        if (request.Description != null) milestone.Description = request.Description;
        if (request.DueDate != null) milestone.DueDate = request.DueDate;
        if (request.CompletionPercentage.HasValue) milestone.CompletionPercentage = request.CompletionPercentage.Value;

        await db.SaveChangesAsync();
        return Ok(Map(milestone));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id)
    {
        var milestone = await db.Milestones.FindAsync(id);
        if (milestone == null) return NotFound();
        db.Milestones.Remove(milestone);
        await db.SaveChangesAsync();
        return NoContent();
    }
}
