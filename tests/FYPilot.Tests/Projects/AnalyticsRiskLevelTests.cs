using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Pages.Admin;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Tests.Projects;

/// <summary>
/// TEST E: admin risk scoring must resolve "the student's currently
/// selected idea" through the project's authoritative ProjectIdeaId,
/// not the legacy IsSelected flag (which can leave an old, already
/// reviewed-and-rejected idea flagged true after the student replaced
/// it with a fresh, not-yet-reviewed idea).
/// </summary>
public sealed class AnalyticsRiskLevelTests
{
    [Fact]
    public async Task OnGetAsync_StaleIsSelectedOnRejectedIdea_DoesNotMarkStudentHighRisk()
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

        var rejectedIdea = new ProjectIdea
        {
            UserId = student.Id,
            GeneratedForProjectId = project.Id,
            Title = "Old rejected idea",
            IsSelected = true, // stale: no longer the project's idea
            CreatedAt = DateTime.UtcNow.AddDays(-2)
        };

        var newIdea = new ProjectIdea
        {
            UserId = student.Id,
            GeneratedForProjectId = project.Id,
            Title = "New unreviewed idea",
            IsSelected = false,
            CreatedAt = DateTime.UtcNow
        };

        db.ProjectIdeas.AddRange(rejectedIdea, newIdea);
        await db.SaveChangesAsync();

        project.ProjectIdeaId = newIdea.Id;
        project.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync();

        db.SupervisorAssignments.Add(new SupervisorAssignment
        {
            StudentId = student.Id,
            SupervisorId = supervisor.Id,
            Status = "active",
            RequestedAt = DateTime.UtcNow
        });

        db.SupervisorEvaluations.Add(new SupervisorEvaluation
        {
            IdeaId = rejectedIdea.Id,
            SupervisorId = supervisor.Id,
            Status = "rejected",
            CreatedAt = DateTime.UtcNow.AddDays(-1),
            UpdatedAt = DateTime.UtcNow.AddDays(-1)
        });

        await db.SaveChangesAsync();

        var pageModel = new AnalyticsModel(db);
        await pageModel.OnGetAsync();

        // The new idea has no evaluation yet -> medium risk, not the
        // "high" the stale rejected flag would have produced.
        Assert.Equal(0, pageModel.HighRiskStudents);
        Assert.Equal(1, pageModel.MediumRiskStudents);
    }
}
