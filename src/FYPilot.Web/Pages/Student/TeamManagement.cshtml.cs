using System.Data;
using System.Net;
using System.Security.Claims;
using System.Security.Cryptography;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class TeamManagementModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    INotificationService notificationService,
    ILogger<TeamManagementModel> logger)
    : PageModel
{
    private const int MaximumCollaborators = 20;
    private const int MaximumProjectMembers =
        MaximumCollaborators + 1;

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

    public int CollaboratorCapacity =>
        Math.Max(0, MaximumMembers - 1);

    public int MaximumCollaboratorsAllowed =>
        MaximumCollaborators;

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


        /*
         * Use projectId from the URL when available.
         * Otherwise restore the student's active project.
         */
        if (ProjectId <= 0)
        {
            var activeProjectId =
                await activeProjectService
                    .GetActiveProjectIdAsync(
                        userId.Value,
                        cancellationToken);

            if (!activeProjectId.HasValue)
            {
                TempData["Error"] =
                    "Choose a valid project before "
                    + "opening team management.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            ProjectId = activeProjectId.Value;
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

        await activeProjectService.RememberPageAsync(
            userId.Value,
            ProjectId,
            "/Student/TeamManagement",
            cancellationToken);

        return Page();
    }

    public async Task<IActionResult>
        OnPostUpdateCapacityAsync(
            int projectId,
            int collaboratorCapacity,
            CancellationToken cancellationToken)
    {
        ProjectId = projectId;

        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        if (collaboratorCapacity is < 0 or >
            MaximumCollaborators)
        {
            TempData["Error"] =
                $"Choose between 0 and "
                + $"{MaximumCollaborators} collaborators.";

            return RedirectToTeamPage();
        }

        /*
         * MaximumMembers stores the complete team:
         * one owner plus the selected collaborators.
         */
        var maximumMembers =
            collaboratorCapacity + 1;

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
                    MaximumProjectMembers);

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
                        $"The collaborator capacity was changed "
                        + $"from {Math.Max(0, oldMaximum - 1)} "
                        + $"to {collaboratorCapacity}. "
                        + $"The complete team capacity is now "
                        + $"{maximumMembers} member(s).",
                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await NotifyActiveProjectMembersAsync(
                project.Id,
                userId.Value,
                "Team capacity updated",
                collaboratorCapacity == 0
                    ? $"Project \"{SafeProjectTitle(project.Title)}\" is now configured for the owner only."
                    : $"Project \"{SafeProjectTitle(project.Title)}\" now supports up to {collaboratorCapacity} collaborator(s).",
                "team_capacity_updated",
                $"/Student/TeamManagement?projectId={project.Id}",
                cancellationToken);

            TempData["Success"] =
                collaboratorCapacity == 0
                    ? "The project is now configured "
                      + "for the owner only."
                    : "The project now supports one owner "
                      + $"and up to {collaboratorCapacity} "
                      + "collaborator(s).";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
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
                    MaximumProjectMembers);

            if (project.MaximumMembers == 1)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This project currently has no "
                    + "collaborator positions. Increase "
                    + "the collaborator capacity before "
                    + "sending an invitation.";

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

            /*
             * The owner's name will be used in both
             * the in-app notification and the email.
             */
            var ownerName = await db.Users
                .AsNoTracking()
                .Where(user =>
                    user.Id == userId.Value)
                .Select(user =>
                    user.FullName)
                .FirstOrDefaultAsync(
                    cancellationToken);

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
                    Status =
                        "pending",
                    Source =
                        "student_invite",
                    CreatedAt =
                        now,
                    ExpiresAt =
                        now.AddDays(7)
                };

            db.ProjectInvitations.Add(
                invitation);

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId =
                        ProjectId,
                    UserId =
                        userId.Value,
                    ActionType =
                        "member_invited",
                    Description =
                        $"{SafeName(invitedUser.FullName)} "
                        + "was invited to join "
                        + "the project.",
                    CreatedAtUtc =
                        now
                });

            project.UpdatedAt =
                now;

            /*
             * Save and commit the invitation before
             * trying to send an email.
             *
             * An SMTP or notification error must not
             * remove the invitation from the database.
             */
            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await SendInvitationCommunicationAsync(
                invitedUser,
                ownerName,
                project,
                invitation.Id,
                userId.Value,
                cancellationToken);

            TempData["Success"] =
                $"Invitation sent to "
                + $"{SafeName(invitedUser.FullName)}. "
                + "The student can review it from "
                + "the My Projects page.";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
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
    OnPostRemoveCollaboratorAsync(
        int projectId,
        int memberUserId,
        CancellationToken cancellationToken)
    {
        ProjectId = projectId;

        var ownerUserId = CurrentUserId();

        if (ownerUserId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        if (memberUserId <= 0)
        {
            TempData["Error"] =
                "Choose a valid collaborator.";

            return RedirectToTeamPage();
        }

        if (memberUserId == ownerUserId.Value)
        {
            TempData["Error"] =
                "The project owner cannot remove themselves.";

            return RedirectToTeamPage();
        }

        var access =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                ownerUserId.Value,
                "student",
                cancellationToken);

        if (access == null ||
            !access.IsOwner)
        {
            TempData["Error"] =
                "Only the project owner can remove "
                + "a collaborator.";

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
                    ownerUserId.Value,
                    cancellationToken);

            if (!ownerStillActive)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "You are no longer the active owner "
                    + "of this project.";

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
                    "The selected project could not be found.";

                return RedirectToPage(
                    "/Student/MyProjects");
            }

            var membership = await db.ProjectMembers
                .Include(member => member.User)
                .FirstOrDefaultAsync(
                    member =>
                        member.ProjectId == ProjectId &&
                        member.UserId == memberUserId &&
                        member.Status == "active",
                    cancellationToken);

            if (membership == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This student is not an active "
                    + "collaborator on the project.";

                return RedirectToTeamPage();
            }

            if (Normalize(membership.Role) == "owner")
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The project owner cannot be removed.";

                return RedirectToTeamPage();
            }

            var now = DateTime.UtcNow;

            var collaboratorName =
                SafeName(
                    membership.User?.FullName);

            /*
             * Preserve the membership row for history and
             * possible safe re-invitation later.
             */
            membership.Status =
                "removed";

            membership.LeftAt =
                now;

            /*
             * Do not let the removed user resume this
             * project as their last active project.
             */
            if (membership.User != null &&
                membership.User.LastActiveProjectId ==
                    ProjectId)
            {
                membership.User.LastActiveProjectId =
                    null;

                membership.User.LastProjectPage =
                    "/Student/MyProjects";

                membership.User.LastProjectVisitedAtUtc =
                    now;
            }

            project.UpdatedAt =
                now;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId =
                        ProjectId,

                    UserId =
                        ownerUserId.Value,

                    ActionType =
                        "member_removed",

                    Description =
                        $"{collaboratorName} was removed "
                        + "from the project by the owner.",

                    CreatedAtUtc =
                        now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            /*
             * The membership change is already committed.
             * Notification failure must not undo removal.
             */
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        memberUserId,

                    title:
                        "Project access removed",

                    message:
                        $"You are no longer a collaborator "
                        + $"on \"{SafeProjectTitle(project.Title)}\".",

                    type:
                        "project_membership_removed",

                    url:
                        "/Student/MyProjects",

                    sendEmail:
                        false,

                    projectId:
                        ProjectId,

                    actorUserId:
                        ownerUserId.Value,

                    cancellationToken:
                        cancellationToken);
            }
            catch (Exception notificationException)
            {
                logger.LogWarning(
                    notificationException,
                    "Collaborator {UserId} was removed "
                    + "from project {ProjectId}, but the "
                    + "notification could not be delivered.",
                    memberUserId,
                    ProjectId);
            }

            TempData["Success"] =
                $"{collaboratorName} was removed from "
                + "the project.";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to remove collaborator "
                + "{MemberUserId} from project "
                + "{ProjectId} by owner {OwnerUserId}.",
                memberUserId,
                ProjectId,
                ownerUserId.Value);

            TempData["Error"] =
                "The collaborator could not be removed.";

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
                    .Include(item =>
                        item.Project)
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

            var now =
                DateTime.UtcNow;

            invitation.Status =
                "cancelled";

            invitation.RespondedAt =
                now;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId =
                        ProjectId,
                    UserId =
                        userId.Value,
                    ActionType =
                        "invitation_cancelled",
                    Description =
                        $"The invitation for "
                        + $"{SafeName(
                            invitation.InvitedUser
                                ?.FullName
                            ?? invitation.InvitedEmail)} "
                        + "was cancelled.",
                    CreatedAtUtc =
                        now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            if (invitation.InvitedUserId.HasValue)
            {
                try
                {
                    await notificationService.NotifyUserAsync(
                        recipientUserId:
                            invitation.InvitedUserId.Value,
                        title:
                            "Project invitation cancelled",
                        message:
                            $"The invitation to join \"{SafeProjectTitle(invitation.Project?.Title)}\" was cancelled by the project owner.",
                        type:
                            "invitation_cancelled",
                        url:
                            "/Student/MyProjects",
                        sendEmail:
                            false,
                        projectId:
                            ProjectId,
                        actorUserId:
                            userId.Value,
                        cancellationToken:
                            cancellationToken);
                }
                catch (Exception notificationException)
                {
                    logger.LogWarning(
                        notificationException,
                        "Invitation {InvitationId} was cancelled, but user {UserId} could not be notified.",
                        invitation.Id,
                        invitation.InvitedUserId.Value);
                }
            }

            TempData["Success"] =
                "The invitation was cancelled.";

            return RedirectToTeamPage();
        }
        catch (Exception exception)
        {
            await SafeRollbackAsync(
                transaction,
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

    private async Task SendInvitationCommunicationAsync(
        User invitedUser,
        string? ownerName,
        Project project,
        int invitationId,
        int ownerUserId,
        CancellationToken cancellationToken)
    {
        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var displayOwnerName =
            string.IsNullOrWhiteSpace(ownerName)
                ? "The project owner"
                : ownerName.Trim();

        var displayStudentName =
            SafeName(
                invitedUser.FullName);

        /*
         * During local testing this will be a localhost
         * URL. In production it will use the public
         * website address.
         */
        var myProjectsUrl =
            Url.Page(
                "/Student/MyProjects",
                pageHandler: null,
                values: null,
                protocol: Request.Scheme)
            ?? "/Student/MyProjects";

        var safeStudentName =
            WebUtility.HtmlEncode(
                displayStudentName);

        var safeOwnerName =
            WebUtility.HtmlEncode(
                displayOwnerName);

        var safeProjectTitle =
            WebUtility.HtmlEncode(
                projectTitle);

        var safeMyProjectsUrl =
            WebUtility.HtmlEncode(
                myProjectsUrl);

        var emailSubjectProjectTitle =
            SanitizeEmailSubjectPart(
                projectTitle);

        var emailBody = $"""
<div style="
    margin:0;
    padding:32px 18px;
    background:#f4f7fb;
    font-family:Arial,sans-serif;
">
    <div style="
        max-width:620px;
        margin:auto;
        padding:30px;
        border:1px solid #dfe6ef;
        border-radius:18px;
        background:#ffffff;
    ">
        <div style="
            margin-bottom:22px;
            color:#155eef;
            font-size:14px;
            font-weight:700;
        ">
            FYPilot
        </div>

        <h2 style="
            margin:0 0 18px;
            color:#173463;
            font-size:23px;
        ">
            Project collaboration invitation
        </h2>

        <p style="
            color:#475569;
            font-size:15px;
            line-height:1.7;
        ">
            Hello {safeStudentName},
        </p>

        <p style="
            color:#475569;
            font-size:15px;
            line-height:1.7;
        ">
            <strong>{safeOwnerName}</strong>
            invited you to become a collaborator
            on the project:
        </p>

        <div style="
            margin:22px 0;
            padding:18px;
            border-left:4px solid #155eef;
            border-radius:10px;
            background:#f3f7ff;
            color:#173463;
            font-size:17px;
            font-weight:700;
        ">
            {safeProjectTitle}
        </div>

        <p style="
            color:#475569;
            font-size:15px;
            line-height:1.7;
        ">
            Log in to FYPilot and open your
            <strong>My Projects</strong> page to
            accept or reject this invitation.
        </p>

        <p style="margin:26px 0;">
            <a href="{safeMyProjectsUrl}"
               style="
                   display:inline-block;
                   padding:12px 20px;
                   border-radius:10px;
                   background:#155eef;
                   color:#ffffff;
                   font-size:14px;
                   font-weight:700;
                   text-decoration:none;
               ">
                Review Invitation
            </a>
        </p>

        <p style="
            margin-top:26px;
            color:#94a3b8;
            font-size:12px;
            line-height:1.5;
        ">
            This invitation expires in 7 days.
            You must log in using the invited student
            account to accept or reject it.
        </p>

        <p style="
            margin-top:12px;
            color:#94a3b8;
            font-size:12px;
            line-height:1.5;
        ">
            This is an automated message from FYPilot.
        </p>
    </div>
</div>
""";

        try
        {
            await notificationService.NotifyUserAsync(
                recipientUserId:
                    invitedUser.Id,

                title:
                    "Project collaboration invitation",

                message:
                    $"{displayOwnerName} invited you "
                    + $"to collaborate on "
                    + $"\"{projectTitle}\". Open My "
                    + "Projects to accept or reject.",

                type:
                    "project_invitation",

                url:
                    "/Student/MyProjects",

                sendEmail:
                    true,

                emailSubject:
                    "You are invited to collaborate on "
                    + emailSubjectProjectTitle,

                emailHtmlBody:
                    emailBody,

                projectId:
                    project.Id,

                actorUserId:
                    ownerUserId,

                cancellationToken:
                    cancellationToken);
        }
        catch (Exception notificationException)
        {
            /*
             * The invitation is already committed.
             * Notification failure is logged, but the
             * invitation remains available in My Projects.
             */
            logger.LogError(
                notificationException,
                "Invitation {InvitationId} was created, "
                + "but notification delivery failed for "
                + "user {InvitedUserId}.",
                invitationId,
                invitedUser.Id);
        }
    }

    private async Task NotifyActiveProjectMembersAsync(
        int projectId,
        int actorUserId,
        string title,
        string message,
        string type,
        string url,
        CancellationToken cancellationToken)
    {
        var recipientIds = await db.ProjectMembers
            .AsNoTracking()
            .Where(member =>
                member.ProjectId == projectId &&
                member.Status == "active" &&
                member.UserId != actorUserId)
            .Select(member => member.UserId)
            .Distinct()
            .ToListAsync(cancellationToken);

        foreach (var recipientId in recipientIds)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId: recipientId,
                    title: title,
                    message: message,
                    type: type,
                    url: url,
                    sendEmail: false,
                    projectId: projectId,
                    actorUserId: actorUserId,
                    cancellationToken: cancellationToken);
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Project {ProjectId} changed, but user {UserId} could not be notified.",
                    projectId,
                    recipientId);
            }
        }
    }

    private async Task<bool> LoadPageDataAsync(
        CancellationToken cancellationToken)
    {
        var now =
            DateTime.UtcNow;

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
                MaximumProjectMembers);

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

    private static async Task SafeRollbackAsync(
        Microsoft.EntityFrameworkCore.Storage
            .IDbContextTransaction transaction,
        CancellationToken cancellationToken)
    {
        try
        {
            await transaction.RollbackAsync(
                cancellationToken);
        }
        catch
        {
            /*
             * The transaction may already have been
             * committed or rolled back. Do not replace
             * the original exception with a rollback
             * exception.
             */
        }
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

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(
            title)
                ? "Untitled Project"
                : title.Trim();
    }

    private static string SanitizeEmailSubjectPart(
        string? value)
    {
        return SafeProjectTitle(value)
            .Replace(
                "\r",
                " ",
                StringComparison.Ordinal)
            .Replace(
                "\n",
                " ",
                StringComparison.Ordinal)
            .Trim();
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