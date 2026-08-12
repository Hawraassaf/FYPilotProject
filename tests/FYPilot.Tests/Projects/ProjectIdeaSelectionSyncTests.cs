using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Tests.Projects;

/// <summary>
/// Regression coverage for the "old selected idea keeps showing" bug:
/// Project.ProjectIdeaId is authoritative, but the legacy
/// ProjectIdea.IsSelected flag was only cleared on the old idea by one
/// of the two selection handlers. These tests exercise the shared
/// ProjectIdeaSelectionSync helper both handlers now call.
/// </summary>
public sealed class ProjectIdeaSelectionSyncTests
{
    [Fact]
    public async Task SyncSelectedFlagAsync_ReplacingIdea_ClearsOldAndSetsNew()
    {
        await using var scope = await TestScope.CreateAsync();

        var idea1 = scope.AddIdea(scope.ProjectA.Id, isSelected: true);
        var idea2 = scope.AddIdea(scope.ProjectA.Id, isSelected: false);
        await scope.Db.SaveChangesAsync();

        scope.ProjectA.ProjectIdeaId = idea1.Id;
        await scope.Db.SaveChangesAsync();

        // TEST A: student selects idea2 through either handler.
        scope.ProjectA.ProjectIdeaId = idea2.Id;

        await ProjectIdeaSelectionSync.SyncSelectedFlagAsync(
            scope.Db,
            scope.ProjectA.Id,
            idea2.Id,
            previousIdeaId: idea1.Id,
            CancellationToken.None);

        await scope.Db.SaveChangesAsync();
        scope.Db.ChangeTracker.Clear();

        var refreshedIdea1 = await scope.Db.ProjectIdeas.SingleAsync(i => i.Id == idea1.Id);
        var refreshedIdea2 = await scope.Db.ProjectIdeas.SingleAsync(i => i.Id == idea2.Id);
        var refreshedProject = await scope.Db.Projects.SingleAsync(p => p.Id == scope.ProjectA.Id);

        Assert.Equal(idea2.Id, refreshedProject.ProjectIdeaId);
        Assert.False(refreshedIdea1.IsSelected);
        Assert.True(refreshedIdea2.IsSelected);
    }

    [Fact]
    public async Task SyncSelectedFlagAsync_ScopedToOneProject_DoesNotTouchAnotherProject()
    {
        await using var scope = await TestScope.CreateAsync();

        var ideaA1 = scope.AddIdea(scope.ProjectA.Id, isSelected: true);
        var ideaB1 = scope.AddIdea(scope.ProjectB.Id, isSelected: true);
        await scope.Db.SaveChangesAsync();

        scope.ProjectA.ProjectIdeaId = ideaA1.Id;
        scope.ProjectB.ProjectIdeaId = ideaB1.Id;
        await scope.Db.SaveChangesAsync();

        var ideaA2 = scope.AddIdea(scope.ProjectA.Id, isSelected: false);
        await scope.Db.SaveChangesAsync();

        // TEST B: student changes the selected idea in Project A only.
        scope.ProjectA.ProjectIdeaId = ideaA2.Id;

        await ProjectIdeaSelectionSync.SyncSelectedFlagAsync(
            scope.Db,
            scope.ProjectA.Id,
            ideaA2.Id,
            previousIdeaId: ideaA1.Id,
            CancellationToken.None);

        await scope.Db.SaveChangesAsync();
        scope.Db.ChangeTracker.Clear();

        var refreshedProjectB = await scope.Db.Projects.SingleAsync(p => p.Id == scope.ProjectB.Id);
        var refreshedIdeaB1 = await scope.Db.ProjectIdeas.SingleAsync(i => i.Id == ideaB1.Id);

        Assert.Equal(ideaB1.Id, refreshedProjectB.ProjectIdeaId);
        Assert.True(refreshedIdeaB1.IsSelected);
    }

    [Fact]
    public async Task SyncSelectedFlagAsync_LegacyIdeaWithoutProjectLink_IsStillCleared()
    {
        await using var scope = await TestScope.CreateAsync();

        // Legacy idea saved before GeneratedForProjectId existed.
        var legacyIdea = scope.AddIdea(generatedForProjectId: null, isSelected: true);
        var newIdea = scope.AddIdea(scope.ProjectA.Id, isSelected: false);
        await scope.Db.SaveChangesAsync();

        scope.ProjectA.ProjectIdeaId = legacyIdea.Id;
        await scope.Db.SaveChangesAsync();

        scope.ProjectA.ProjectIdeaId = newIdea.Id;

        await ProjectIdeaSelectionSync.SyncSelectedFlagAsync(
            scope.Db,
            scope.ProjectA.Id,
            newIdea.Id,
            previousIdeaId: legacyIdea.Id,
            CancellationToken.None);

        await scope.Db.SaveChangesAsync();
        scope.Db.ChangeTracker.Clear();

        var refreshedLegacyIdea = await scope.Db.ProjectIdeas.SingleAsync(i => i.Id == legacyIdea.Id);

        Assert.False(refreshedLegacyIdea.IsSelected);
    }

    private sealed class TestScope : IAsyncDisposable
    {
        private TestScope(
            SqliteConnection connection,
            ApplicationDbContext db,
            Project projectA,
            Project projectB)
        {
            Connection = connection;
            Db = db;
            ProjectA = projectA;
            ProjectB = projectB;
        }

        public SqliteConnection Connection { get; }
        public ApplicationDbContext Db { get; }
        public Project ProjectA { get; }
        public Project ProjectB { get; }

        public ProjectIdea AddIdea(
            int? generatedForProjectId,
            bool isSelected)
        {
            var idea = new ProjectIdea
            {
                UserId = ProjectA.StudentId,
                GeneratedForProjectId = generatedForProjectId,
                Title = $"Idea {Guid.NewGuid():N}",
                IsSelected = isSelected,
                CreatedAt = DateTime.UtcNow
            };

            Db.ProjectIdeas.Add(idea);
            return idea;
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

            var projectA = new Project
            {
                Title = "Project A",
                Description = "",
                Technologies = "",
                Status = "planning",
                StudentId = student.Id,
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            var projectB = new Project
            {
                Title = "Project B",
                Description = "",
                Technologies = "",
                Status = "planning",
                StudentId = student.Id,
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            db.Projects.AddRange(projectA, projectB);
            await db.SaveChangesAsync();

            return new TestScope(connection, db, projectA, projectB);
        }

        public async ValueTask DisposeAsync()
        {
            await Db.DisposeAsync();
            await Connection.DisposeAsync();
        }
    }
}
