using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/projects")]
[Authorize]
public class ProjectsController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);
    private string UserRole => User.FindFirst("userRole")!.Value;

    private ProjectResponse MapProject(Project p) => new(
        p.Id, p.Title, p.Description, p.Technologies, p.Status,
        p.StartDate, p.EndDate, p.ProgressPercentage, p.StudentId,
        p.SupervisorId, p.CreatedAt.ToString("o"),
        p.Student?.FullName ?? "Unknown"
    );

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? status, [FromQuery] int? studentId)
    {
        var query = db.Projects.Include(p => p.Student).AsQueryable();

        if (UserRole == "student")
            query = query.Where(p => p.StudentId == UserId);

        if (studentId.HasValue)
            query = query.Where(p => p.StudentId == studentId.Value);

        if (!string.IsNullOrEmpty(status))
            query = query.Where(p => p.Status == status);

        var projects = await query.OrderByDescending(p => p.CreatedAt).ToListAsync();
        return Ok(projects.Select(MapProject));
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> Get(int id)
    {
        var project = await db.Projects.Include(p => p.Student).FirstOrDefaultAsync(p => p.Id == id);
        if (project == null) return NotFound(new { error = "Project not found" });
        return Ok(MapProject(project));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateProjectRequest request)
    {
        var project = new Project
        {
            Title = request.Title,
            Description = request.Description,
            Technologies = request.Technologies,
            StartDate = request.StartDate,
            EndDate = request.EndDate,
            SupervisorId = request.SupervisorId,
            StudentId = UserId,
            Status = "planning",
        };
        db.Projects.Add(project);
        await db.SaveChangesAsync();

        await db.Entry(project).Reference(p => p.Student).LoadAsync();
        return StatusCode(201, MapProject(project));
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(int id, [FromBody] UpdateProjectRequest request)
    {
        var project = await db.Projects.Include(p => p.Student).FirstOrDefaultAsync(p => p.Id == id);
        if (project == null) return NotFound(new { error = "Project not found" });

        if (request.Title != null) project.Title = request.Title;
        if (request.Description != null) project.Description = request.Description;
        if (request.Technologies != null) project.Technologies = request.Technologies;
        if (request.Status != null) project.Status = request.Status;
        if (request.StartDate != null) project.StartDate = request.StartDate;
        if (request.EndDate != null) project.EndDate = request.EndDate;
        if (request.ProgressPercentage.HasValue) project.ProgressPercentage = request.ProgressPercentage.Value;
        if (request.SupervisorId.HasValue) project.SupervisorId = request.SupervisorId.Value;
        project.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();
        return Ok(MapProject(project));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id)
    {
        var project = await db.Projects.FindAsync(id);
        if (project == null) return NotFound();
        db.Projects.Remove(project);
        await db.SaveChangesAsync();
        return NoContent();
    }

    [HttpGet("{id}/roadmap")]
    public async Task<IActionResult> Roadmap(int id)
    {
        var milestones = await db.Milestones.Where(m => m.ProjectId == id)
            .OrderBy(m => m.DueDate).ToListAsync();
        var tasks = await db.Tasks.Where(t => t.ProjectId == id).ToListAsync();
        return Ok(new { milestones, tasks });
    }

    [HttpGet("{id}/risks")]
    public async Task<IActionResult> Risks(int id)
    {
        var project = await db.Projects.FindAsync(id);
        if (project == null) return NotFound();

        var risks = new List<object>();
        var tasks = await db.Tasks.Where(t => t.ProjectId == id).ToListAsync();
        var todoCount = tasks.Count(t => t.Status == "todo");

        if (project.ProgressPercentage < 20)
            risks.Add(new { level = "high", message = "Low overall progress. Consider reviewing your timeline." });
        if (todoCount > 5)
            risks.Add(new { level = "medium", message = $"{todoCount} tasks not yet started. Risk of falling behind." });
        if (tasks.Count == 0)
            risks.Add(new { level = "low", message = "No tasks defined yet. Break your project into tasks." });

        return Ok(risks);
    }
}
