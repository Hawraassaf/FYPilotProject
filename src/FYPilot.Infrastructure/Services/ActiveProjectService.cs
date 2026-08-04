using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// Saves and restores the student's current project context.
///
/// The service never trusts the stored project ID without checking
/// whether the student can still view the project.
///
/// An archived membership remains viewable, but read-only.
/// Write authorization remains controlled by ProjectAccessService
/// and ArchivedProjectWriteFilter.
/// </summary>
public sealed class ActiveProjectService(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService)
    : IActiveProjectService
{
    private const string DefaultProjectPage =
        "/Student/Dashboard";

    /*
     * Add each real project-related page to this list when we
     * convert that page to use projectId.
     *
     * Do not add private pages such as Profile,
     * ChangePassword, or SkillAssessment.
     */
    private static readonly HashSet<string>
        AllowedProjectPages =
            new(StringComparer.OrdinalIgnoreCase)
            {
                "/Student/Dashboard",
                "/Student/IdeaGenerator",
                "/Student/IdeaComparison",
                "/Student/MarketDemand",
                "/Student/ProjectDNA",
                "/Student/Roadmap",
                "/Student/DocumentationGenerator",
                "/Student/MentorChat",
                "/Student/DefenseSimulator",
                "/Student/Feedback",
                "/Student/TeamManagement",
                "/Student/ProjectWorkspace"
            };

    public bool IsAllowedProjectPage(
        string? pageName)
    {
        return GetCanonicalPage(pageName) != null;
    }

    public async Task<ActiveProjectDestination?>
        ActivateProjectAsync(
            int userId,
            int projectId,
            string? requestedPage = null,
            CancellationToken cancellationToken = default)
    {
        var access =
            await projectAccessService
                .GetAccessAsync(
                    projectId,
                    userId,
                    "student",
                    cancellationToken);

        /*
         * An archived member may enter the project as a
         * read-only viewing context.
         *
         * CanEdit is intentionally not required here.
         */
        if (access?.CanView != true)
        {
            return null;
        }

        var user =
            await db.Users
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == userId,
                    cancellationToken);

        if (user == null)
        {
            return null;
        }

        var isSameProject =
            user.LastActiveProjectId ==
            projectId;

        var approvedRequestedPage =
            GetCanonicalPage(
                requestedPage);

        var approvedPreviousPage =
            isSameProject
                ? GetCanonicalPage(
                    user.LastProjectPage)
                : null;

        var destinationPage =
            approvedRequestedPage
            ?? approvedPreviousPage
            ?? DefaultProjectPage;

        user.LastActiveProjectId =
            projectId;

        user.LastProjectPage =
            destinationPage;

        user.LastProjectVisitedAtUtc =
            DateTime.UtcNow;

        await db.SaveChangesAsync(
            cancellationToken);

        return new ActiveProjectDestination(
            ProjectId:
                projectId,
            PageName:
                destinationPage);
    }

    public async Task<bool> RememberPageAsync(
        int userId,
        int projectId,
        string pageName,
        CancellationToken cancellationToken = default)
    {
        var approvedPage =
            GetCanonicalPage(
                pageName);

        if (approvedPage == null)
        {
            return false;
        }

        var access =
            await projectAccessService
                .GetAccessAsync(
                    projectId,
                    userId,
                    "student",
                    cancellationToken);

        /*
         * Remember navigation for both editable projects
         * and archived read-only projects.
         */
        if (access?.CanView != true)
        {
            return false;
        }

        var user =
            await db.Users
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == userId,
                    cancellationToken);

        if (user == null)
        {
            return false;
        }

        user.LastActiveProjectId =
            projectId;

        user.LastProjectPage =
            approvedPage;

        user.LastProjectVisitedAtUtc =
            DateTime.UtcNow;

        await db.SaveChangesAsync(
            cancellationToken);

        return true;
    }

    public async Task<int?> GetActiveProjectIdAsync(
        int userId,
        CancellationToken cancellationToken = default)
    {
        var destination =
            await GetResumeDestinationAsync(
                userId,
                cancellationToken);

        return destination?.ProjectId;
    }

    public async Task<ActiveProjectDestination?>
        GetResumeDestinationAsync(
            int userId,
            CancellationToken cancellationToken = default)
    {
        var user =
            await db.Users
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == userId,
                    cancellationToken);

        if (user == null ||
            !user.LastActiveProjectId.HasValue)
        {
            return null;
        }

        var projectId =
            user.LastActiveProjectId.Value;

        /*
         * The stored project ID is never trusted.
         * Verify that the student can still view the project.
         *
         * Archived memberships remain valid read-only contexts.
         * Removed memberships and deleted projects return no access.
         */
        var access =
            await projectAccessService
                .GetAccessAsync(
                    projectId,
                    userId,
                    "student",
                    cancellationToken);

        if (access?.CanView != true)
        {
            ClearUserProjectContext(
                user);

            await db.SaveChangesAsync(
                cancellationToken);

            return null;
        }

        var destinationPage =
            GetCanonicalPage(
                user.LastProjectPage)
            ?? DefaultProjectPage;

        /*
         * Correct old or invalid stored page values.
         */
        if (!string.Equals(
                user.LastProjectPage,
                destinationPage,
                StringComparison.Ordinal))
        {
            user.LastProjectPage =
                destinationPage;

            user.LastProjectVisitedAtUtc =
                DateTime.UtcNow;

            await db.SaveChangesAsync(
                cancellationToken);
        }

        return new ActiveProjectDestination(
            ProjectId:
                projectId,
            PageName:
                destinationPage);
    }

    public async Task ClearActiveProjectAsync(
        int userId,
        CancellationToken cancellationToken = default)
    {
        var user =
            await db.Users
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == userId,
                    cancellationToken);

        if (user == null)
        {
            return;
        }

        ClearUserProjectContext(
            user);

        await db.SaveChangesAsync(
            cancellationToken);
    }

    private static void ClearUserProjectContext(
        FYPilot.Domain.Entities.User user)
    {
        user.LastActiveProjectId =
            null;

        user.LastProjectPage =
            null;

        user.LastProjectVisitedAtUtc =
            null;
    }

    private static string? GetCanonicalPage(
        string? pageName)
    {
        if (string.IsNullOrWhiteSpace(
                pageName))
        {
            return null;
        }

        var normalizedPage =
            pageName.Trim();

        return AllowedProjectPages
            .FirstOrDefault(page =>
                string.Equals(
                    page,
                    normalizedPage,
                    StringComparison.OrdinalIgnoreCase));
    }
}