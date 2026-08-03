using System.Data;
using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.Projects;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class MyProjectsModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    IProjectLifecycleService projectLifecycleService,
    INotificationService notificationService,
    ILogger<MyProjectsModel> logger) : PageModel
{
    private const int MaximumCollaborators = 20;
    private const int MaximumProjectMembers =
        MaximumCollaborators + 1;

    public List<ProjectCardViewModel> Projects { get; private set; } = [];
    public List<PendingInvitationViewModel> PendingInvitations { get; private set; } = [];

    public int PendingInvitationsCount =>
        PendingInvitations.Count;

    public int TotalProjects => Projects.Count;

    public int OwnedProjects =>
        Projects.Count(project =>
            string.Equals(
                project.Role,
                "owner",
                StringComparison.OrdinalIgnoreCase));

    public int CollaboratingProjects =>
        Projects.Count(project =>
            string.Equals(
                project.Role,
                "collaborator",
                StringComparison.OrdinalIgnoreCase));

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }



        var currentUser = await db.Users
            .AsNoTracking()
            .Where(user => user.Id == userId.Value)
           .Select(user => new
           {
               user.LastActiveProjectId,
               user.Email
           })
            .FirstOrDefaultAsync(cancellationToken);

        if (currentUser == null)
        {
            return RedirectToPage("/Account/Login");
        }

        var now = DateTime.UtcNow;

        var normalizedEmail = currentUser.Email
            .Trim()
            .ToLowerInvariant();

        var pendingInvitationEntities =
            await db.ProjectInvitations
                .AsNoTracking()
              .Where(invitation =>
                invitation.Status == "pending" &&
                invitation.ExpiresAt > now &&
                invitation.Project != null &&
                !invitation.Project.IsDeleted &&
                    (
                        invitation.InvitedUserId ==
                            userId.Value ||
                        (
                            invitation.InvitedUserId == null &&
                            invitation.InvitedEmail.ToLower() ==
                                normalizedEmail
                        )
                    ))
                .Include(invitation =>
                    invitation.Project)
                .ThenInclude(project =>
                    project!.Members)
                .Include(invitation =>
                    invitation.InvitedByUser)
                .AsSplitQuery()
                .OrderByDescending(invitation =>
                    invitation.CreatedAt)
                .ToListAsync(cancellationToken);

        PendingInvitations = pendingInvitationEntities
            .Where(invitation =>
                invitation.Project != null)
            .Select(invitation =>
                new PendingInvitationViewModel(
                    Id: invitation.Id,

                    ProjectId:
                        invitation.ProjectId,

                    ProjectTitle:
                        string.IsNullOrWhiteSpace(
                            invitation.Project!.Title)
                            ? "Untitled Project"
                            : invitation.Project.Title.Trim(),

                    InvitedByName:
                        SafeName(
                            invitation.InvitedByUser
                                ?.FullName),

                    InvitedByEmail:
                        invitation.InvitedByUser
                            ?.Email ?? "",

                    ActiveMembersCount:
                        invitation.Project.Members.Count(
                            member =>
                                member.Status == "active"),

                    MaximumMembers:
                        Math.Clamp(
                            invitation.Project.MaximumMembers,
                            1,
                            MaximumProjectMembers),

                    CreatedAt:
                        invitation.CreatedAt,

                    ExpiresAt:
                        invitation.ExpiresAt))
            .ToList();
        var projects = await db.Projects
     .AsNoTracking()
     .Where(project =>
         !project.IsDeleted &&
         project.Members.Any(member =>
             member.UserId == userId.Value &&
             member.Status == "active" &&
             !member.IsArchived))
             .Include(project => project.ProjectIdea)
            .Include(project => project.Members
                .Where(member =>
                    member.Status == "active"))
            .ThenInclude(member => member.User)
            .AsSplitQuery()
            .OrderByDescending(project => project.UpdatedAt)
            .ThenByDescending(project => project.Id)
            .ToListAsync(cancellationToken);

        var projectIds = projects
            .Select(project => project.Id)
            .ToList();

        var activities = projectIds.Count == 0
            ? []
            : await db.ProjectActivities
                .AsNoTracking()
                .Where(activity =>
                    projectIds.Contains(activity.ProjectId))
                .OrderByDescending(activity =>
                    activity.CreatedAtUtc)
                .Select(activity =>
                    new LatestActivityQueryResult(
                        activity.ProjectId,
                        activity.Description,
                        activity.CreatedAtUtc))
                .ToListAsync(cancellationToken);

        var latestActivityByProject = activities
            .GroupBy(activity => activity.ProjectId)
            .ToDictionary(
                group => group.Key,
                group => group.First());

        Projects = projects
            .Select(project =>
            {
                var currentMembership = project.Members
                    .First(member =>
                        member.UserId == userId.Value &&
                        member.Status == "active");

                var members = project.Members
                    .Where(member =>
                        member.Status == "active" &&
                        member.User != null)
                    .OrderBy(member =>
                        Normalize(member.Role) == "owner"
                            ? 0
                            : 1)
                    .ThenBy(member => member.JoinedAt)
                    .Select(member =>
                        new MemberPreviewViewModel(
                            UserId: member.UserId,
                            FullName: member.User!.FullName,
                            Initials: Initials(
                                member.User.FullName),
                            Role: Normalize(member.Role)))
                    .ToList();

                var maximumMembers =
                    Math.Clamp(
                        project.MaximumMembers,
                        1,
                        MaximumProjectMembers);

                var teamFillPercentage =
                    Math.Clamp(
                        (int)Math.Round(
                            members.Count * 100.0 /
                            maximumMembers),
                        0,
                        100);

                var hasSelectedIdea =
                    project.ProjectIdeaId.HasValue &&
                    project.ProjectIdea != null;

                var domain = hasSelectedIdea &&
                             !string.IsNullOrWhiteSpace(
                                 project.ProjectIdea!.Domain)
                    ? project.ProjectIdea.Domain.Trim()
                    : "Idea not selected";

                var description =
                    !string.IsNullOrWhiteSpace(project.Description)
                        ? project.Description
                        : hasSelectedIdea
                            ? project.ProjectIdea!.WhyUseful
                            : "Create or select an idea to begin "
                              + "building this project.";

                var icon = ResolveProjectIcon(domain);

                latestActivityByProject.TryGetValue(
                    project.Id,
                    out var latestActivity);

                var role = Normalize(currentMembership.Role);
                var status = NormalizeStatus(project.Status);

                return new ProjectCardViewModel(
                    Id: project.Id,
                    ProjectIdeaId: project.ProjectIdeaId,
                    Title: string.IsNullOrWhiteSpace(project.Title)
                        ? "Untitled Project"
                        : project.Title.Trim(),
                    SelectedIdeaTitle: hasSelectedIdea
                        ? project.ProjectIdea!.Title
                        : "No idea selected",
                    HasSelectedIdea: hasSelectedIdea,
                    Domain: domain,
                    Description: Shorten(description, 180),
                    Role: role,
                    RoleLabel: ToDisplayText(role),
                    Status: status,
                    StatusLabel: ToDisplayText(status),
                    StatusCssClass:
                        ResolveStatusClass(status),
                    ProgressPercentage: Math.Clamp(
                        project.ProgressPercentage,
                        0,
                        100),
                    TeamFillPercentage: teamFillPercentage,
                    ActiveMembersCount: members.Count,
                    MaximumMembers: maximumMembers,
                    Members: members,
                    CreatedAt: project.CreatedAt,
                    UpdatedAt: project.UpdatedAt,
                    CreatedSortValue:
                        ToUnixMilliseconds(project.CreatedAt),
                    IconCssClass: icon.CssClass,
                    IconClass: icon.IconClass,
                    TeamActionText:
                        ResolveTeamActionText(
                            role,
                            members.Count,
                            maximumMembers),
                    IsActiveProject:
                        currentUser.LastActiveProjectId ==
                        project.Id,
                    LastActivityDescription:
                        latestActivity?.Description,
                    LastActivityAtUtc:
                        latestActivity?.CreatedAtUtc);
            })
            .ToList();

        return Page();
    }

    public async Task<IActionResult>
        OnPostCreateProjectAsync(
            CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var user = await db.Users
                .FirstOrDefaultAsync(
                    item => item.Id == userId.Value,
                    cancellationToken);

            if (user == null)
            {
                TempData["Error"] =
                    "Your account could not be found.";

                await transaction.RollbackAsync(
                    cancellationToken);

                return RedirectToPage();
            }
            /*
             * Every new project starts with one owner.
             * The owner chooses the collaborator capacity
             * later from Team Management.
             */
            const int projectTeamSize = 1;
            var now = DateTime.UtcNow;


            var project = new Project
            {
                StudentId = user.Id,
                SupervisorId = null,
                ProjectIdeaId = null,
                Title = "Untitled Project",
                Description = "",
                Technologies = "",
                Status = "draft",
                ProgressPercentage = 0,
                MaximumMembers = projectTeamSize,
                CreatedAt = now,
                UpdatedAt = now
            };

            project.Members.Add(
                new ProjectMember
                {
                    UserId = user.Id,
                    Role = "owner",
                    Status = "active",
                    JoinedAt = now
                });

            project.Activities.Add(
                new ProjectActivity
                {
                    UserId = user.Id,
                    ActionType = "project_created",
                    Description =
                        $"{SafeName(user.FullName)} created "
                        + "the project.",
                    CreatedAtUtc = now
                });

            db.Projects.Add(project);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            var destination =
                await activeProjectService
                   .ActivateProjectAsync(
                     user.Id,
                     project.Id,
                     "/Student/Dashboard",
                        cancellationToken);

            if (destination == null)
            {
                TempData["Success"] =
                    "The project was created successfully.";

                return RedirectToPage();
            }

            TempData["Success"] =
    "Your new project was created successfully. "
    + "Choose an idea from the project Dashboard when you are ready.";

            return RedirectToPage(
                destination.PageName,
                new
                {
                    projectId = destination.ProjectId
                });
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to create a project for user {UserId}.",
                userId.Value);

            TempData["Error"] =
                "The project could not be created. "
                + "Please try again.";

            return RedirectToPage();
        }
    }

    public async Task<IActionResult>
        OnPostOpenProjectAsync(
            int projectId,
            string? requestedPage,
            CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }

        /*
         * Validate that the logged-in student still has
         * active access to the selected project before
         * making it the current project workspace.
         */
        var access =
            await projectAccessService.GetAccessAsync(
                projectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access?.CanEdit != true)
        {
            TempData["Error"] =
                access?.IsArchived == true
                    ? "Restore this project before continuing your work."
                    : "You do not have access to that project.";

            return RedirectToPage();
        }

        /*
         * The top navigation project switcher sends the
         * current project page as requestedPage.
         *
         * The Continue Project button on My Projects does
         * not send requestedPage, so it continues opening
         * the selected project's Dashboard.
         *
         * Only whitelisted project pages are accepted.
         */
        var safeRequestedPage =
            activeProjectService.IsAllowedProjectPage(
                requestedPage)
                ? requestedPage
                : "/Student/Dashboard";

        var destination =
            await activeProjectService
                .ActivateProjectAsync(
                    userId.Value,
                    projectId,
                    requestedPage:
                        safeRequestedPage,
                    cancellationToken);

        if (destination == null)
        {
            TempData["Error"] =
                "The project could not be opened. "
                + "Please try again.";

            return RedirectToPage();
        }

        return RedirectToPage(
            destination.PageName,
            new
            {
                projectId =
                    destination.ProjectId
            });
    }
    public async Task<IActionResult>
    OnPostArchiveAsync(
        int projectId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        var result =
            await projectLifecycleService.ArchiveAsync(
                projectId,
                userId.Value,
                cancellationToken);

        TempData[
            result.Succeeded
                ? "Success"
                : "Error"] =
            result.Message;

        return RedirectToPage();
    }
    public async Task<IActionResult>
    OnGetRemovalPreviewAsync(
        int projectId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return new JsonResult(
                new
                {
                    succeeded = false,
                    message =
                        "Your session has expired."
                })
            {
                StatusCode = StatusCodes.Status401Unauthorized
            };
        }

        var preview =
            await projectLifecycleService
                .GetRemovalPreviewAsync(
                    projectId,
                    userId.Value,
                    cancellationToken);

        return new JsonResult(preview);
    }
    public async Task<IActionResult>
    OnPostRemoveAsync(
        int projectId,
        int? newOwnerUserId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        var result =
            await projectLifecycleService.RemoveAsync(
                projectId,
                userId.Value,
                newOwnerUserId,
                cancellationToken);

        TempData[
            result.Succeeded
                ? "Success"
                : "Error"] =
            result.Message;

        return RedirectToPage();
    }
    public async Task<IActionResult>
        OnPostRenameProjectAsync(
            int projectId,
            string? title,
            CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }

        var cleanTitle = title?.Trim() ?? "";

        if (cleanTitle.Length < 3 ||
            cleanTitle.Length > 120)
        {
            TempData["Error"] =
                "The project name must contain "
                + "between 3 and 120 characters.";

            return RedirectToPage();
        }

        var access = await projectAccessService
            .GetAccessAsync(
                projectId,
                userId.Value,
                "student",
                cancellationToken);

        if (access?.CanEdit != true)
        {
            TempData["Error"] =
                access?.IsArchived == true
                    ? "Restore this project before renaming it."
                    : "You do not have permission "
                      + "to rename that project.";

            return RedirectToPage();
        }
        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var project = await db.Projects
    .FirstOrDefaultAsync(
        item =>
            item.Id == projectId &&
            !item.IsDeleted,
        cancellationToken);

            var currentUser = await db.Users
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    user => user.Id == userId.Value,
                    cancellationToken);

            if (project == null ||
                currentUser == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The project could not be found.";

                return RedirectToPage();
            }

            var oldTitle =
                string.IsNullOrWhiteSpace(project.Title)
                    ? "Untitled Project"
                    : project.Title.Trim();

            if (string.Equals(
                    oldTitle,
                    cleanTitle,
                    StringComparison.Ordinal))
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Success"] =
                    "The project name is already up to date.";

                return RedirectToPage();
            }

            project.Title = cleanTitle;
            project.UpdatedAt = DateTime.UtcNow;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId.Value,
                    ActionType = "project_renamed",
                    Description =
                        $"{SafeName(currentUser.FullName)} "
                        + $"renamed the project from "
                        + $"\"{oldTitle}\" to "
                        + $"\"{cleanTitle}\".",
                    CreatedAtUtc = DateTime.UtcNow
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await NotifyProjectParticipantsAsync(
                project.Id,
                userId.Value,
                "Project renamed",
                $"{SafeName(currentUser.FullName)} renamed the project from \"{oldTitle}\" to \"{cleanTitle}\".",
                "project_renamed",
                $"/Student/MyProjects",
                cancellationToken);

            TempData["Success"] =
                "The project name was updated "
                + "for every project member.";

            return RedirectToPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to rename project {ProjectId} "
                + "by user {UserId}.",
                projectId,
                userId.Value);

            TempData["Error"] =
                "The project could not be renamed. "
                + "Please try again.";

            return RedirectToPage();
        }
    }
    public async Task<IActionResult>
    OnPostAcceptInvitationAsync(
        int invitationId,
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage(
                "/Account/Login");
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var currentUser = await db.Users
                .FirstOrDefaultAsync(
                    user =>
                        user.Id == userId.Value,
                    cancellationToken);

            var invitation =
                await db.ProjectInvitations
                    .Include(item =>
                        item.Project)
                    .FirstOrDefaultAsync(
                        item =>
                            item.Id == invitationId,
                        cancellationToken);

            if (currentUser == null ||
                invitation == null ||
                !IsInvitationRecipient(
                    invitation,
                    currentUser))
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The invitation could not be found "
                    + "or does not belong to your account.";

                return RedirectToPage();
            }

            if (!string.Equals(
                    invitation.Status,
                    "pending",
                    StringComparison.OrdinalIgnoreCase))
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This invitation has already been "
                    + "processed.";

                return RedirectToPage();
            }

            var now = DateTime.UtcNow;

            if (invitation.ExpiresAt <= now)
            {
                invitation.Status = "expired";
                invitation.RespondedAt = now;

                await db.SaveChangesAsync(
                    cancellationToken);

                await transaction.CommitAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This invitation has expired. Ask the "
                    + "project owner to send a new one.";

                return RedirectToPage();
            }

            var project = invitation.Project;

            if (project == null ||
    project.IsDeleted)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The invited project is no longer available.";

                return RedirectToPage();
            }

            var existingMembership =
                await db.ProjectMembers
                    .FirstOrDefaultAsync(
                        member =>
                            member.ProjectId ==
                                project.Id &&
                            member.UserId ==
                                userId.Value,
                        cancellationToken);

            /*
             * Safely handle duplicated button submissions
             * or an invitation sent to an existing member.
             */
            if (existingMembership != null &&
                string.Equals(
                    existingMembership.Status,
                    "active",
                    StringComparison.OrdinalIgnoreCase))
            {
                invitation.Status = "accepted";
                invitation.RespondedAt = now;

                currentUser.LastActiveProjectId =
                    project.Id;

                currentUser.LastProjectPage =
                    "/Student/Dashboard";

                currentUser.LastProjectVisitedAtUtc =
                    now;

                await db.SaveChangesAsync(
                    cancellationToken);

                await transaction.CommitAsync(
                    cancellationToken);

                TempData["Success"] =
                    "You are already an active member of "
                    + $"{SafeProjectTitle(project.Title)}.";

                return RedirectToPage();
            }

            var maximumMembers = Math.Clamp(
                project.MaximumMembers,
                1,
                MaximumProjectMembers);

            var activeMembersCount =
                await db.ProjectMembers.CountAsync(
                    member =>
                        member.ProjectId ==
                            project.Id &&
                        member.Status == "active",
                    cancellationToken);

            /*
             * Capacity is checked again during acceptance,
             * not only when the invitation was created.
             */
            if (activeMembersCount >= maximumMembers)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This project has reached its maximum "
                    + "team capacity. Contact the project "
                    + "owner.";

                return RedirectToPage();
            }

            /*
             * The unique ProjectId + UserId database index
             * means a removed member must be reactivated
             * instead of inserting a duplicate row.
             */
            if (existingMembership == null)
            {
                db.ProjectMembers.Add(
                  new ProjectMember
                  {
                      ProjectId = project.Id,
                      UserId = userId.Value,
                      Role = "collaborator",
                      Status = "active",
                      JoinedAt = now,
                      LeftAt = null,
                      IsArchived = false,
                      ArchivedAtUtc = null,
                      RemovedAtUtc = null
                  });
            }
            else
            {
                existingMembership.Role =
                    "collaborator";

                existingMembership.Status =
                    "active";

                existingMembership.JoinedAt =
                    now;

                existingMembership.LeftAt =
                    null;
                existingMembership.IsArchived =
                   false;

                existingMembership.ArchivedAtUtc =
                    null;

                existingMembership.RemovedAtUtc =
                    null;
            }

            invitation.Status = "accepted";
            invitation.RespondedAt = now;

            project.UpdatedAt = now;

            currentUser.LastActiveProjectId =
                project.Id;

            currentUser.LastProjectPage =
                "/Student/Dashboard";

            currentUser.LastProjectVisitedAtUtc =
                now;

            db.ProjectActivities.Add(
                new ProjectActivity
                {
                    ProjectId = project.Id,
                    UserId = userId.Value,
                    ActionType = "member_joined",

                    Description =
                        $"{SafeName(currentUser.FullName)} "
                        + "accepted the invitation and "
                        + "joined the project.",

                    CreatedAtUtc = now
                });

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            await NotifyInvitationSenderAsync(
                invitation.InvitedByUserId,
                "Invitation accepted",
                $"{SafeName(currentUser.FullName)} accepted the invitation and joined \"{SafeProjectTitle(project.Title)}\".",
                "invitation_accepted",
                project.Id,
                userId.Value,
                cancellationToken);

            TempData["Success"] =
                "Invitation accepted. You are now a "
                + "collaborator on "
                + $"{SafeProjectTitle(project.Title)}.";

            return RedirectToPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to accept invitation "
                + "{InvitationId} for user {UserId}.",
                invitationId,
                userId.Value);

            TempData["Error"] =
                "The invitation could not be accepted. "
                + "Please try again.";

            return RedirectToPage();
        }
    }
    public async Task<IActionResult>
    OnPostRejectInvitationAsync(
        int invitationId,
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
        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var currentUser = await db.Users
                .FirstOrDefaultAsync(
                    user =>
                        user.Id == userId.Value,
                    cancellationToken);

            var invitation =
                await db.ProjectInvitations
                    .Include(item =>
                        item.Project)
                    .FirstOrDefaultAsync(
                        item =>
                            item.Id == invitationId,
                        cancellationToken);

            if (currentUser == null ||
                invitation == null ||
                !IsInvitationRecipient(
                    invitation,
                    currentUser))
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "The invitation could not be found "
                    + "or does not belong to your account.";

                return RedirectToPage();
            }

            if (!string.Equals(
                    invitation.Status,
                    "pending",
                    StringComparison.OrdinalIgnoreCase))
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                TempData["Error"] =
                    "This invitation has already been "
                    + "processed.";

                return RedirectToPage();
            }

            var now = DateTime.UtcNow;
            var expired =
                invitation.ExpiresAt <= now;

            invitation.Status = expired
                ? "expired"
                : "rejected";

            invitation.RespondedAt = now;

            if (invitation.Project != null)
            {
                invitation.Project.UpdatedAt = now;
            }

            if (!expired)
            {
                db.ProjectActivities.Add(
                    new ProjectActivity
                    {
                        ProjectId =
                            invitation.ProjectId,

                        UserId =
                            userId.Value,

                        ActionType =
                            "invitation_rejected",

                        Description =
                            $"{SafeName(currentUser.FullName)} "
                            + "rejected the project "
                            + "invitation.",

                        CreatedAtUtc =
                            now
                    });
            }

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            if (!expired)
            {
                await NotifyInvitationSenderAsync(
                    invitation.InvitedByUserId,
                    "Invitation rejected",
                    $"{SafeName(currentUser.FullName)} declined the invitation to join \"{SafeProjectTitle(invitation.Project?.Title)}\".",
                    "invitation_rejected",
                    invitation.ProjectId,
                    userId.Value,
                    cancellationToken);
            }

            TempData[
                expired ? "Error" : "Success"] =
                expired
                    ? "This invitation had already expired."
                    : "The project invitation was rejected.";

            return RedirectToPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Failed to reject invitation "
                + "{InvitationId} for user {UserId}.",
                invitationId,
                userId.Value);

            TempData["Error"] =
                "The invitation could not be rejected. "
                + "Please try again.";

            return RedirectToPage();
        }
    }
    private async Task NotifyInvitationSenderAsync(
        int senderUserId,
        string title,
        string message,
        string type,
        int projectId,
        int actorUserId,
        CancellationToken cancellationToken)
    {
        var senderRole = await db.Users
            .AsNoTracking()
            .Where(user => user.Id == senderUserId)
            .Select(user => user.Role)
            .FirstOrDefaultAsync(cancellationToken);

        var destination = senderRole?.Trim().ToLowerInvariant() switch
        {
            "supervisor" =>
                $"/Supervisor/ProgressTracking?projectId={projectId}",
            "admin" =>
                $"/Admin/Projects?projectId={projectId}",
            _ =>
                $"/Student/TeamManagement?projectId={projectId}"
        };

        await TryNotifyAsync(
            senderUserId,
            title,
            message,
            type,
            destination,
            projectId,
            actorUserId,
            cancellationToken);
    }

    private async Task NotifyProjectParticipantsAsync(
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
            .ToListAsync(cancellationToken);

        foreach (var recipientId in recipientIds.Distinct())
        {
            await TryNotifyAsync(
                recipientId,
                title,
                message,
                type,
                url,
                projectId,
                actorUserId,
                cancellationToken);
        }
    }

    private async Task TryNotifyAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url,
        int? projectId,
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
                recipientUserId: recipientUserId,
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
                "Project {ProjectId} changed, but notification delivery failed for user {UserId}.",
                projectId,
                recipientUserId);
        }
    }

    private static bool IsInvitationRecipient(
    ProjectInvitation invitation,
    User currentUser)
    {
        /*
         * New invitations are connected directly
         * through InvitedUserId.
         */
        if (invitation.InvitedUserId.HasValue)
        {
            return invitation.InvitedUserId.Value ==
                   currentUser.Id;
        }

        /*
         * Email fallback supports older invitation
         * records that may not contain InvitedUserId.
         */
        return string.Equals(
            invitation.InvitedEmail?.Trim(),
            currentUser.Email?.Trim(),
            StringComparison.OrdinalIgnoreCase);
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
    }
    private int? CurrentUserId()
    {
        var value = User.FindFirst(
            ClaimTypes.NameIdentifier)?.Value;

        return int.TryParse(value, out var userId)
            ? userId
            : null;
    }

    private static string SafeName(
        string? fullName)
    {
        return string.IsNullOrWhiteSpace(fullName)
            ? "A project member"
            : fullName.Trim();
    }

    private static string Initials(
        string? fullName)
    {
        if (string.IsNullOrWhiteSpace(fullName))
        {
            return "ST";
        }

        var parts = fullName
            .Split(
                ' ',
                StringSplitOptions.RemoveEmptyEntries)
            .Take(2)
            .ToList();

        if (parts.Count == 0)
        {
            return "ST";
        }

        return string.Join(
                "",
                parts.Select(part => part[0]))
            .ToUpperInvariant();
    }

    private static string Shorten(
        string? value,
        int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "A collaborative final year "
                   + "project workspace.";
        }

        var clean = value.Trim();

        if (clean.Length <= maximumLength)
        {
            return clean;
        }

        return $"{clean[..maximumLength].TrimEnd()}…";
    }

    private static string Normalize(
        string? value)
    {
        return string.IsNullOrWhiteSpace(value)
            ? ""
            : value.Trim().ToLowerInvariant();
    }

    private static string NormalizeStatus(
        string? status)
    {
        return string.IsNullOrWhiteSpace(status)
            ? "draft"
            : status
                .Trim()
                .ToLowerInvariant()
                .Replace(" ", "_")
                .Replace("-", "_");
    }

    private static string ToDisplayText(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "Draft";
        }

        var words = value
            .Trim()
            .Replace("_", " ")
            .Replace("-", " ")
            .Split(
                ' ',
                StringSplitOptions.RemoveEmptyEntries);

        return string.Join(
            " ",
            words.Select(word =>
                word.Length == 1
                    ? word.ToUpperInvariant()
                    : char.ToUpperInvariant(word[0])
                      + word[1..].ToLowerInvariant()));
    }

    private static string ResolveStatusClass(
        string? status)
    {
        return NormalizeStatus(status) switch
        {
            "active" => "status-active",
            "in_progress" => "status-progress",
            "completed" => "status-completed",
            "planning" => "status-planning",
            "draft" => "status-draft",
            _ => "status-neutral"
        };
    }

    private static string ResolveTeamActionText(
        string role,
        int activeMembers,
        int maximumMembers)
    {
        if (!string.Equals(
                role,
                "owner",
                StringComparison.OrdinalIgnoreCase))
        {
            return "View Team";
        }

        return activeMembers < maximumMembers
            ? "Collaborate"
            : "Manage Team";
    }

    private static ProjectIconViewModel
        ResolveProjectIcon(string domain)
    {
        var value = domain.ToLowerInvariant();

        if (value.Contains("health") ||
            value.Contains("medical"))
        {
            return new ProjectIconViewModel(
                "project-icon-teal",
                "bi bi-heart-pulse-fill");
        }

        if (value.Contains("web"))
        {
            return new ProjectIconViewModel(
                "project-icon-purple",
                "bi bi-window-stack");
        }

        if (value.Contains("data") ||
            value.Contains("analytics"))
        {
            return new ProjectIconViewModel(
                "project-icon-orange",
                "bi bi-bar-chart-line-fill");
        }

        if (value.Contains("mobile"))
        {
            return new ProjectIconViewModel(
                "project-icon-cyan",
                "bi bi-phone-fill");
        }

        if (value.Contains("not selected"))
        {
            return new ProjectIconViewModel(
                "project-icon-empty",
                "bi bi-lightbulb");
        }

        return new ProjectIconViewModel(
            "project-icon-navy",
            "bi bi-cpu-fill");
    }

    private static long ToUnixMilliseconds(
        DateTime dateTime)
    {
        var utcDateTime = dateTime.Kind ==
                          DateTimeKind.Utc
            ? dateTime
            : DateTime.SpecifyKind(
                dateTime,
                DateTimeKind.Utc);

        return new DateTimeOffset(
                utcDateTime)
            .ToUnixTimeMilliseconds();
    }
    public sealed record PendingInvitationViewModel(
    int Id,
    int ProjectId,
    string ProjectTitle,
    string InvitedByName,
    string InvitedByEmail,
    int ActiveMembersCount,
    int MaximumMembers,
    DateTime CreatedAt,
    DateTime ExpiresAt);
    public sealed record ProjectCardViewModel(
        int Id,
        int? ProjectIdeaId,
        string Title,
        string SelectedIdeaTitle,
        bool HasSelectedIdea,
        string Domain,
        string Description,
        string Role,
        string RoleLabel,
        string Status,
        string StatusLabel,
        string StatusCssClass,
        int ProgressPercentage,
        int TeamFillPercentage,
        int ActiveMembersCount,
        int MaximumMembers,
        List<MemberPreviewViewModel> Members,
        DateTime CreatedAt,
        DateTime UpdatedAt,
        long CreatedSortValue,
        string IconCssClass,
        string IconClass,
        string TeamActionText,
        bool IsActiveProject,
        string? LastActivityDescription,
        DateTime? LastActivityAtUtc);

    public sealed record MemberPreviewViewModel(
        int UserId,
        string FullName,
        string Initials,
        string Role);

    private sealed record ProjectIconViewModel(
        string CssClass,
        string IconClass);

    private sealed record LatestActivityQueryResult(
        int ProjectId,
        string Description,
        DateTime CreatedAtUtc);
}