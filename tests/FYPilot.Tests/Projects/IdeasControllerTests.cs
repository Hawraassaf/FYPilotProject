using System.Security.Claims;
using FYPilot.Api.Controllers;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Moq;

namespace FYPilot.Tests.Projects;

/// <summary>
/// Regression coverage for api/ideas/selected and api/ideas/{id}/select,
/// which used to read/write the legacy ProjectIdea.IsSelected flag and
/// could return or leave behind the wrong (old) idea once a project had
/// more than one flagged row.
/// </summary>
public sealed class IdeasControllerTests
{
    [Fact]
    public async Task GetSelected_StaleFlags_ReturnsIdeaFromAuthoritativeProjectIdeaId()
    {
        await using var scope = await TestScope.CreateAsync();

        // TEST C: database has two stale IsSelected=true rows, but the
        // project FK genuinely points at idea2.
        var idea1 = scope.AddIdea(isSelected: true);
        var idea2 = scope.AddIdea(isSelected: true);
        await scope.Db.SaveChangesAsync();

        scope.Project.ProjectIdeaId = idea2.Id;
        await scope.Db.SaveChangesAsync();

        var controller = scope.CreateController();

        var result = Assert.IsType<OkObjectResult>(
            await controller.GetSelected(scope.Project.Id, CancellationToken.None));

        var response = Assert.IsType<ProjectIdeaResponse>(result.Value);
        Assert.Equal(idea2.Id, response.Id);
    }

    [Fact]
    public async Task Select_ScopedToProject_DoesNotAffectAnotherProject()
    {
        await using var scope = await TestScope.CreateAsync();

        var projectB = scope.AddProject();

        var ideaA1 = scope.AddIdea(isSelected: true);
        var ideaB1 = scope.AddIdea(isSelected: true, generatedForProjectId: projectB.Id);
        var ideaA2 = scope.AddIdea(isSelected: false);
        await scope.Db.SaveChangesAsync();

        scope.Project.ProjectIdeaId = ideaA1.Id;
        projectB.ProjectIdeaId = ideaB1.Id;
        await scope.Db.SaveChangesAsync();

        var controller = scope.CreateController();

        await controller.Select(
            ideaA2.Id,
            scope.Project.Id,
            CancellationToken.None);

        scope.Db.ChangeTracker.Clear();

        var refreshedProjectA = await scope.Db.Projects.SingleAsync(p => p.Id == scope.Project.Id);
        var refreshedProjectB = await scope.Db.Projects.SingleAsync(p => p.Id == projectB.Id);
        var refreshedIdeaB1 = await scope.Db.ProjectIdeas.SingleAsync(i => i.Id == ideaB1.Id);

        Assert.Equal(ideaA2.Id, refreshedProjectA.ProjectIdeaId);
        Assert.Equal(ideaB1.Id, refreshedProjectB.ProjectIdeaId);
        Assert.True(refreshedIdeaB1.IsSelected);
    }

    private sealed class TestScope : IAsyncDisposable
    {
        private TestScope(
            SqliteConnection connection,
            ApplicationDbContext db,
            User student,
            Project project)
        {
            Connection = connection;
            Db = db;
            Student = student;
            Project = project;
        }

        public SqliteConnection Connection { get; }
        public ApplicationDbContext Db { get; }
        public User Student { get; }
        public Project Project { get; }

        public ProjectIdea AddIdea(bool isSelected, int? generatedForProjectId = null)
        {
            var idea = new ProjectIdea
            {
                UserId = Student.Id,
                GeneratedForProjectId = generatedForProjectId ?? Project.Id,
                Title = $"Idea {Guid.NewGuid():N}",
                IsSelected = isSelected,
                CreatedAt = DateTime.UtcNow
            };

            Db.ProjectIdeas.Add(idea);
            return idea;
        }

        public Project AddProject()
        {
            var project = new Project
            {
                Title = "Project B",
                Description = "",
                Technologies = "",
                Status = "planning",
                StudentId = Student.Id,
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            Db.Projects.Add(project);
            Db.SaveChanges();
            return project;
        }

        public IdeasController CreateController()
        {
            var activeProjectService = new Mock<IActiveProjectService>();
            activeProjectService
                .Setup(service => service.GetActiveProjectIdAsync(
                    Student.Id,
                    It.IsAny<CancellationToken>()))
                .ReturnsAsync(Project.Id);

            var controller = new IdeasController(Db, activeProjectService.Object);

            var claims = new ClaimsIdentity(
                [new Claim("userId", Student.Id.ToString())],
                "TestAuth");

            controller.ControllerContext = new ControllerContext
            {
                HttpContext = new DefaultHttpContext
                {
                    User = new ClaimsPrincipal(claims)
                }
            };

            return controller;
        }

        public static async Task<TestScope> CreateAsync()
        {
            var connection = new SqliteConnection("Data Source=:memory:");
            await connection.OpenAsync();

            var options = new DbContextOptionsBuilder<ApplicationDbContext>()
                .UseSqlite(connection)
                .Options;

            var db = new ApplicationDbContext(options);
            await db.Database.EnsureCreatedAsync();

            var student = new User
            {
                FullName = "Student",
                Email = "student@test.local",
                PasswordHash = "test-hash",
                Role = "student",
                CreatedAt = DateTime.UtcNow
            };

            db.Users.Add(student);
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

            return new TestScope(connection, db, student, project);
        }

        public async ValueTask DisposeAsync()
        {
            await Db.DisposeAsync();
            await Connection.DisposeAsync();
        }
    }
}
