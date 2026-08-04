using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.Projects;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Moq;

namespace FYPilot.Tests.Projects;

public sealed class ProjectLifecycleServiceTests
{
    [Fact]
    public async Task ArchiveAsync_ArchivesOnlyCurrentMember_AndKeepsProjectActive()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 1);

        var collaborator = scope.Collaborators[0];

        var result = await scope.Service.ArchiveAsync(
            scope.Project.Id,
            collaborator.Id);

        Assert.True(result.Succeeded);

        scope.Db.ChangeTracker.Clear();

        var membership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == collaborator.Id);

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        var user = await scope.Db.Users
            .SingleAsync(item => item.Id == collaborator.Id);

        Assert.True(membership.IsArchived);
        Assert.NotNull(membership.ArchivedAtUtc);
        Assert.Equal("active", membership.Status);
        Assert.False(project.IsDeleted);
        Assert.Equal(scope.Owner.Id, project.StudentId);
        Assert.Null(user.LastActiveProjectId);
        Assert.Null(user.LastProjectPage);

        Assert.Contains(
            await scope.Db.ProjectActivities.ToListAsync(),
            activity =>
                activity.ProjectId == scope.Project.Id &&
                activity.UserId == collaborator.Id &&
                activity.ActionType == "project_archived");
    }

    [Fact]
    public async Task RestoreAsync_RestoresOnlyCurrentMember()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 1,
            archivedCollaboratorIndexes: [0]);

        var collaborator = scope.Collaborators[0];

        var result = await scope.Service.RestoreAsync(
            scope.Project.Id,
            collaborator.Id);

        Assert.True(result.Succeeded);

        scope.Db.ChangeTracker.Clear();

        var membership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == collaborator.Id);

        Assert.False(membership.IsArchived);
        Assert.Null(membership.ArchivedAtUtc);
        Assert.Equal("active", membership.Status);

        Assert.Contains(
            await scope.Db.ProjectActivities.ToListAsync(),
            activity =>
                activity.ProjectId == scope.Project.Id &&
                activity.UserId == collaborator.Id &&
                activity.ActionType == "project_restored");
    }

    [Fact]
    public async Task RemoveAsync_Collaborator_SoftRemovesOnlyThatMembership()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 1,
            archivedCollaboratorIndexes: [0]);

        var collaborator = scope.Collaborators[0];

        var result = await scope.Service.RemoveAsync(
            scope.Project.Id,
            collaborator.Id);

        Assert.True(result.Succeeded);

        scope.Db.ChangeTracker.Clear();

        var membership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == collaborator.Id);

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        Assert.Equal("removed", membership.Status);
        Assert.NotNull(membership.LeftAt);
        Assert.NotNull(membership.RemovedAtUtc);
        Assert.False(membership.IsArchived);
        Assert.Null(membership.ArchivedAtUtc);
        Assert.False(project.IsDeleted);
        Assert.Equal(scope.Owner.Id, project.StudentId);

        Assert.Contains(
            await scope.Db.ProjectActivities.ToListAsync(),
            activity =>
                activity.ProjectId == scope.Project.Id &&
                activity.UserId == collaborator.Id &&
                activity.ActionType == "member_removed");
    }

    [Fact]
    public async Task RemoveAsync_OwnerWithOneCollaborator_TransfersAutomatically()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 1,
            archivedCollaboratorIndexes: [0]);

        var newOwner = scope.Collaborators[0];

        var result = await scope.Service.RemoveAsync(
            scope.Project.Id,
            scope.Owner.Id);

        Assert.True(result.Succeeded);
        Assert.False(result.RequiresOwnerSelection);
        Assert.Equal(newOwner.Id, result.NewOwnerUserId);

        scope.Db.ChangeTracker.Clear();

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        var oldOwnerMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == scope.Owner.Id);

        var newOwnerMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == newOwner.Id);

        Assert.Equal(newOwner.Id, project.StudentId);
        Assert.False(project.IsDeleted);

        Assert.Equal("removed", oldOwnerMembership.Status);
        Assert.Equal("collaborator", oldOwnerMembership.Role);
        Assert.NotNull(oldOwnerMembership.RemovedAtUtc);

        Assert.Equal("active", newOwnerMembership.Status);
        Assert.Equal("owner", newOwnerMembership.Role);
        Assert.False(newOwnerMembership.IsArchived);
        Assert.Null(newOwnerMembership.ArchivedAtUtc);
        Assert.Null(newOwnerMembership.RemovedAtUtc);

        var activities = await scope.Db.ProjectActivities
            .Where(activity => activity.ProjectId == scope.Project.Id)
            .ToListAsync();

        Assert.Contains(
            activities,
            activity => activity.ActionType == "ownership_transferred");

        Assert.Contains(
            activities,
            activity => activity.ActionType == "member_removed");
    }

    [Fact]
    public async Task RemoveAsync_OwnerWithMultipleCollaborators_RequiresSelection()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 2);

        var result = await scope.Service.RemoveAsync(
            scope.Project.Id,
            scope.Owner.Id);

        Assert.False(result.Succeeded);
        Assert.True(result.RequiresOwnerSelection);
        Assert.Null(result.NewOwnerUserId);

        scope.Db.ChangeTracker.Clear();

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        var ownerMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == scope.Owner.Id);

        Assert.Equal(scope.Owner.Id, project.StudentId);
        Assert.False(project.IsDeleted);
        Assert.Equal("active", ownerMembership.Status);
        Assert.Equal("owner", ownerMembership.Role);
        Assert.Empty(await scope.Db.ProjectActivities.ToListAsync());
    }

    [Fact]
    public async Task RemoveAsync_OwnerWithMultipleCollaborators_TransfersToSelectedMember()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 2,
            archivedCollaboratorIndexes: [1]);

        var selectedNewOwner = scope.Collaborators[1];

        var result = await scope.Service.RemoveAsync(
            scope.Project.Id,
            scope.Owner.Id,
            selectedNewOwner.Id);

        Assert.True(result.Succeeded);
        Assert.Equal(selectedNewOwner.Id, result.NewOwnerUserId);

        scope.Db.ChangeTracker.Clear();

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        var selectedMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == selectedNewOwner.Id);

        var otherMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == scope.Collaborators[0].Id);

        Assert.Equal(selectedNewOwner.Id, project.StudentId);
        Assert.Equal("owner", selectedMembership.Role);
        Assert.Equal("active", selectedMembership.Status);
        Assert.False(selectedMembership.IsArchived);
        Assert.Equal("collaborator", otherMembership.Role);
        Assert.Equal("active", otherMembership.Status);
    }

    [Fact]
    public async Task RemoveAsync_OwnerAlone_SoftDeletesProjectAndCancelsPendingInvitations()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 0,
            includePendingInvitation: true);

        var result = await scope.Service.RemoveAsync(
            scope.Project.Id,
            scope.Owner.Id);

        Assert.True(result.Succeeded);

        scope.Db.ChangeTracker.Clear();

        var project = await scope.Db.Projects
            .SingleAsync(item => item.Id == scope.Project.Id);

        var ownerMembership = await scope.Db.ProjectMembers
            .SingleAsync(member =>
                member.ProjectId == scope.Project.Id &&
                member.UserId == scope.Owner.Id);

        var invitation = await scope.Db.ProjectInvitations
            .SingleAsync(item => item.ProjectId == scope.Project.Id);

        Assert.True(project.IsDeleted);
        Assert.NotNull(project.DeletedAtUtc);
        Assert.Equal(scope.Owner.Id, project.DeletedByUserId);
        Assert.Equal("removed", ownerMembership.Status);
        Assert.NotNull(ownerMembership.RemovedAtUtc);
        Assert.Equal("cancelled", invitation.Status);
        Assert.NotNull(invitation.RespondedAt);

        Assert.Contains(
            await scope.Db.ProjectActivities.ToListAsync(),
            activity =>
                activity.ProjectId == scope.Project.Id &&
                activity.ActionType == "project_removed");
    }

    [Fact]
    public async Task GetRemovalPreviewAsync_MultipleCollaborators_ReturnsCandidatesAndArchiveState()
    {
        await using var scope = await LifecycleTestScope.CreateAsync(
            collaboratorCount: 2,
            archivedCollaboratorIndexes: [1]);

        var preview = await scope.Service.GetRemovalPreviewAsync(
            scope.Project.Id,
            scope.Owner.Id);

        Assert.True(preview.Succeeded);
        Assert.True(preview.IsOwner);
        Assert.Equal(2, preview.ActiveCollaboratorCount);
        Assert.Null(preview.AutomaticNewOwnerUserId);
        Assert.Equal(2, preview.OwnerCandidates.Count);

        var archivedCandidate = Assert.Single(
            preview.OwnerCandidates.Where(candidate => candidate.IsArchived));

        Assert.Equal(scope.Collaborators[1].Id, archivedCandidate.UserId);
    }

    private sealed class LifecycleTestScope : IAsyncDisposable
    {
        private LifecycleTestScope(
            SqliteConnection connection,
            ApplicationDbContext db,
            ProjectLifecycleService service,
            Project project,
            User owner,
            IReadOnlyList<User> collaborators,
            User supervisor)
        {
            Connection = connection;
            Db = db;
            Service = service;
            Project = project;
            Owner = owner;
            Collaborators = collaborators;
            Supervisor = supervisor;
        }

        public SqliteConnection Connection { get; }
        public ApplicationDbContext Db { get; }
        public ProjectLifecycleService Service { get; }
        public Project Project { get; }
        public User Owner { get; }
        public IReadOnlyList<User> Collaborators { get; }
        public User Supervisor { get; }

        public static async Task<LifecycleTestScope> CreateAsync(
            int collaboratorCount,
            IReadOnlyCollection<int>? archivedCollaboratorIndexes = null,
            bool includePendingInvitation = false)
        {
            var connection = new SqliteConnection("Data Source=:memory:");
            await connection.OpenAsync();

            var options = new DbContextOptionsBuilder<ApplicationDbContext>()
                .UseSqlite(connection)
                .EnableSensitiveDataLogging()
                .Options;

            var db = new ApplicationDbContext(options);
            await db.Database.EnsureCreatedAsync();

            var owner = NewUser("Owner", "owner@test.local");
            var supervisor = NewUser(
                "Supervisor",
                "supervisor@test.local",
                "supervisor");

            var collaborators = Enumerable.Range(1, collaboratorCount)
                .Select(index => NewUser(
                    $"Collaborator {index}",
                    $"collaborator{index}@test.local"))
                .ToList();

            db.Users.Add(owner);
            db.Users.Add(supervisor);
            db.Users.AddRange(collaborators);
            await db.SaveChangesAsync();

            var project = new Project
            {
                Title = "Lifecycle Test Project",
                Description = "Project used by lifecycle regression tests.",
                Technologies = "ASP.NET Core",
                Status = "planning",
                StudentId = owner.Id,
                SupervisorId = supervisor.Id,
                MaximumMembers = Math.Max(1, collaboratorCount + 1),
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            db.Projects.Add(project);
            await db.SaveChangesAsync();

            var ownerMembership = new ProjectMember
            {
                ProjectId = project.Id,
                UserId = owner.Id,
                Role = "owner",
                Status = "active",
                JoinedAt = DateTime.UtcNow
            };

            db.ProjectMembers.Add(ownerMembership);

            for (var index = 0; index < collaborators.Count; index++)
            {
                var isArchived =
                    archivedCollaboratorIndexes?.Contains(index) == true;

                db.ProjectMembers.Add(new ProjectMember
                {
                    ProjectId = project.Id,
                    UserId = collaborators[index].Id,
                    Role = "collaborator",
                    Status = "active",
                    JoinedAt = DateTime.UtcNow.AddMinutes(index + 1),
                    IsArchived = isArchived,
                    ArchivedAtUtc = isArchived
                        ? DateTime.UtcNow
                        : null
                });
            }

            owner.LastActiveProjectId = project.Id;
            owner.LastProjectPage = "/Student/Dashboard";
            owner.LastProjectVisitedAtUtc = DateTime.UtcNow;

            foreach (var collaborator in collaborators)
            {
                collaborator.LastActiveProjectId = project.Id;
                collaborator.LastProjectPage = "/Student/Dashboard";
                collaborator.LastProjectVisitedAtUtc = DateTime.UtcNow;
            }

            if (includePendingInvitation)
            {
                db.ProjectInvitations.Add(new ProjectInvitation
                {
                    ProjectId = project.Id,
                    InvitedByUserId = owner.Id,
                    InvitedEmail = "pending@test.local",
                    TokenHash = Guid.NewGuid().ToString("N"),
                    Status = "pending",
                    Source = "student_invite",
                    CreatedAt = DateTime.UtcNow,
                    ExpiresAt = DateTime.UtcNow.AddDays(7)
                });
            }

            await db.SaveChangesAsync();

            var notifications = new Mock<INotificationService>();
            notifications
                .Setup(service => service.NotifyUserAsync(
                    It.IsAny<int>(),
                    It.IsAny<string>(),
                    It.IsAny<string>(),
                    It.IsAny<string>(),
                    It.IsAny<string>(),
                    It.IsAny<bool>(),
                    It.IsAny<string?>(),
                    It.IsAny<string?>(),
                    It.IsAny<int?>(),
                    It.IsAny<int?>(),
                    It.IsAny<CancellationToken>()))
                .Returns(Task.CompletedTask);

            var service = new ProjectLifecycleService(
                db,
                notifications.Object,
                Mock.Of<ILogger<ProjectLifecycleService>>());

            return new LifecycleTestScope(
                connection,
                db,
                service,
                project,
                owner,
                collaborators,
                supervisor);
        }

        public async ValueTask DisposeAsync()
        {
            await Db.DisposeAsync();
            await Connection.DisposeAsync();
        }

        private static User NewUser(
            string fullName,
            string email,
            string role = "student")
        {
            return new User
            {
                FullName = fullName,
                Email = email,
                PasswordHash = "test-hash",
                Role = role,
                CreatedAt = DateTime.UtcNow
            };
        }
    }
}