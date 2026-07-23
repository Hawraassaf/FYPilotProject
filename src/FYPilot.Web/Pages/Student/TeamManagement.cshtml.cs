using System.Data;
using System.Security.Claims;
using System.Security.Cryptography;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class TeamManagementModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    ILogger<TeamManagementModel> logger)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public ProjectAccessResult? ProjectAccess
    {
        get;
        private set;
    }

    public string ProjectTitle { get; private set; } =
        "Untitled Project";

    public int MaximumMembers { get; private set; } = 1;

    public List<MemberItem> ActiveMembers
    {
        get;
        private set;
    } = [];

    public List<InvitationItem> PendingInvitations
    {
        get;
        private set;
    } = [];

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    public bool IsOwner =>
        ProjectAccess?.IsOwner == true;

    public int ActiveMembersCount =>
        ActiveMembers.Count;

    public int PendingInvitationsCount =>
        PendingInvitations.Count;

    /*
     * Pending invitations reserve a team position.
     *
     * Example:
     * Maximum = 3
     * Active = 2
     * Pending = 1
     * Available = 0
     */
    public int ReservedPositions =>
        ActiveMembersCount +
        PendingInvitationsCount;

    public int AvailablePositions =>
        Math.Max(
            0,
            MaximumMembers - ReservedPositions);

    public bool IsIndividualProject =>
        MaximumMembers == 1;

    public bool CanInvite =>
        IsOwner &&
        !IsIndividualProject &&
        AvailablePositions > 0;

    public sealed record MemberItem(
        int UserId,
        string FullName,
        string Email,
        string Initials,
        string Role,
        DateTime JoinedAt);

    public sealed record InvitationItem(
        int Id,
        int? InvitedUserId,
        string InvitedEmail,
        string InvitedUserName,
        DateTime CreatedAt,
        DateTime ExpiresAt);

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        SuccessMessage =
            TempData["Success"] as string;

        ErrorMessage =
            TempData["Error"] as string;

        if (ProjectId <= 0)
        {
            TempData["Error"] =
                "Choose a valid project before "
                + "opening team management.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        ProjectAccess =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (ProjectAccess == null)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        var loaded =
            await LoadPageDataAsync(
                cancellationToken);

        if (!loaded)
        {
            TempData["Error"] =
                "The selected project could not be found.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        return Page();
    }

    public async Task<IActionResult>
        OnPostUpdateCapacityAsync(
            int projectId,
            int maximumMembers,
            CancellationToken cancellationToken)
    {
        ProjectId = projectId;

        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        if (maximumMembers is < 1 or > 3)
        {
            TempData["Error"] =
                "Team size must be 1, 2, or 3.";

            return RedirectToTeamPage();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access == null ||
            !access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can change "
                + "the team size.";

            return RedirectToTeamPage();
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var ownerStillActive =
                await IsActiveOwnerAsync(
                    userId.Value,
                    cancellationToken);

            if (!ownerStillActive)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "You are no longer the active "
                    + "owner of this project.";

                return RedirectToTeamPage();
            }

            var project = await db.Projects
                .FirstOrDefaultAsync(
                    item => item.Id == ProjectId,
                    cancellationToken);

            if (project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The selected project could "
                    + "not be found.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            var now = DateTime.UtcNow;

            var activeMembersCount =
                await db.ProjectMembers
                    .CountAsync(
                        member =>
                            member.ProjectId == ProjectId &&
                            member.Status == "active",
                        cancellationToken);

            var pendingInvitationsCount =
                await db.ProjectInvitations
                    .CountAsync(
                        invitation =>
                            invitation.ProjectId == ProjectId &&
                            invitation.Status == "pending" &&
                            invitation.ExpiresAt > now,
                        cancellationToken);

            /*
             * Pending invitations reserve positions.
             * The owner must cancel them before reducing
             * the team capacity below the reserved total.
             */
            var requiredPositions =
                activeMembersCount +
                pendingInvitationsCount;

            if (maximumMembers < requiredPositions)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    $"The project currently requires "
                    + $"{requiredPositions} position(s): "
                    + $"{activeMembersCount} active member(s) "
                    + $"and {pendingInvitationsCount} pending "
                    + "invitation(s).";

                return RedirectToTeamPage();
            }

            var oldMaximum =
                Math.Clamp(
                    project.MaximumMembers,
                    1,
                    3);

            if (oldMaximum == maximumMembers)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Success"] =
                    "The team size is already set "
                    + $"to {maximumMembers}.";

                return RedirectToTeamPage();
            }

            project.MaximumMembers =
                maximumMembers;

            project.UpdatedAt = now;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId.Value,
                    ActionType =
                        "team_capacity_updated",
                    Description =
                        $"The project team size was changed "
                        + $"from {oldMaximum} to "
                        + $"{maximumMembers}.",
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            TempData["Success"] =
                maximumMembers switch
                {
                    1 =>
                        "The project is now configured "
                        + "as an individual project.",

                    2 =>
                        "The project now supports the owner "
                        + "and one collaborator.",

                    _ =>
                        "The project now supports the owner "
                        + "and up to two collaborators."
                };

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to update team capacity for "
                + "project {ProjectId}, user {UserId}.",
                ProjectId,
                userId.Value);

            TempData["Error"] =
                "The team size could not be updated.";

            return RedirectToTeamPage();
        }
    }

    public async Task<IActionResult>
        OnPostInviteAsync(
            int projectId,
            string? invitedEmail,
            CancellationToken cancellationToken)
    {
        ProjectId = projectId;

        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        var normalizedEmail =
            invitedEmail?
                .Trim()
                .ToLowerInvariant()
            ?? string.Empty;

        if (string.IsNullOrWhiteSpace(
                normalizedEmail))
        {
            TempData["Error"] =
                "Enter the student email address.";

            return RedirectToTeamPage();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access == null ||
            !access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can "
                + "invite collaborators.";

            return RedirectToTeamPage();
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var ownerStillActive =
                await IsActiveOwnerAsync(
                    userId.Value,
                    cancellationToken);

            if (!ownerStillActive)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "You are no longer the active "
                    + "owner of this project.";

                return RedirectToTeamPage();
            }

            var project = await db.Projects
                .FirstOrDefaultAsync(
                    item => item.Id == ProjectId,
                    cancellationToken);

            if (project == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The selected project could "
                    + "not be found.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            project.MaximumMembers =
                Math.Clamp(
                    project.MaximumMembers,
                    1,
                    3);

            if (project.MaximumMembers == 1)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This is an individual project. "
                    + "Change the team size to 2 or 3 "
                    + "before inviting a collaborator.";

                return RedirectToTeamPage();
            }

            var invitedUser = await db.Users
                .FirstOrDefaultAsync(
                    user =>
                        user.Email.ToLower() ==
                            normalizedEmail &&
                        user.Role.ToLower() ==
                            "student",
                    cancellationToken);

            if (invitedUser == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "No registered student account "
                    + "was found with that email.";

                return RedirectToTeamPage();
            }

            if (invitedUser.Id == userId.Value)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "You cannot invite yourself "
                    + "to your own project.";

                return RedirectToTeamPage();
            }

            var alreadyActive =
                await db.ProjectMembers.AnyAsync(
                    member =>
                        member.ProjectId == ProjectId &&
                        member.UserId == invitedUser.Id &&
                        member.Status == "active",
                    cancellationToken);

            if (alreadyActive)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This student is already an active "
                    + "member of the project.";

                return RedirectToTeamPage();
            }

            var now = DateTime.UtcNow;

            /*
             * Expired pending invitations no longer
             * reserve a team position.
             */
            var expiredInvitations =
                await db.ProjectInvitations
                    .Where(
                        invitation =>
                            invitation.ProjectId ==
                                ProjectId &&
                            invitation.Status ==
                                "pending" &&
                            invitation.ExpiresAt <= now)
                    .ToListAsync(cancellationToken);

            foreach (var expiredInvitation
                     in expiredInvitations)
            {
                expiredInvitation.Status =
                    "expired";

                expiredInvitation.RespondedAt =
                    now;
            }

            var duplicatePending =
                await db.ProjectInvitations
                    .AnyAsync(
                        invitation =>
                            invitation.ProjectId ==
                                ProjectId &&
                            invitation.Status ==
                                "pending" &&
                            invitation.ExpiresAt > now &&
                            (
                                invitation.InvitedUserId ==
                                    invitedUser.Id ||
                                invitation.InvitedEmail
                                    .ToLower() ==
                                    normalizedEmail
                            ),
                        cancellationToken);

            if (duplicatePending)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This student already has a pending "
                    + "invitation for the project.";

                return RedirectToTeamPage();
            }

            var activeMembersCount =
                await db.ProjectMembers
                    .CountAsync(
                        member =>
                            member.ProjectId == ProjectId &&
                            member.Status == "active",
                        cancellationToken);

            var pendingInvitationsCount =
                await db.ProjectInvitations
                    .CountAsync(
                        invitation =>
                            invitation.ProjectId ==
                                ProjectId &&
                            invitation.Status ==
                                "pending" &&
                            invitation.ExpiresAt > now,
                        cancellationToken);

            var reservedPositions =
                activeMembersCount +
                pendingInvitationsCount;

            if (reservedPositions >=
                project.MaximumMembers)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The project has no available "
                    + "collaborator positions.";

                return RedirectToTeamPage();
            }

            var invitation =
                new ProjectInvitation
                {
                    ProjectId = ProjectId,
                    InvitedByUserId =
                        userId.Value,
                    InvitedUserId =
                        invitedUser.Id,
                    InvitedEmail =
                        normalizedEmail,
                    TokenHash =
                        CreateTokenHash(),
                    Status = "pending",
                    Source =
                        "student_invite",
                    CreatedAt = now,
                    ExpiresAt =
                        now.AddDays(7)
                };

            db.ProjectInvitations.Add(
                invitation);

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = ProjectId,
                    UserId = userId.Value,
                    ActionType =
                        "member_invited",
                    Description =
                        $"{SafeName(invitedUser.FullName)} "
                        + "was invited to join "
                        + "the project.",
                    CreatedAtUtc = now
                });

            project.UpdatedAt = now;

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            TempData["Success"] =
                $"Invitation sent to "
                + $"{SafeName(invitedUser.FullName)}.";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to invite a collaborator "
                + "to project {ProjectId} "
                + "by user {UserId}.",
                ProjectId,
                userId.Value);

            TempData["Error"] =
                "The invitation could not be sent.";

            return RedirectToTeamPage();
        }
    }

    public async Task<IActionResult>
        OnPostCancelInvitationAsync(
            int projectId,
            int invitationId,
            CancellationToken cancellationToken)
    {
        ProjectId = projectId;

        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access == null ||
            !access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can cancel "
                + "an invitation.";

            return RedirectToTeamPage();
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var invitation =
                await db.ProjectInvitations
                    .Include(item =>
                        item.InvitedUser)
                    .FirstOrDefaultAsync(
                        item =>
                            item.Id == invitationId &&
                            item.ProjectId ==
                                ProjectId &&
                            item.Status ==
                                "pending",
                        cancellationToken);

            if (invitation == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The pending invitation could "
                    + "not be found.";

                return RedirectToTeamPage();
            }

            var now = DateTime.UtcNow;

            invitation.Status =
                "cancelled";

            invitation.RespondedAt =
                now;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = ProjectId,
                    UserId = userId.Value,
                    ActionType =
                        "invitation_cancelled",
                    Description =
                        $"The invitation for "
                        + $"{SafeName(
                            invitation.InvitedUser
                                ?.FullName
                            ?? invitation.InvitedEmail)} "
                        + "was cancelled.",
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            TempData["Success"] =
                "The invitation was cancelled.";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to cancel invitation "
                + "{InvitationId} for project "
                + "{ProjectId}.",
                invitationId,
                ProjectId);

            TempData["Error"] =
                "The invitation could not be cancelled.";

            return RedirectToTeamPage();
        }
    }

    private async Task<bool> LoadPageDataAsync(
        CancellationToken cancellationToken)
    {
        var now = DateTime.UtcNow;

        var project = await db.Projects
            .AsNoTracking()
            .Include(item => item.Members
                .Where(member =>
                    member.Status == "active"))
            .ThenInclude(member =>
                member.User)
            .Include(item => item.Invitations
                .Where(invitation =>
                    invitation.Status == "pending" &&
                    invitation.ExpiresAt > now))
            .ThenInclude(invitation =>
                invitation.InvitedUser)
            .AsSplitQuery()
            .FirstOrDefaultAsync(
                item => item.Id == ProjectId,
                cancellationToken);

        if (project == null)
        {
            return false;
        }

        ProjectTitle =
            string.IsNullOrWhiteSpace(
                project.Title)
                ? "Untitled Project"
                : project.Title.Trim();

        MaximumMembers =
            Math.Clamp(
                project.MaximumMembers,
                1,
                3);

        ActiveMembers = project.Members
            .Where(member =>
                member.Status == "active" &&
                member.User != null)
            .OrderBy(member =>
                Normalize(member.Role) ==
                    "owner"
                    ? 0
                    : 1)
            .ThenBy(member =>
                member.JoinedAt)
            .Select(member =>
                new MemberItem(
                    member.UserId,
                    member.User!.FullName,
                    member.User.Email,
                    Initials(
                        member.User.FullName),
                    ToDisplayText(
                        member.Role),
                    member.JoinedAt))
            .ToList();

        /*
         * Pending invitation emails are visible only
         * to the owner, not to collaborators.
         */
        PendingInvitations =
            IsOwner
                ? project.Invitations
                    .Where(invitation =>
                        invitation.Status ==
                            "pending" &&
                        invitation.ExpiresAt > now)
                    .OrderByDescending(
                        invitation =>
                            invitation.CreatedAt)
                    .Select(invitation =>
                        new InvitationItem(
                            invitation.Id,
                            invitation.InvitedUserId,
                            invitation.InvitedEmail,
                            invitation.InvitedUser
                                ?.FullName
                            ?? invitation.InvitedEmail,
                            invitation.CreatedAt,
                            invitation.ExpiresAt))
                    .ToList()
                : [];

        return true;
    }

    private async Task<bool> IsActiveOwnerAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        return await db.ProjectMembers
            .AnyAsync(
                member =>
                    member.ProjectId == ProjectId &&
                    member.UserId == userId &&
                    member.Status == "active" &&
                    member.Role == "owner",
                cancellationToken);
    }

    private RedirectToPageResult
        RedirectToTeamPage()
    {
        return RedirectToPage(
            "/Student/TeamManagement",
            new
            {
                projectId = ProjectId
            });
    }

    private int? CurrentUserId()
    {
        var value = User.FindFirst(
            ClaimTypes.NameIdentifier)?.Value;

        return int.TryParse(
            value,
            out var userId)
            ? userId
            : null;
    }

    private static string CreateTokenHash()
    {
        var randomBytes =
            RandomNumberGenerator.GetBytes(32);

        return Convert.ToHexString(
                randomBytes)
            .ToLowerInvariant();
    }

    private static string SafeName(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "The student"
                : value.Trim();
    }

    private static string Normalize(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? string.Empty
                : value.Trim()
                    .ToLowerInvariant();
    }

    private static string ToDisplayText(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(
                value))
        {
            return "Member";
        }

        var clean = value
            .Trim()
            .Replace("_", " ")
            .Replace("-", " ");

        return string.Join(
            " ",
            clean.Split(
                    ' ',
                    StringSplitOptions
                        .RemoveEmptyEntries)
                .Select(word =>
                    char.ToUpperInvariant(
                        word[0])
                    + word[1..]
                        .ToLowerInvariant()));
    }

    private static string Initials(
        string? fullName)
    {
        if (string.IsNullOrWhiteSpace(
                fullName))
        {
            return "ST";
        }

        var parts = fullName
            .Split(
                ' ',
                StringSplitOptions
                    .RemoveEmptyEntries)
            .Take(2)
            .ToList();

        return parts.Count == 0
            ? "ST"
            : string.Join(
                    "",
                    parts.Select(
                        part => part[0]))
                .ToUpperInvariant();
    }
}