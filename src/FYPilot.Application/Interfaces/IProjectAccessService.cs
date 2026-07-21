namespace FYPilot.Application.Interfaces;

/// <summary>
/// Provides centralized authorization information for one project.
/// Every shared project page should use this service before loading
/// or modifying project data.
/// </summary>
public interface IProjectAccessService
{
    Task<ProjectAccessResult?> GetAccessAsync(
        int projectId,
        int userId,
        string userRole,
        CancellationToken cancellationToken = default);
}

/// <summary>
/// Represents the validated relationship between a user and a project.
/// </summary>
public sealed record ProjectAccessResult(
    int ProjectId,
    int? ProjectIdeaId,
    int OwnerUserId,
    int? SupervisorId,
    string AccessRole,
    bool IsOwner,
    bool IsCollaborator,
    bool IsSupervisor);