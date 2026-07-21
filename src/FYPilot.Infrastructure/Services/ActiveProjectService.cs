using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// Saves and restores the student's active project context.
///
/// The service never trusts the stored project ID without checking
/// the student's current active membership.
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
            "/Student/IdeaGenerator"
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
        var access = await projectAccessService
            .GetAccessAsync(
                projectId,
                userId,
                "student",
                cancellationToken);

        if (access == null)
        {
            return null;
        }

        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Id == userId,
                cancellationToken);

        if (user == null)
        {
            return null;
        }

        var isSameProject =
            user.LastActiveProjectId == projectId;

        var approvedRequestedPage =
            GetCanonicalPage(requestedPage);

        var approvedPreviousPage =
            isSameProject
                ? GetCanonicalPage(user.LastProjectPage)
                : null;

        var destinationPage =
            approvedRequestedPage
            ?? approvedPreviousPage
            ?? DefaultProjectPage;

        user.LastActiveProjectId = projectId;
        user.LastProjectPage = destinationPage;
        user.LastProjectVisitedAtUtc = DateTime.UtcNow;

        await db.SaveChangesAsync(cancellationToken);

        return new ActiveProjectDestination(
            ProjectId: projectId,
            PageName: destinationPage);
    }

    public async Task<bool> RememberPageAsync(
        int userId,
        int projectId,
        string pageName,
        CancellationToken cancellationToken = default)
    {
        var approvedPage =
            GetCanonicalPage(pageName);

        if (approvedPage == null)
        {
            return false;
        }

        var access = await projectAccessService
            .GetAccessAsync(
                projectId,
                userId,
                "student",
                cancellationToken);

        if (access == null)
        {
            return false;
        }

        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Id == userId,
                cancellationToken);

        if (user == null)
        {
            return false;
        }

        user.LastActiveProjectId = projectId;
        user.LastProjectPage = approvedPage;
        user.LastProjectVisitedAtUtc = DateTime.UtcNow;

        await db.SaveChangesAsync(cancellationToken);

        return true;
    }

    public async Task<ActiveProjectDestination?>
        GetResumeDestinationAsync(
            int userId,
            CancellationToken cancellationToken = default)
    {
        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Id == userId,
                cancellationToken);

        if (user == null ||
            !user.LastActiveProjectId.HasValue)
        {
            return null;
        }

        var projectId =
            user.LastActiveProjectId.Value;

        /*
         * The stored project ID is not trusted.
         * Membership is checked again on every resume.
         */
        var access = await projectAccessService
            .GetAccessAsync(
                projectId,
                userId,
                "student",
                cancellationToken);

        if (access == null)
        {
            ClearUserProjectContext(user);

            await db.SaveChangesAsync(
                cancellationToken);

            return null;
        }

        var destinationPage =
            GetCanonicalPage(user.LastProjectPage)
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
            ProjectId: projectId,
            PageName: destinationPage);
    }

    public async Task ClearActiveProjectAsync(
        int userId,
        CancellationToken cancellationToken = default)
    {
        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Id == userId,
                cancellationToken);

        if (user == null)
        {
            return;
        }

        ClearUserProjectContext(user);

        await db.SaveChangesAsync(
            cancellationToken);
    }

    private static void ClearUserProjectContext(
      FYPilot.Domain.Entities.User user)
    {
        user.LastActiveProjectId = null;
        user.LastProjectPage = null;
        user.LastProjectVisitedAtUtc = null;
    }

    private static string? GetCanonicalPage(
        string? pageName)
    {
        if (string.IsNullOrWhiteSpace(pageName))
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