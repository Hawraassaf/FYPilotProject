namespace FYPilot.Application.Interfaces;

/// <summary>
/// Saves and restores the student's current project context.
/// </summary>
public interface IActiveProjectService
{
    /// <summary>
    /// Makes a project active for the student.
    /// Returns null when the student does not have access.
    /// </summary>
    Task<ActiveProjectDestination?> ActivateProjectAsync(
        int userId,
        int projectId,
        string? requestedPage = null,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Remembers the page the student successfully visited
    /// inside a project.
    /// </summary>
    Task<bool> RememberPageAsync(
        int userId,
        int projectId,
        string pageName,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Returns the student's last valid project and page.
    /// Invalid saved project information is cleared automatically.
    /// </summary>
    Task<ActiveProjectDestination?> GetResumeDestinationAsync(
        int userId,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Clears the saved active-project information.
    /// </summary>
    Task ClearActiveProjectAsync(
        int userId,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// Checks whether a route is approved as a resumable
    /// project-related page.
    /// </summary>
    bool IsAllowedProjectPage(string? pageName);
}

public sealed record ActiveProjectDestination(
    int ProjectId,
    string PageName);