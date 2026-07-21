using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// Central authorization service for shared projects.
///
/// Students receive access through an active ProjectMember record.
/// Supervisors receive access through Project.SupervisorId.
/// </summary>
public sealed class ProjectAccessService(
    ApplicationDbContext db)
    : IProjectAccessService
{
    public async Task<ProjectAccessResult?> GetAccessAsync(
        int projectId,
        int userId,
        string userRole,
        CancellationToken cancellationToken = default)
    {
        if (projectId <= 0 || userId <= 0)
        {
            return null;
        }

        var normalizedUserRole =
            Normalize(userRole);

        if (normalizedUserRole == "student")
        {
            return await GetStudentAccessAsync(
                projectId,
                userId,
                cancellationToken);
        }

        if (normalizedUserRole == "supervisor")
        {
            return await GetSupervisorAccessAsync(
                projectId,
                userId,
                cancellationToken);
        }

        return null;
    }

    private async Task<ProjectAccessResult?> GetStudentAccessAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken)
    {
        var membership = await db.ProjectMembers
            .AsNoTracking()
            .Where(member =>
                member.ProjectId == projectId &&
                member.UserId == userId &&
                member.Status == "active")
            .Select(member => new
            {
                member.ProjectId,
                member.Role,

                ProjectIdeaId =
                    member.Project != null
                        ? member.Project.ProjectIdeaId
                        : null,

                OwnerUserId =
                    member.Project != null
                        ? member.Project.StudentId
                        : 0,

                SupervisorId =
                    member.Project != null
                        ? member.Project.SupervisorId
                        : null
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (membership == null ||
            membership.OwnerUserId <= 0)
        {
            return null;
        }

        var accessRole =
            Normalize(membership.Role);

        var isOwner =
            accessRole == "owner";

        var isCollaborator =
            accessRole == "collaborator";

        /*
         * A student membership should normally be either owner or
         * collaborator. Unknown membership roles are rejected.
         */
        if (!isOwner && !isCollaborator)
        {
            return null;
        }

        return new ProjectAccessResult(
            ProjectId: membership.ProjectId,
            ProjectIdeaId: membership.ProjectIdeaId,
            OwnerUserId: membership.OwnerUserId,
            SupervisorId: membership.SupervisorId,
            AccessRole: accessRole,
            IsOwner: isOwner,
            IsCollaborator: isCollaborator,
            IsSupervisor: false);
    }

    private async Task<ProjectAccessResult?> GetSupervisorAccessAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken)
    {
        var project = await db.Projects
            .AsNoTracking()
            .Where(item =>
                item.Id == projectId &&
                item.SupervisorId == userId)
            .Select(item => new
            {
                item.Id,
                item.ProjectIdeaId,
                item.StudentId,
                item.SupervisorId
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (project == null)
        {
            return null;
        }

        return new ProjectAccessResult(
            ProjectId: project.Id,
            ProjectIdeaId: project.ProjectIdeaId,
            OwnerUserId: project.StudentId,
            SupervisorId: project.SupervisorId,
            AccessRole: "supervisor",
            IsOwner: false,
            IsCollaborator: false,
            IsSupervisor: true);
    }

    private static string Normalize(string? value)
    {
        return string.IsNullOrWhiteSpace(value)
            ? string.Empty
            : value.Trim().ToLowerInvariant();
    }
}