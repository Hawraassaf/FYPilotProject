using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/projects")]
[Authorize(Roles = "student,supervisor,admin")]
public class ProjectsController(
    ApplicationDbContext db) : ControllerBase
{
    private int UserId
    {
        get
        {
            var value = User.FindFirst("userId")?.Value;

            return int.TryParse(value, out var userId)
                ? userId
                : 0;
        }
    }

    private IQueryable<Project> AccessibleProjects()
    {
        var query = db.Projects.AsQueryable();

        if (User.IsInRole("admin"))
        {
            return query;
        }

        if (User.IsInRole("supervisor"))
        {
            return query.Where(project =>
                project.SupervisorId == UserId);
        }

        return query.Where(project =>
            project.StudentId == UserId);
    }

    private static ProjectResponse MapProject(Project project)
    {
        return new ProjectResponse(
            project.Id,
            project.Title,
            project.Description,
            project.Technologies,
            project.Status,
            project.StartDate,
            project.EndDate,
            project.ProgressPercentage,
            project.StudentId,
            project.SupervisorId,
            project.CreatedAt.ToString("o"),
            project.Student?.FullName ?? "Unknown");
    }

    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] string? status,
        [FromQuery] int? studentId)
    {
        IQueryable<Project> query = AccessibleProjects();

        // This filter cannot escape the user's permitted projects
        // because AccessibleProjects was applied first.
        if (studentId.HasValue)
        {
            query = query.Where(project =>
                project.StudentId == studentId.Value);
        }

        if (!string.IsNullOrWhiteSpace(status))
        {
            var normalizedStatus =
                status.Trim().ToLowerInvariant();

            query = query.Where(project =>
                project.Status.ToLower() ==
                normalizedStatus);
        }

        var projects = await query
    .Include(project => project.Student)
    .AsNoTracking()
    .OrderByDescending(project =>
        project.CreatedAt)
    .ToListAsync();
        return Ok(projects.Select(MapProject));
    }

    [HttpGet("{id:int}")]
    public async Task<IActionResult> Get(int id)
    {
        var project = await AccessibleProjects()
            .AsNoTracking()
            .Include(project => project.Student)
            .FirstOrDefaultAsync(project =>
                project.Id == id);

        if (project == null)
        {
            return NotFound(new
            {
                error = "Project not found."
            });
        }

        return Ok(MapProject(project));
    }

    [HttpPost]
    [Authorize(Roles = "student")]
    public async Task<IActionResult> Create(
        [FromBody] CreateProjectRequest request)
    {
        var project = new Project
        {
            Title = request.Title.Trim(),
            Description =
                request.Description?.Trim() ?? "",
            Technologies =
                request.Technologies?.Trim() ?? "",
            StartDate = request.StartDate,
            EndDate = request.EndDate,
            StudentId = UserId,
            Status = "planning"
        };

        // A student cannot manually assign an arbitrary
        // supervisor through the request.
        if (request.SupervisorId.HasValue)
        {
            var hasActiveAssignment =
                await db.SupervisorAssignments.AnyAsync(
                    assignment =>
                        assignment.StudentId == UserId &&
                        assignment.SupervisorId ==
                        request.SupervisorId.Value &&
                        assignment.Status == "active");

            if (!hasActiveAssignment)
            {
                return BadRequest(new
                {
                    error =
                        "The selected supervisor is not actively assigned to this student."
                });
            }

            project.SupervisorId =
                request.SupervisorId.Value;
        }

        db.Projects.Add(project);
        await db.SaveChangesAsync();

        await db.Entry(project)
            .Reference(item => item.Student)
            .LoadAsync();

        return StatusCode(
            StatusCodes.Status201Created,
            MapProject(project));
    }

    [HttpPut("{id:int}")]
    public async Task<IActionResult> Update(
        int id,
        [FromBody] UpdateProjectRequest request)
    {
        var project = await AccessibleProjects()
            .Include(item => item.Student)
            .FirstOrDefaultAsync(item =>
                item.Id == id);

        if (project == null)
        {
            return NotFound(new
            {
                error = "Project not found."
            });
        }

        if (request.Title != null)
        {
            project.Title = request.Title.Trim();
        }

        if (request.Description != null)
        {
            project.Description =
                request.Description.Trim();
        }

        if (request.Technologies != null)
        {
            project.Technologies =
                request.Technologies.Trim();
        }

        if (request.Status != null)
        {
            project.Status =
                request.Status.Trim()
                    .ToLowerInvariant();
        }

        if (request.StartDate != null)
        {
            project.StartDate = request.StartDate;
        }

        if (request.EndDate != null)
        {
            project.EndDate = request.EndDate;
        }

        if (request.ProgressPercentage.HasValue)
        {
            if (request.ProgressPercentage.Value
                is < 0 or > 100)
            {
                return BadRequest(new
                {
                    error =
                        "Progress percentage must be between 0 and 100."
                });
            }

            project.ProgressPercentage =
                request.ProgressPercentage.Value;
        }

        // Only an administrator can directly reassign
        // a legacy project through this endpoint.
        if (request.SupervisorId.HasValue &&
            User.IsInRole("admin"))
        {
            project.SupervisorId =
                request.SupervisorId.Value;
        }

        project.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        return Ok(MapProject(project));
    }

    [HttpDelete("{id:int}")]
    [Authorize(Roles = "student,admin")]
    public async Task<IActionResult> Delete(int id)
    {
        var project = await AccessibleProjects()
            .FirstOrDefaultAsync(item =>
                item.Id == id);

        if (project == null)
        {
            return NotFound(new
            {
                error = "Project not found."
            });
        }

        db.Projects.Remove(project);
        await db.SaveChangesAsync();

        return NoContent();
    }

    [HttpGet("{id:int}/roadmap")]
    public async Task<IActionResult> Roadmap(int id)
    {
        var projectExists =
            await AccessibleProjects().AnyAsync(
                project => project.Id == id);

        if (!projectExists)
        {
            return NotFound(new
            {
                error = "Project not found."
            });
        }

        var milestones = await db.Milestones
            .AsNoTracking()
            .Where(milestone =>
                milestone.ProjectId == id)
            .OrderBy(milestone =>
                milestone.DueDate)
            .ToListAsync();

        var tasks = await db.Tasks
            .AsNoTracking()
            .Where(task =>
                task.ProjectId == id)
            .ToListAsync();

        return Ok(new
        {
            milestones,
            tasks
        });
    }

    [HttpGet("{id:int}/risks")]
    public async Task<IActionResult> Risks(int id)
    {
        var project = await AccessibleProjects()
            .AsNoTracking()
            .FirstOrDefaultAsync(item =>
                item.Id == id);

        if (project == null)
        {
            return NotFound(new
            {
                error = "Project not found."
            });
        }

        var tasks = await db.Tasks
            .AsNoTracking()
            .Where(task =>
                task.ProjectId == id)
            .ToListAsync();

        var risks = new List<object>();

        var todoCount = tasks.Count(task =>
            task.Status == "todo");

        if (project.ProgressPercentage < 20)
        {
            risks.Add(new
            {
                level = "high",
                message =
                    "Low overall progress. Consider reviewing your timeline."
            });
        }

        if (todoCount > 5)
        {
            risks.Add(new
            {
                level = "medium",
                message =
                    $"{todoCount} tasks have not been started."
            });
        }

        if (tasks.Count == 0)
        {
            risks.Add(new
            {
                level = "low",
                message =
                    "No tasks are defined for this project."
            });
        }

        return Ok(risks);
    }
}