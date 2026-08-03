namespace FYPilot.Web.Services.Projects;

public interface IProjectLifecycleService
{
    Task<ProjectLifecycleResult> ArchiveAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken = default);

    Task<ProjectLifecycleResult> RestoreAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken = default);

    Task<ProjectRemovalPreview> GetRemovalPreviewAsync(
        int projectId,
        int userId,
        CancellationToken cancellationToken = default);

    Task<ProjectLifecycleResult> RemoveAsync(
        int projectId,
        int userId,
        int? newOwnerUserId = null,
        CancellationToken cancellationToken = default);
}

public sealed record ProjectLifecycleResult(
    bool Succeeded,
    string Message,
    bool RequiresOwnerSelection = false,
    int? NewOwnerUserId = null);

public sealed record ProjectRemovalPreview(
    bool Succeeded,
    string Message,
    bool IsOwner,
    int ActiveCollaboratorCount,
    int? AutomaticNewOwnerUserId,
    IReadOnlyList<ProjectOwnerCandidate> OwnerCandidates);

public sealed record ProjectOwnerCandidate(
    int UserId,
    string FullName,
    string Email,
    bool IsArchived);