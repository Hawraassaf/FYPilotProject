using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/tasks")]
[Authorize]
public class TasksController(ApplicationDbContext db) : ControllerBase
{
    private static TaskResponse Map(ProjectTask t) => new(
        t.Id, t.Title, t.Description, t.Status, t.Priority,
        t.Deadline, t.ProjectId, t.CreatedAt.ToString("o")
    );

    [HttpGet("project/{projectId}")]
    public async Task<IActionResult> ListByProject(int projectId)
    {
        var tasks = await db.Tasks.Where(t => t.ProjectId == projectId)
            .OrderBy(t => t.CreatedAt).ToListAsync();
        return Ok(tasks.Select(Map));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateTaskRequest request)
    {
        var task = new ProjectTask
        {
            Title = request.Title,
            Description = request.Description ?? "",
            Status = request.Status ?? "todo",
            Priority = request.Priority ?? "medium",
            Deadline = request.Deadline,
            ProjectId = request.ProjectId,
        };
        db.Tasks.Add(task);
        await db.SaveChangesAsync();
        return StatusCode(201, Map(task));
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(int id, [FromBody] UpdateTaskRequest request)
    {
        var task = await db.Tasks.FindAsync(id);
        if (task == null) return NotFound(new { error = "Task not found" });

        if (request.Title != null) task.Title = request.Title;
        if (request.Description != null) task.Description = request.Description;
        if (request.Status != null) task.Status = request.Status;
        if (request.Priority != null) task.Priority = request.Priority;
        if (request.Deadline != null) task.Deadline = request.Deadline;

        await db.SaveChangesAsync();
        return Ok(Map(task));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id)
    {
        var task = await db.Tasks.FindAsync(id);
        if (task == null) return NotFound();
        db.Tasks.Remove(task);
        await db.SaveChangesAsync();
        return NoContent();
    }
}
