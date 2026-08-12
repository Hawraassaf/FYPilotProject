using System.Security.Claims;
using System.Text.Json;
using FYPilot.Api.Controllers;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Tests.Projects;

/// <summary>
/// TEST D: the supervisor "pending review" queue must follow each
/// project's authoritative ProjectIdeaId, not the legacy
/// ProjectIdea.IsSelected flag, which can be stale for ideas that were
/// already replaced.
/// </summary>
public sealed class SupervisorEvalControllerTests
{
    [Fact]
    public async Task GetPending_IgnoresStaleIsSelected_UsesCurrentProjectIdeaId()
    {
        var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();

        var options = new DbContextOptionsBuilder<ApplicationDbContext>()
            .UseSqlite(connection)
            .Options;

        await using var db = new ApplicationDbContext(options);
        await db.Database.EnsureCreatedAsync();

        var student = new User
        {
            FullName = "Student",
            Email = "student@test.local",
            PasswordHash = "test-hash",
            Role = "student",
            CreatedAt = DateTime.UtcNow
        };

        var supervisor = new User
        {
            FullName = "Supervisor",
            Email = "supervisor@test.local",
            PasswordHash = "test-hash",
            Role = "supervisor",
            CreatedAt = DateTime.UtcNow
        };

        db.Users.AddRange(student, supervisor);
        await db.SaveChangesAsync();

        var project = new Project
        {
            Title = "Project A",
            Description = "",
            Technologies = "",
            Status = "planning",
            StudentId = student.Id,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };

        db.Projects.Add(project);
        await db.SaveChangesAsync();

        // Old idea: still flagged IsSelected=true (stale), but no longer
        // the project's official idea.
        var oldIdea = new ProjectIdea
        {
            UserId = student.Id,
            GeneratedForProjectId = project.Id,
            Title = "Old replaced idea",
            IsSelected = true,
            CreatedAt = DateTime.UtcNow
        };

        // New idea: the real official idea (Project.ProjectIdeaId points
        // here), but IsSelected was never set true by the buggy handler.
        var newIdea = new ProjectIdea
        {
            UserId = student.Id,
            GeneratedForProjectId = project.Id,
            Title = "New official idea",
            IsSelected = false,
            CreatedAt = DateTime.UtcNow
        };

        db.ProjectIdeas.AddRange(oldIdea, newIdea);
        await db.SaveChangesAsync();

        project.ProjectIdeaId = newIdea.Id;
        await db.SaveChangesAsync();

        var controller = new SupervisorEvalController(db);

        var claims = new ClaimsIdentity(
            [
                new Claim("userId", supervisor.Id.ToString()),
                new Claim("userRole", "supervisor")
            ],
            "TestAuth");

        controller.ControllerContext = new ControllerContext
        {
            HttpContext = new DefaultHttpContext
            {
                User = new ClaimsPrincipal(claims)
            }
        };

        var result = Assert.IsType<OkObjectResult>(await controller.GetPending());

        // The response items are an anonymous type (internal to
        // FYPilot.Api); round-trip through JSON to read Id safely
        // across the assembly boundary.
        var json = JsonSerializer.Serialize(result.Value);
        var pending = JsonSerializer.Deserialize<List<PendingIdeaDto>>(
            json,
            new JsonSerializerOptions(JsonSerializerDefaults.Web))!;

        var ids = pending.Select(item => item.Id).ToList();

        Assert.Contains(newIdea.Id, ids);
        Assert.DoesNotContain(oldIdea.Id, ids);
    }

    private sealed record PendingIdeaDto(int Id);
}
