using System.Data;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Storage;

namespace FYPilot.Web.Services.Projects;

public sealed class ProjectLifecycleService(
    ApplicationDbContext db,
    INotificationService notificationService,
    ILogger<ProjectLifecycleService> logger)
    : IProjectLifecycleService
{
    public async Task<ProjectLifecycleResult> ArchiveAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken = default)
    {
        if (projectId <= 0 || userId <= 0)
        {
            return Failed(
                "The selected project is invalid.");
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var membership =
                await db.ProjectMembers
                    .Include(member => member.Project)
                    .Include(member => member.User)
                    .FirstOrDefaultAsync(
                        member =>
                            member.ProjectId == projectId &&
                            member.UserId == userId &&
                            member.Status == "active" &&
                            member.Project != null &&
                            !member.Project.IsDeleted,
                        cancellationToken);

            if (membership?.Project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Failed(
                    "You do not have access to that project.");
            }

            if (membership.IsArchived)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Succeeded(
                    "The project is already in your Archive.");
            }

            var now = DateTime.UtcNow;
            var project = membership.Project;
            var actorName =
                SafeName(membership.User?.FullName);

            membership.IsArchived = true;
            membership.ArchivedAtUtc = now;

            ClearProjectContext(
                membership.User,
                project.Id,
                now);

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId,
                    ActionType = "project_archived",
                    Description =
                        $"{actorName} moved the project "
                        + "to their Archive.",
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await NotifyProjectObserversAsync(
                projectId: project.Id,
                supervisorId: project.SupervisorId,
                actorUserId: userId,
                title: "Project archived",
                message:
                    $"{actorName} moved "
                    + $"\"{SafeProjectTitle(project.Title)}\" "
                    + "to their Archive.",
                type: "project_archived",
                cancellationToken: cancellationToken);

            return Succeeded(
                "The project was moved to your Archive.");
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to archive project {ProjectId} "
                + "for user {UserId}.",
                projectId,
                userId);

            return Failed(
                "The project could not be archived.");
        }
    }

    public async Task<ProjectLifecycleResult> RestoreAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken = default)
    {
        if (projectId <= 0 || userId <= 0)
        {
            return Failed(
                "The selected project is invalid.");
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var membership =
                await db.ProjectMembers
                    .Include(member => member.Project)
                    .Include(member => member.User)
                    .FirstOrDefaultAsync(
                        member =>
                            member.ProjectId == projectId &&
                            member.UserId == userId &&
                            member.Status == "active" &&
                            member.Project != null &&
                            !member.Project.IsDeleted,
                        cancellationToken);

            if (membership?.Project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Failed(
                    "You do not have access to that project.");
            }

            if (!membership.IsArchived)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Succeeded(
                    "The project is already in My Projects.");
            }

            var now = DateTime.UtcNow;
            var project = membership.Project;
            var actorName =
                SafeName(membership.User?.FullName);

            membership.IsArchived = false;
            membership.ArchivedAtUtc = null;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId,
                    ActionType = "project_restored",
                    Description =
                        $"{actorName} restored the project "
                        + "from their Archive.",
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await NotifyProjectObserversAsync(
                projectId: project.Id,
                supervisorId: project.SupervisorId,
                actorUserId: userId,
                title: "Project restored",
                message:
                    $"{actorName} restored "
                    + $"\"{SafeProjectTitle(project.Title)}\" "
                    + "from their Archive.",
                type: "project_restored",
                cancellationToken: cancellationToken);

            return Succeeded(
                "The project was restored to My Projects.");
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to restore project {ProjectId} "
                + "for user {UserId}.",
                projectId,
                userId);

            return Failed(
                "The project could not be restored.");
        }
    }

    public async Task<ProjectRemovalPreview>
        GetRemovalPreviewAsync(
            int projectId,
            int userId,
            CancellationToken cancellationToken = default)
    {
        if (projectId <= 0 || userId <= 0)
        {
            return FailedPreview(
                "The selected project is invalid.");
        }

        var project =
            await db.Projects
                .AsNoTracking()
                .Include(item => item.Members
                    .Where(member =>
                        member.Status == "active"))
                .ThenInclude(member => member.User)
                .AsSplitQuery()
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == projectId &&
                        !item.IsDeleted,
                    cancellationToken);

        if (project == null)
        {
            return FailedPreview(
                "The selected project could not be found.");
        }

        var membership =
            project.Members.FirstOrDefault(
                member =>
                    member.UserId == userId &&
                    member.Status == "active");

        if (membership == null)
        {
            return FailedPreview(
                "You do not have access to that project.");
        }

        var isOwner =
            project.StudentId == userId;

        if (!isOwner)
        {
            return new ProjectRemovalPreview(
                Succeeded: true,
                Message:
                    "The project will be removed "
                    + "from your projects.",
                IsOwner: false,
                ActiveCollaboratorCount: 0,
                AutomaticNewOwnerUserId: null,
                OwnerCandidates:
                    Array.Empty<ProjectOwnerCandidate>());
        }

        var candidates =
            project.Members
                .Where(member =>
                    member.Status == "active" &&
                    member.UserId != userId &&
                    member.User != null)
                .OrderBy(member =>
                    member.JoinedAt)
                .Select(member =>
                    new ProjectOwnerCandidate(
                        UserId: member.UserId,
                        FullName:
                            SafeName(
                                member.User!.FullName),
                        Email:
                            member.User.Email,
                        IsArchived:
                            member.IsArchived))
                .ToList();

        int? automaticNewOwnerUserId =
     candidates.Count == 1
         ? candidates[0].UserId
         : null;

        var message = candidates.Count switch
        {
            0 =>
                "The project will no longer appear "
                + "in your account.",

            1 =>
                $"{candidates[0].FullName} will become "
                + "the new project owner.",

            _ =>
                "Choose a new owner before removing "
                + "the project from your projects."
        };

        return new ProjectRemovalPreview(
            Succeeded: true,
            Message: message,
            IsOwner: true,
            ActiveCollaboratorCount:
                candidates.Count,
            AutomaticNewOwnerUserId:
                automaticNewOwnerUserId,
            OwnerCandidates:
                candidates);
    }

    public async Task<ProjectLifecycleResult> RemoveAsync(
        int projectId,
        int userId,
        int? newOwnerUserId = null,
        CancellationToken cancellationToken = default)
    {
        if (projectId <= 0 || userId <= 0)
        {
            return Failed(
                "The selected project is invalid.");
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var project =
                await db.Projects
                    .Include(item => item.Members)
                    .ThenInclude(member => member.User)
                    .Include(item => item.Invitations)
                    .AsSplitQuery()
                    .FirstOrDefaultAsync(
                        item =>
                            item.Id == projectId &&
                            !item.IsDeleted,
                        cancellationToken);

            if (project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Failed(
                    "The selected project could not be found.");
            }

            var currentMembership =
                project.Members.FirstOrDefault(
                    member =>
                        member.UserId == userId &&
                        member.Status == "active");

            if (currentMembership == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return Failed(
                    "You do not have access to that project.");
            }

            var isOwner =
                project.StudentId == userId;

            if (!isOwner)
            {
                var collaboratorResult =
                    await RemoveCollaboratorAsync(
                        project,
                        currentMembership,
                        userId,
                        transaction,
                        cancellationToken);

                return collaboratorResult;
            }

            var activeCollaborators =
                project.Members
                    .Where(member =>
                        member.Status == "active" &&
                        member.UserId != userId &&
                        member.User != null)
                    .OrderBy(member =>
                        member.JoinedAt)
                    .ToList();

            if (activeCollaborators.Count == 0)
            {
                return await RemoveOwnerOnlyProjectAsync(
                    project,
                    currentMembership,
                    userId,
                    transaction,
                    cancellationToken);
            }

            ProjectMember? selectedNewOwner;

            if (activeCollaborators.Count == 1)
            {
                selectedNewOwner =
                    activeCollaborators[0];
            }
            else
            {
                if (!newOwnerUserId.HasValue)
                {
                    await transaction.RollbackAsync(
                        cancellationToken);

                    return new ProjectLifecycleResult(
                        Succeeded: false,
                        Message:
                            "Choose a new owner before "
                            + "removing this project.",
                        RequiresOwnerSelection: true);
                }

                selectedNewOwner =
                    activeCollaborators.FirstOrDefault(
                        member =>
                            member.UserId ==
                            newOwnerUserId.Value);

                if (selectedNewOwner == null)
                {
                    await transaction.RollbackAsync(
                        cancellationToken);

                    return new ProjectLifecycleResult(
                        Succeeded: false,
                        Message:
                            "The selected new owner is "
                            + "not an active collaborator.",
                        RequiresOwnerSelection: true);
                }
            }

            return await TransferOwnershipAndRemoveAsync(
                project,
                currentMembership,
                selectedNewOwner,
                userId,
                transaction,
                cancellationToken);
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to remove project {ProjectId} "
                + "for user {UserId}.",
                projectId,
                userId);

            return Failed(
                "The project could not be removed.");
        }
    }

    private async Task<ProjectLifecycleResult>
        RemoveCollaboratorAsync(
            Project project,
            ProjectMember membership,
            int userId,
            IDbContextTransaction transaction,
            CancellationToken cancellationToken)
    {
        var now = DateTime.UtcNow;
        var actorName =
            SafeName(membership.User?.FullName);

        MarkMembershipRemoved(
            membership,
            now);

        ClearProjectContext(
            membership.User,
            project.Id,
            now);

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId = project.Id,
                UserId = userId,
                ActionType = "member_removed",
                Description =
                    $"{actorName} removed the project "
                    + "from their projects.",
                CreatedAtUtc = now
            });

        await db.SaveChangesAsync(
            cancellationToken);

        await transaction.CommitAsync(
            cancellationToken);

        await NotifyProjectObserversAsync(
            projectId: project.Id,
            supervisorId: project.SupervisorId,
            actorUserId: userId,
            title: "Project member removed",
            message:
                $"{actorName} removed "
                + $"\"{SafeProjectTitle(project.Title)}\" "
                + "from their projects.",
            type: "project_member_removed",
            cancellationToken: cancellationToken);

        return Succeeded(
            "The project was removed from your projects.");
    }

    private async Task<ProjectLifecycleResult>
        RemoveOwnerOnlyProjectAsync(
            Project project,
            ProjectMember ownerMembership,
            int userId,
            IDbContextTransaction transaction,
            CancellationToken cancellationToken)
    {
        var now = DateTime.UtcNow;
        var actorName =
            SafeName(ownerMembership.User?.FullName);

        project.IsDeleted = true;
        project.DeletedAtUtc = now;
        project.DeletedByUserId = userId;
        project.UpdatedAt = now;

        MarkMembershipRemoved(
            ownerMembership,
            now);

        ClearProjectContext(
            ownerMembership.User,
            project.Id,
            now);

        foreach (var invitation in project.Invitations
                     .Where(invitation =>
                         invitation.Status == "pending"))
        {
            invitation.Status = "cancelled";
            invitation.RespondedAt = now;
        }

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId = project.Id,
                UserId = userId,
                ActionType = "project_removed",
                Description =
                    $"{actorName} removed the project "
                    + "from their projects.",
                CreatedAtUtc = now
            });

        await db.SaveChangesAsync(
            cancellationToken);

        await transaction.CommitAsync(
            cancellationToken);

        await NotifyProjectObserversAsync(
            projectId: project.Id,
            supervisorId: project.SupervisorId,
            actorUserId: userId,
            title: "Project removed",
            message:
                $"{actorName} removed "
                + $"\"{SafeProjectTitle(project.Title)}\".",
            type: "project_removed",
            cancellationToken: cancellationToken);

        return Succeeded(
            "The project was removed from your projects.");
    }

    private async Task<ProjectLifecycleResult>
        TransferOwnershipAndRemoveAsync(
            Project project,
            ProjectMember oldOwnerMembership,
            ProjectMember newOwnerMembership,
            int oldOwnerUserId,
            IDbContextTransaction transaction,
            CancellationToken cancellationToken)
    {
        var now = DateTime.UtcNow;

        var oldOwnerName =
            SafeName(oldOwnerMembership.User?.FullName);

        var newOwnerName =
            SafeName(newOwnerMembership.User?.FullName);

        project.StudentId =
            newOwnerMembership.UserId;

        project.UpdatedAt =
            now;

        oldOwnerMembership.Role =
            "collaborator";

        MarkMembershipRemoved(
            oldOwnerMembership,
            now);

        newOwnerMembership.Role =
            "owner";

        newOwnerMembership.Status =
            "active";

        newOwnerMembership.IsArchived =
            false;

        newOwnerMembership.ArchivedAtUtc =
            null;

        newOwnerMembership.RemovedAtUtc =
            null;

        newOwnerMembership.LeftAt =
            null;

        ClearProjectContext(
            oldOwnerMembership.User,
            project.Id,
            now);

        db.ProjectActivities.AddRange(
            new ProjectActivity
            {
                ProjectId = project.Id,
                UserId = oldOwnerUserId,
                ActionType = "ownership_transferred",
                Description =
                    $"Project ownership was transferred "
                    + $"from {oldOwnerName} "
                    + $"to {newOwnerName}.",
                CreatedAtUtc = now
            },
            new ProjectActivity
            {
                ProjectId = project.Id,
                UserId = oldOwnerUserId,
                ActionType = "member_removed",
                Description =
                    $"{oldOwnerName} removed the project "
                    + "from their projects.",
                CreatedAtUtc = now
            });

        await db.SaveChangesAsync(
            cancellationToken);

        await transaction.CommitAsync(
            cancellationToken);

        await TryNotifyAsync(
            recipientUserId:
                newOwnerMembership.UserId,
            title:
                "You are now the project owner",
            message:
                $"{oldOwnerName} transferred ownership "
                + $"of \"{SafeProjectTitle(project.Title)}\" "
                + "to you.",
            type:
                "ownership_transferred",
            url:
                $"/Student/MyProjects",
            projectId:
                project.Id,
            actorUserId:
                oldOwnerUserId,
            cancellationToken:
                cancellationToken);

        await NotifyProjectObserversAsync(
            projectId: project.Id,
            supervisorId: project.SupervisorId,
            actorUserId: oldOwnerUserId,
            title: "Project ownership changed",
            message:
                $"{newOwnerName} is now the owner of "
                + $"\"{SafeProjectTitle(project.Title)}\".",
            type: "ownership_transferred",
            excludedRecipientUserId:
                newOwnerMembership.UserId,
            cancellationToken:
                cancellationToken);

        return new ProjectLifecycleResult(
            Succeeded: true,
            Message:
                $"The project was removed and "
                + $"{newOwnerName} is now the owner.",
            RequiresOwnerSelection: false,
            NewOwnerUserId:
                newOwnerMembership.UserId);
    }

    private async Task NotifyProjectObserversAsync(
        int projectId,
        int? supervisorId,
        int actorUserId,
        string title,
        string message,
        string type,
        int? excludedRecipientUserId = null,
        CancellationToken cancellationToken = default)
    {
        var recipientIds =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId == projectId &&
                    member.Status == "active" &&
                    member.UserId != actorUserId)
                .Select(member =>
                    member.UserId)
                .Distinct()
                .ToListAsync(
                    cancellationToken);

        if (supervisorId.HasValue &&
            supervisorId.Value > 0 &&
            supervisorId.Value != actorUserId)
        {
            recipientIds.Add(
                supervisorId.Value);
        }

        foreach (var recipientId in recipientIds
                     .Distinct()
                     .Where(recipientId =>
                         !excludedRecipientUserId.HasValue ||
                         recipientId !=
                         excludedRecipientUserId.Value))
        {
            var url =
                supervisorId.HasValue &&
                recipientId == supervisorId.Value
                    ? $"/Supervisor/ProgressTracking"
                      + $"?projectId={projectId}"
                    : "/Student/MyProjects";

            await TryNotifyAsync(
                recipientUserId:
                    recipientId,
                title:
                    title,
                message:
                    message,
                type:
                    type,
                url:
                    url,
                projectId:
                    projectId,
                actorUserId:
                    actorUserId,
                cancellationToken:
                    cancellationToken);
        }
    }

    private async Task TryNotifyAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url,
        int projectId,
        int actorUserId,
        CancellationToken cancellationToken)
    {
        if (recipientUserId <= 0 ||
            recipientUserId == actorUserId)
        {
            return;
        }

        try
        {
            await notificationService.NotifyUserAsync(
                recipientUserId:
                    recipientUserId,
                title:
                    title,
                message:
                    message,
                type:
                    type,
                url:
                    url,
                sendEmail:
                    false,
                projectId:
                    projectId,
                actorUserId:
                    actorUserId,
                cancellationToken:
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Project lifecycle operation completed, "
                + "but notification delivery failed for "
                + "user {UserId}, project {ProjectId}.",
                recipientUserId,
                projectId);
        }
    }

    private static void MarkMembershipRemoved(
        ProjectMember membership,
        DateTime now)
    {
        membership.Status = "removed";
        membership.LeftAt = now;
        membership.RemovedAtUtc = now;
        membership.IsArchived = false;
        membership.ArchivedAtUtc = null;
    }

    private static void ClearProjectContext(
        User? user,
        int projectId,
        DateTime now)
    {
        if (user == null ||
            user.LastActiveProjectId != projectId)
        {
            return;
        }

        user.LastActiveProjectId = null;
        user.LastProjectPage = null;
        user.LastProjectVisitedAtUtc = now;
    }

    private static ProjectLifecycleResult Succeeded(
        string message)
    {
        return new ProjectLifecycleResult(
            Succeeded: true,
            Message: message);
    }

    private static ProjectLifecycleResult Failed(
        string message)
    {
        return new ProjectLifecycleResult(
            Succeeded: false,
            Message: message);
    }

    private static ProjectRemovalPreview FailedPreview(
        string message)
    {
        return new ProjectRemovalPreview(
            Succeeded: false,
            Message: message,
            IsOwner: false,
            ActiveCollaboratorCount: 0,
            AutomaticNewOwnerUserId: null,
            OwnerCandidates:
                Array.Empty<ProjectOwnerCandidate>());
    }

    private static string SafeName(
        string? name)
    {
        return string.IsNullOrWhiteSpace(name)
            ? "A project member"
            : name.Trim();
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
    }

    private static async Task SafeRollbackAsync(
        IDbContextTransaction transaction,
        CancellationToken cancellationToken)
    {
        try
        {
            await transaction.RollbackAsync(
                cancellationToken);
        }
        catch
        {
            // Preserve the original exception.
        }
    }
}