using System.Net;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class SupervisorAssignmentsModel(
    ApplicationDbContext db,
    INotificationService notificationService,
    ILogger<SupervisorAssignmentsModel> logger)
    : PageModel
{
    /*
     * Capacity now means supervised projects,
     * not individual students.
     */
    private const int SupervisorLimit = 4;

    public AdminAssignmentStats Stats { get; private set; } =
        new();

    public List<PendingAssignmentRow>
        PendingRequests
    { get; private set; } = [];

    public List<SupervisorCapacityRow>
        SupervisorCapacity
    { get; private set; } = [];

    public List<ActiveAssignmentRow>
        ActiveAssignments
    { get; private set; } = [];

    public List<RejectedAssignmentRow>
        RecentRejectedRequests
    { get; private set; } = [];

    public List<AssignableSupervisorOption>
        AssignableSupervisors
    { get; private set; } = [];

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public async Task OnGetAsync(
        CancellationToken cancellationToken)
    {
        await LoadAsync(
            cancellationToken);
    }

    public async Task<IActionResult>
        OnPostApproveAsync(
            int assignmentId,
            CancellationToken cancellationToken)
    {
        var adminId =
            GetCurrentUserId();

        var assignment =
            await LoadPendingAssignmentAsync(
                assignmentId,
                cancellationToken);

        if (assignment?.Project == null ||
            !assignment.ProjectId.HasValue)
        {
            ErrorMessage =
                "This project supervisor request was "
                + "already processed or is unavailable.";

            return RedirectToPage();
        }

        var project =
            assignment.Project;

        if (!CanProcessPendingProject(
                assignment,
                project))
        {
            ErrorMessage =
                "This project already has an active "
                + "supervisor or the request is outdated.";

            return RedirectToPage();
        }

        var supervisor =
            await LoadValidSupervisorAsync(
                assignment.SupervisorId,
                cancellationToken);

        if (supervisor == null)
        {
            ErrorMessage =
                "The requested supervisor could not "
                + "be found.";

            return RedirectToPage();
        }

        var activeCount =
            await CountActiveProjectsAsync(
                assignment.SupervisorId,
                project.Id,
                cancellationToken);

        if (activeCount >=
            SupervisorLimit)
        {
            ErrorMessage =
                "This supervisor already has the "
                + "maximum number of active projects. "
                + "Assign a different supervisor.";

            return RedirectToPage();
        }

        var now =
            DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            assignment.Status =
                "active";

            assignment.AssignedByAdminId =
                adminId;

            assignment.ApprovedAt =
                now;

            assignment.RejectedAt =
                null;

            assignment.AdminNote =
                "Approved by admin.";

            assignment.UpdatedAt =
                now;

            project.SupervisorId =
                assignment.SupervisorId;

            project.SupervisorAssignmentStatus =
                "active";

            project.UpdatedAt =
                now;

            AddProjectActivity(
                project.Id,
                adminId,
                "supervisor_assigned",
                $"{supervisor.FullName} was assigned "
                + "as the official project supervisor.",
                now);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogWarning(
                exception,
                "Could not approve assignment "
                + "{AssignmentId} for project "
                + "{ProjectId}.",
                assignmentId,
                project.Id);

            ErrorMessage =
                "The project supervisor could not be "
                + "approved. The request may already "
                + "have been processed.";

            return RedirectToPage();
        }

        await SendAssignmentApprovedCommunicationAsync(
            assignment.Id,
            cancellationToken);

        SuccessMessage =
            "Project supervisor approved successfully. "
            + "All active project members and the "
            + "supervisor were notified by email.";

        return RedirectToPage();
    }

    public async Task<IActionResult>
        OnPostAssignDifferentAsync(
            int assignmentId,
            int supervisorId,
            string? adminNote,
            CancellationToken cancellationToken)
    {
        var adminId =
            GetCurrentUserId();

        var assignment =
            await LoadPendingAssignmentAsync(
                assignmentId,
                cancellationToken);

        if (assignment?.Project == null ||
            !assignment.ProjectId.HasValue)
        {
            ErrorMessage =
                "This project supervisor request was "
                + "already processed or is unavailable.";

            return RedirectToPage();
        }

        var project =
            assignment.Project;

        if (!CanProcessPendingProject(
                assignment,
                project))
        {
            ErrorMessage =
                "This project already has an active "
                + "supervisor or the request is outdated.";

            return RedirectToPage();
        }

        var supervisor =
            await LoadValidSupervisorAsync(
                supervisorId,
                cancellationToken);

        if (supervisor == null)
        {
            ErrorMessage =
                "The selected supervisor was not found.";

            return RedirectToPage();
        }

        var activeCount =
            await CountActiveProjectsAsync(
                supervisorId,
                project.Id,
                cancellationToken);

        if (activeCount >=
            SupervisorLimit)
        {
            ErrorMessage =
                "The selected supervisor already has "
                + "the maximum number of active projects.";

            return RedirectToPage();
        }

        var note =
            string.IsNullOrWhiteSpace(
                adminNote)
                ? "Admin assigned a different supervisor "
                  + "based on capacity and suitability."
                : adminNote.Trim();

        var now =
            DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            assignment.SupervisorId =
                supervisorId;

            assignment.Status =
                "active";

            assignment.AssignedByAdminId =
                adminId;

            assignment.ApprovedAt =
                now;

            assignment.RejectedAt =
                null;

            assignment.AdminNote =
                note;

            assignment.UpdatedAt =
                now;

            project.SupervisorId =
                supervisorId;

            project.SupervisorAssignmentStatus =
                "active";

            project.UpdatedAt =
                now;

            AddProjectActivity(
                project.Id,
                adminId,
                "supervisor_assigned",
                $"{supervisor.FullName} was assigned "
                + "as the official project supervisor.",
                now);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogWarning(
                exception,
                "Could not assign supervisor "
                + "{SupervisorId} to project "
                + "{ProjectId}.",
                supervisorId,
                project.Id);

            ErrorMessage =
                "The project supervisor assignment "
                + "could not be completed.";

            return RedirectToPage();
        }

        await SendAssignmentApprovedCommunicationAsync(
            assignment.Id,
            cancellationToken);

        SuccessMessage =
            $"{supervisor.FullName} was assigned to "
            + $"project \"{SafeProjectTitle(project.Title)}\". "
            + "All active project members and the "
            + "supervisor were notified by email.";

        return RedirectToPage();
    }

    public async Task<IActionResult>
        OnPostRejectAsync(
            int assignmentId,
            string? adminNote,
            CancellationToken cancellationToken)
    {
        var adminId =
            GetCurrentUserId();

        var assignment =
            await LoadPendingAssignmentAsync(
                assignmentId,
                cancellationToken);

        if (assignment?.Project == null ||
            !assignment.ProjectId.HasValue)
        {
            ErrorMessage =
                "This project supervisor request was "
                + "already processed or is unavailable.";

            return RedirectToPage();
        }

        var project =
            assignment.Project;

        if (!CanProcessPendingProject(
                assignment,
                project))
        {
            ErrorMessage =
                "This project request was already "
                + "processed by another administrator.";

            return RedirectToPage();
        }

        var note =
            string.IsNullOrWhiteSpace(
                adminNote)
                ? "The request was not approved. "
                  + "The project owner may choose another "
                  + "available supervisor."
                : adminNote.Trim();

        var now =
            DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            assignment.Status =
                "rejected";

            assignment.AssignedByAdminId =
                adminId;

            assignment.AdminNote =
                note;

            assignment.RejectedAt =
                now;

            assignment.ApprovedAt =
                null;

            assignment.UpdatedAt =
                now;

            project.SupervisorId =
                null;

            project.SupervisorAssignmentStatus =
                "unassigned";

            project.UpdatedAt =
                now;

            AddProjectActivity(
                project.Id,
                adminId,
                "supervisor_request_rejected",
                "The project supervisor request "
                + "was not approved.",
                now);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogWarning(
                exception,
                "Could not reject assignment "
                + "{AssignmentId} for project "
                + "{ProjectId}.",
                assignmentId,
                project.Id);

            ErrorMessage =
                "The project supervisor request could "
                + "not be rejected.";

            return RedirectToPage();
        }

        /*
         * The requested supervisor is not emailed.
         *
         * They were never officially assigned and were
         * deliberately not notified before approval.
         */
        await SendAssignmentRejectedCommunicationAsync(
            assignment.Id,
            cancellationToken);

        SuccessMessage =
            "Project supervisor request rejected. "
            + "All active project members were notified.";

        return RedirectToPage();
    }

    public async Task<IActionResult>
        OnPostTransferAsync(
            int assignmentId,
            int newSupervisorId,
            string transferReason,
            CancellationToken cancellationToken)
    {
        var adminId =
            GetCurrentUserId();

        if (string.IsNullOrWhiteSpace(
                transferReason))
        {
            ErrorMessage =
                "A transfer reason is required.";

            return RedirectToPage();
        }

        var currentAssignment =
            await db.SupervisorAssignments
                .Include(assignment =>
                    assignment.Project)
                .Include(assignment =>
                    assignment.Supervisor)
                .AsSplitQuery()
                .FirstOrDefaultAsync(
                    assignment =>
                        assignment.Id ==
                            assignmentId &&
                        assignment.ProjectId.HasValue &&
                        assignment.Status ==
                            "active",
                    cancellationToken);

        if (currentAssignment?.Project == null ||
            !currentAssignment.ProjectId.HasValue)
        {
            ErrorMessage =
                "This project assignment was already "
                + "changed or is unavailable.";

            return RedirectToPage();
        }

        var project =
            currentAssignment.Project;

        if (project.SupervisorId !=
            currentAssignment.SupervisorId ||
            NormalizeStatus(
                project.SupervisorAssignmentStatus) !=
            "active")
        {
            ErrorMessage =
                "This project assignment is no longer "
                + "the current active assignment.";

            return RedirectToPage();
        }

        if (currentAssignment.SupervisorId ==
            newSupervisorId)
        {
            ErrorMessage =
                "This supervisor is already assigned "
                + "to the project.";

            return RedirectToPage();
        }

        var newSupervisor =
            await LoadValidSupervisorAsync(
                newSupervisorId,
                cancellationToken);

        if (newSupervisor == null)
        {
            ErrorMessage =
                "The selected new supervisor was not found.";

            return RedirectToPage();
        }

        var activeCount =
            await CountActiveProjectsAsync(
                newSupervisorId,
                project.Id,
                cancellationToken);

        if (activeCount >=
            SupervisorLimit)
        {
            ErrorMessage =
                "The selected supervisor already has "
                + "the maximum number of active projects.";

            return RedirectToPage();
        }

        var oldSupervisorId =
            currentAssignment.SupervisorId;

        var oldSupervisorName =
            currentAssignment.Supervisor?.FullName
            ?? "Previous supervisor";

        var reason =
            transferReason.Trim();

        var now =
            DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        SupervisorAssignment? newAssignment =
            null;

        try
        {
            /*
             * Save the previous row as transferred
             * before inserting the new active row.
             *
             * This avoids violating the unique active
             * assignment index for ProjectId.
             */
            currentAssignment.Status =
                "transferred";

            currentAssignment.AdminNote =
                $"Transferred to {newSupervisor.FullName}. "
                + $"Reason: {reason}";

            currentAssignment.UpdatedAt =
                now;

            await db.SaveChangesAsync(
                cancellationToken);

            newAssignment =
                new SupervisorAssignment
                {
                    ProjectId =
                        project.Id,

                    StudentId =
                        project.StudentId,

                    SupervisorId =
                        newSupervisorId,

                    AssignedByAdminId =
                        adminId,

                    Status =
                        "active",

                    StudentMessage =
                        currentAssignment.StudentMessage,

                    AdminNote =
                        $"Transferred from "
                        + $"{oldSupervisorName}. "
                        + $"Reason: {reason}",

                    RequestedAt =
                        now,

                    ApprovedAt =
                        now,

                    RejectedAt =
                        null,

                    UpdatedAt =
                        now
                };

            db.SupervisorAssignments.Add(
                newAssignment);

            project.SupervisorId =
                newSupervisorId;

            project.SupervisorAssignmentStatus =
                "active";

            project.UpdatedAt =
                now;

            AddProjectActivity(
                project.Id,
                adminId,
                "supervisor_transferred",
                $"Project supervision changed from "
                + $"{oldSupervisorName} to "
                + $"{newSupervisor.FullName}.",
                now);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogWarning(
                exception,
                "Could not transfer project "
                + "{ProjectId} to supervisor "
                + "{SupervisorId}.",
                project.Id,
                newSupervisorId);

            ErrorMessage =
                "The project supervisor transfer "
                + "could not be completed.";

            return RedirectToPage();
        }

        await SendTransferCommunicationAsync(
            newAssignment.Id,
            oldSupervisorId,
            oldSupervisorName,
            reason,
            cancellationToken);

        SuccessMessage =
            $"Project \"{SafeProjectTitle(project.Title)}\" "
            + $"was transferred from {oldSupervisorName} "
            + $"to {newSupervisor.FullName}. "
            + "Project members and both supervisors "
            + "were notified.";

        return RedirectToPage();
    }

    private async Task LoadAsync(
        CancellationToken cancellationToken)
    {
        var activeProjectRows =
            await db.Projects
                .AsNoTracking()
                .Where(project =>
                    project.SupervisorId.HasValue &&
                    project.SupervisorAssignmentStatus ==
                        "active")
                .Select(project => new
                {
                    SupervisorId =
                        project.SupervisorId!.Value,

                    ProjectId =
                        project.Id
                })
                .ToListAsync(
                    cancellationToken);

        var activeLoadMap =
            activeProjectRows
                .GroupBy(row =>
                    row.SupervisorId)
                .ToDictionary(
                    group =>
                        group.Key,

                    group =>
                        group
                            .Select(row =>
                                row.ProjectId)
                            .Distinct()
                            .Count());

        var pendingProjectRows =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Where(assignment =>
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "pending_admin")
                .Select(assignment => new
                {
                    assignment.SupervisorId,

                    ProjectId =
                        assignment.ProjectId!.Value
                })
                .ToListAsync(
                    cancellationToken);

        var pendingLoadMap =
            pendingProjectRows
                .GroupBy(row =>
                    row.SupervisorId)
                .ToDictionary(
                    group =>
                        group.Key,

                    group =>
                        group
                            .Select(row =>
                                row.ProjectId)
                            .Distinct()
                            .Count());

        var supervisors =
            await db.Users
                .AsNoTracking()
                .Include(user =>
                    user.SupervisorProfile)
                .Where(user =>
                    user.Role.ToLower() ==
                        "supervisor" &&
                    user.SupervisorProfile != null)
                .OrderBy(user =>
                    user.FullName)
                .ToListAsync(
                    cancellationToken);

        SupervisorCapacity =
            supervisors
                .Select(supervisor =>
                {
                    var activeCount =
                        activeLoadMap.GetValueOrDefault(
                            supervisor.Id,
                            0);

                    var pendingCount =
                        pendingLoadMap.GetValueOrDefault(
                            supervisor.Id,
                            0);

                    return new SupervisorCapacityRow
                    {
                        SupervisorId =
                            supervisor.Id,

                        FullName =
                            supervisor.FullName,

                        Email =
                            supervisor.Email,

                        Department =
                            supervisor.SupervisorProfile
                                ?.Department
                            ?? "",

                        Specialization =
                            supervisor.SupervisorProfile
                                ?.Specialization
                            ?? "",

                        ResearchAreas =
                            supervisor.SupervisorProfile
                                ?.ResearchAreas
                            ?? "",

                        ActiveCount =
                            activeCount,

                        PendingCount =
                            pendingCount,

                        CapacityLimit =
                            SupervisorLimit,

                        IsFull =
                            activeCount >=
                            SupervisorLimit
                    };
                })
                .OrderByDescending(row =>
                    row.IsFull)
                .ThenByDescending(row =>
                    row.ActiveCount)
                .ThenBy(row =>
                    row.FullName)
                .ToList();

        AssignableSupervisors =
            SupervisorCapacity
                .Where(supervisor =>
                    !supervisor.IsFull)
                .Select(supervisor =>
                    new AssignableSupervisorOption
                    {
                        SupervisorId =
                            supervisor.SupervisorId,

                        FullName =
                            supervisor.FullName,

                        CapacityUsed =
                            supervisor.ActiveCount,

                        CapacityLimit =
                            supervisor.CapacityLimit,

                        Department =
                            supervisor.Department,

                        Specialization =
                            supervisor.Specialization
                    })
                .OrderBy(supervisor =>
                    supervisor.CapacityUsed)
                .ThenBy(supervisor =>
                    supervisor.FullName)
                .ToList();

        var pendingEntities =
            await BuildAssignmentQuery()
                .Where(assignment =>
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "pending_admin")
                .OrderBy(assignment =>
                    assignment.RequestedAt)
                .ToListAsync(
                    cancellationToken);

        PendingRequests =
            pendingEntities
                .Where(assignment =>
                    assignment.Project != null)
                .Select(assignment =>
                {
                    var project =
                        assignment.Project!;

                    var team =
                        BuildTeamSummary(
                            project,
                            assignment.Student);

                    var used =
                        activeLoadMap.GetValueOrDefault(
                            assignment.SupervisorId,
                            0);

                    return new PendingAssignmentRow
                    {
                        AssignmentId =
                            assignment.Id,

                        ProjectId =
                            project.Id,

                        ProjectTitle =
                            SafeProjectTitle(
                                project.Title),

                        ProjectMembers =
                            team.MemberNames,

                        ProjectMemberCount =
                            team.MemberCount,

                        OwnerName =
                            team.OwnerName,

                        OwnerEmail =
                            team.OwnerEmail,

                        StudentId =
                            assignment.StudentId,

                        /*
                         * Temporary compatibility with
                         * the current Razor design.
                         */
                        StudentName =
                            team.OwnerName,

                        StudentEmail =
                            team.OwnerEmail,

                        StudentMajor =
                            team.OwnerMajor,

                        PreferredDomain =
                            project.ProjectIdea?.Domain
                            ?? team.PreferredDomain,

                        StudentSkills =
                            team.TeamSkills,

                        StudentIdeaTitle =
                            project.ProjectIdea?.Title
                            ?? "No official idea selected",

                        StudentIdeaDomain =
                            project.ProjectIdea?.Domain
                            ?? "",

                        RequestedSupervisorId =
                            assignment.SupervisorId,

                        RequestedSupervisorName =
                            assignment.Supervisor?.FullName
                            ?? "Supervisor",

                        RequestedSupervisorDepartment =
                            assignment.Supervisor
                                ?.SupervisorProfile
                                ?.Department
                            ?? "",

                        RequestedSupervisorSpecialization =
                            assignment.Supervisor
                                ?.SupervisorProfile
                                ?.Specialization
                            ?? "",

                        RequestedSupervisorCapacityUsed =
                            used,

                        RequestedSupervisorCapacityLimit =
                            SupervisorLimit,

                        RequestedSupervisorIsFull =
                            used >= SupervisorLimit,

                        StudentMessage =
                            assignment.StudentMessage,

                        RequestedAt =
                            assignment.RequestedAt
                    };
                })
                .ToList();

        var activeEntities =
            await BuildAssignmentQuery()
                .Where(assignment =>
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "active")
                .OrderByDescending(assignment =>
                    assignment.ApprovedAt ??
                    assignment.UpdatedAt)
                .ToListAsync(
                    cancellationToken);

        ActiveAssignments =
            activeEntities
                .Where(assignment =>
                    assignment.Project != null)
                .Select(assignment =>
                {
                    var project =
                        assignment.Project!;

                    var team =
                        BuildTeamSummary(
                            project,
                            assignment.Student);

                    return new ActiveAssignmentRow
                    {
                        AssignmentId =
                            assignment.Id,

                        ProjectId =
                            project.Id,

                        ProjectTitle =
                            SafeProjectTitle(
                                project.Title),

                        ProjectMembers =
                            team.MemberNames,

                        ProjectMemberCount =
                            team.MemberCount,

                        StudentId =
                            assignment.StudentId,

                        SupervisorId =
                            assignment.SupervisorId,

                        /*
                         * Temporary compatibility with
                         * the current Razor page.
                         */
                        StudentName =
                            team.OwnerName,

                        StudentEmail =
                            team.OwnerEmail,

                        StudentMajor =
                            team.OwnerMajor,

                        PreferredDomain =
                            project.ProjectIdea?.Domain
                            ?? team.PreferredDomain,

                        SupervisorName =
                            assignment.Supervisor?.FullName
                            ?? "Supervisor",

                        SupervisorSpecialization =
                            assignment.Supervisor
                                ?.SupervisorProfile
                                ?.Specialization
                            ?? "",

                        IdeaTitle =
                            project.ProjectIdea?.Title
                            ?? "No official idea selected",

                        ApprovedAt =
                            assignment.ApprovedAt ??
                            assignment.UpdatedAt
                    };
                })
                .ToList();

        var rejectedEntities =
            await BuildAssignmentQuery()
                .Where(assignment =>
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "rejected")
                .OrderByDescending(assignment =>
                    assignment.RejectedAt ??
                    assignment.UpdatedAt)
                .Take(8)
                .ToListAsync(
                    cancellationToken);

        RecentRejectedRequests =
            rejectedEntities
                .Where(assignment =>
                    assignment.Project != null)
                .Select(assignment =>
                {
                    var project =
                        assignment.Project!;

                    var team =
                        BuildTeamSummary(
                            project,
                            assignment.Student);

                    return new RejectedAssignmentRow
                    {
                        AssignmentId =
                            assignment.Id,

                        ProjectId =
                            project.Id,

                        ProjectTitle =
                            SafeProjectTitle(
                                project.Title),

                        ProjectMembers =
                            team.MemberNames,

                        /*
                         * Temporary compatibility with
                         * the current Razor page.
                         */
                        StudentName =
                            team.OwnerName,

                        SupervisorName =
                            assignment.Supervisor?.FullName
                            ?? "Supervisor",

                        AdminNote =
                            assignment.AdminNote,

                        RejectedAt =
                            assignment.RejectedAt ??
                            assignment.UpdatedAt
                    };
                })
                .ToList();

        Stats =
            new AdminAssignmentStats
            {
                PendingRequests =
                    PendingRequests.Count,

                ActiveAssignments =
                    ActiveAssignments.Count,

                FullSupervisors =
                    SupervisorCapacity.Count(
                        supervisor =>
                            supervisor.IsFull),

                AvailableSupervisors =
                    SupervisorCapacity.Count(
                        supervisor =>
                            !supervisor.IsFull)
            };
    }

    private IQueryable<SupervisorAssignment>
        BuildAssignmentQuery()
    {
        return db.SupervisorAssignments
            .AsNoTracking()
            .Include(assignment =>
                assignment.Student)
            .ThenInclude(student =>
                student!.StudentProfile)
            .Include(assignment =>
                assignment.Supervisor)
            .ThenInclude(supervisor =>
                supervisor!.SupervisorProfile)
            .Include(assignment =>
                assignment.Project)
            .ThenInclude(project =>
                project!.ProjectIdea)
            .Include(assignment =>
                assignment.Project)
            .ThenInclude(project =>
                project!.Members
                    .Where(member =>
                        member.Status ==
                            "active"))
            .ThenInclude(member =>
                member.User)
            .ThenInclude(user =>
                user!.StudentProfile)
            .AsSplitQuery();
    }

    private async Task<SupervisorAssignment?>
        LoadPendingAssignmentAsync(
            int assignmentId,
            CancellationToken cancellationToken)
    {
        return await db.SupervisorAssignments
            .Include(assignment =>
                assignment.Project)
            .ThenInclude(project =>
                project!.ProjectIdea)
            .Include(assignment =>
                assignment.Student)
            .Include(assignment =>
                assignment.Supervisor)
            .ThenInclude(supervisor =>
                supervisor!.SupervisorProfile)
            .AsSplitQuery()
            .FirstOrDefaultAsync(
                assignment =>
                    assignment.Id ==
                        assignmentId &&
                    assignment.ProjectId.HasValue &&
                    assignment.Status ==
                        "pending_admin",
                cancellationToken);
    }

    private static bool CanProcessPendingProject(
        SupervisorAssignment assignment,
        Project project)
    {
        return assignment.Status ==
                   "pending_admin" &&
               assignment.ProjectId ==
                   project.Id &&
               !project.SupervisorId.HasValue &&
               NormalizeStatus(
                   project.SupervisorAssignmentStatus) ==
                   "pending_admin";
    }

    private async Task<User?>
        LoadValidSupervisorAsync(
            int supervisorId,
            CancellationToken cancellationToken)
    {
        return await db.Users
            .Include(user =>
                user.SupervisorProfile)
            .FirstOrDefaultAsync(
                user =>
                    user.Id ==
                        supervisorId &&
                    user.Role.ToLower() ==
                        "supervisor" &&
                    user.SupervisorProfile != null,
                cancellationToken);
    }

    private async Task<int>
        CountActiveProjectsAsync(
            int supervisorId,
            int excludedProjectId,
            CancellationToken cancellationToken)
    {
        return await db.Projects
            .AsNoTracking()
            .CountAsync(
                project =>
                    project.Id !=
                        excludedProjectId &&
                    project.SupervisorId ==
                        supervisorId &&
                    project.SupervisorAssignmentStatus ==
                        "active",
                cancellationToken);
    }

    private void AddProjectActivity(
        int projectId,
        int adminId,
        string actionType,
        string description,
        DateTime createdAtUtc)
    {
        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    projectId,

                UserId =
                    adminId,

                ActionType =
                    actionType,

                Description =
                    description,

                CreatedAtUtc =
                    createdAtUtc
            });
    }

    private async Task
        SendAssignmentApprovedCommunicationAsync(
            int assignmentId,
            CancellationToken cancellationToken)
    {
        var assignment =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Include(item =>
                    item.Project)
                .ThenInclude(project =>
                    project!.ProjectIdea)
                .Include(item =>
                    item.Supervisor)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id ==
                            assignmentId &&
                        item.ProjectId.HasValue &&
                        item.Status ==
                            "active",
                    cancellationToken);

        if (assignment?.Project == null ||
            assignment.Supervisor == null)
        {
            return;
        }

        var project =
            assignment.Project;

        var recipients =
            await LoadProjectRecipientsAsync(
                project,
                cancellationToken);

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var ideaTitle =
            project.ProjectIdea?.Title
            ?? "No official idea selected";

        var supervisorName =
            assignment.Supervisor.FullName;

        var memberNames =
            string.Join(
                ", ",
                recipients.Select(
                    recipient =>
                        recipient.FullName));

        foreach (var recipient in recipients)
        {
            await TryNotifyAsync(
                recipient.UserId,
                "Project Supervisor Approved",
                $"{supervisorName} is now the official "
                + $"supervisor for project "
                + $"\"{projectTitle}\".",
                "project_supervisor_approved",
                $"/Student/Feedback"
                + $"?projectId={project.Id}",
                sendEmail:
                    true,
                emailSubject:
                    $"Supervisor approved for {projectTitle}",
                emailHtmlBody:
                    BuildMemberApprovedEmail(
                        recipient.FullName,
                        projectTitle,
                        supervisorName,
                        ideaTitle,
                        memberNames),
                projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
        }

        await TryNotifyAsync(
            assignment.SupervisorId,
            "New Project Assigned",
            $"Project \"{projectTitle}\" with students "
            + $"{memberNames} has been assigned to you.",
            "project_assigned",
            $"/Supervisor/IdeaReview"
            + $"?projectId={project.Id}",
            sendEmail:
                true,
            emailSubject:
                $"New supervised project: {projectTitle}",
            emailHtmlBody:
                BuildSupervisorApprovedEmail(
                    supervisorName,
                    projectTitle,
                    ideaTitle,
                    memberNames),
            projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
    }

    private async Task
        SendAssignmentRejectedCommunicationAsync(
            int assignmentId,
            CancellationToken cancellationToken)
    {
        var assignment =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Include(item =>
                    item.Project)
                .Include(item =>
                    item.Supervisor)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id ==
                            assignmentId &&
                        item.ProjectId.HasValue &&
                        item.Status ==
                            "rejected",
                    cancellationToken);

        if (assignment?.Project == null)
        {
            return;
        }

        var project =
            assignment.Project;

        var recipients =
            await LoadProjectRecipientsAsync(
                project,
                cancellationToken);

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var supervisorName =
            assignment.Supervisor?.FullName
            ?? "the selected supervisor";

        foreach (var recipient in recipients)
        {
            await TryNotifyAsync(
                recipient.UserId,
                "Project Supervisor Request Not Approved",
                $"The request for {supervisorName} "
                + $"for project \"{projectTitle}\" "
                + "was not approved. "
                + $"Admin note: {assignment.AdminNote}",
                "project_supervisor_rejected",
                $"/Student/Feedback"
                + $"?projectId={project.Id}",
                sendEmail:
                    true,
                emailSubject:
                    $"Supervisor request update for "
                    + projectTitle,
                emailHtmlBody:
                    BuildMemberRejectedEmail(
                        recipient.FullName,
                        projectTitle,
                        supervisorName,
                        assignment.AdminNote),
                projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
        }
    }

    private async Task
        SendTransferCommunicationAsync(
            int newAssignmentId,
            int oldSupervisorId,
            string oldSupervisorName,
            string reason,
            CancellationToken cancellationToken)
    {
        var assignment =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Include(item =>
                    item.Project)
                .ThenInclude(project =>
                    project!.ProjectIdea)
                .Include(item =>
                    item.Supervisor)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id ==
                            newAssignmentId &&
                        item.ProjectId.HasValue &&
                        item.Status ==
                            "active",
                    cancellationToken);

        if (assignment?.Project == null ||
            assignment.Supervisor == null)
        {
            return;
        }

        var project =
            assignment.Project;

        var recipients =
            await LoadProjectRecipientsAsync(
                project,
                cancellationToken);

        var oldSupervisor =
            await db.Users
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    user =>
                        user.Id ==
                            oldSupervisorId,
                    cancellationToken);

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var ideaTitle =
            project.ProjectIdea?.Title
            ?? "No official idea selected";

        var newSupervisorName =
            assignment.Supervisor.FullName;

        var memberNames =
            string.Join(
                ", ",
                recipients.Select(
                    recipient =>
                        recipient.FullName));

        foreach (var recipient in recipients)
        {
            await TryNotifyAsync(
                recipient.UserId,
                "Project Supervisor Changed",
                $"The supervisor for project "
                + $"\"{projectTitle}\" changed from "
                + $"{oldSupervisorName} to "
                + $"{newSupervisorName}. "
                + $"Reason: {reason}",
                "project_supervisor_transferred",
                $"/Student/Feedback"
                + $"?projectId={project.Id}",
                sendEmail:
                    true,
                emailSubject:
                    $"Supervisor changed for {projectTitle}",
                emailHtmlBody:
                    BuildMemberTransferEmail(
                        recipient.FullName,
                        projectTitle,
                        oldSupervisorName,
                        newSupervisorName,
                        ideaTitle,
                        reason),
                projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
        }

        if (oldSupervisor != null)
        {
            await TryNotifyAsync(
                oldSupervisorId,
                "Project Transferred",
                $"Project \"{projectTitle}\" was "
                + $"transferred to {newSupervisorName}. "
                + $"Reason: {reason}",
                "project_transferred_out",
                "/Supervisor/IdeaReview",
                sendEmail:
                    true,
                emailSubject:
                    $"Project transferred: {projectTitle}",
                emailHtmlBody:
                    BuildOldSupervisorTransferEmail(
                        oldSupervisor.FullName,
                        projectTitle,
                        newSupervisorName,
                        memberNames,
                        reason),
                projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
        }

        await TryNotifyAsync(
            assignment.SupervisorId,
            "Project Transferred To You",
            $"Project \"{projectTitle}\" with students "
            + $"{memberNames} was transferred to you.",
            "project_transferred_in",
            $"/Supervisor/IdeaReview"
            + $"?projectId={project.Id}",
            sendEmail:
                true,
            emailSubject:
                $"Project transferred to you: "
                + projectTitle,
            emailHtmlBody:
                BuildNewSupervisorTransferEmail(
                    newSupervisorName,
                    projectTitle,
                    oldSupervisorName,
                    ideaTitle,
                    memberNames,
                    reason),
            projectId:
                    project.Id,
                cancellationToken:
                    cancellationToken);
    }

    private async Task<List<ProjectRecipient>>
        LoadProjectRecipientsAsync(
            Project project,
            CancellationToken cancellationToken)
    {
        var recipients =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        project.Id &&
                    member.Status ==
                        "active")
                .Select(member =>
                    new ProjectRecipient(
                        member.UserId,
                        member.User != null
                            ? member.User.FullName
                            : "Student",
                        member.User != null
                            ? member.User.Email
                            : ""))
                .ToListAsync(
                    cancellationToken);

        if (!recipients.Any(
                recipient =>
                    recipient.UserId ==
                        project.StudentId))
        {
            var owner =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            project.StudentId)
                    .Select(user =>
                        new ProjectRecipient(
                            user.Id,
                            user.FullName,
                            user.Email))
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (owner != null)
            {
                recipients.Add(
                    owner);
            }
        }

        return recipients
            .GroupBy(recipient =>
                recipient.UserId)
            .Select(group =>
                group.First())
            .ToList();
    }

    private async Task TryNotifyAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url,
        bool sendEmail,
        string emailSubject,
        string emailHtmlBody,
        int? projectId,
        CancellationToken cancellationToken)
    {
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
                    sendEmail,

                emailSubject:
                    emailSubject,

                emailHtmlBody:
                    emailHtmlBody,

                projectId:
                    projectId,

                actorUserId:
                    GetCurrentUserId(),

                cancellationToken:
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Database action succeeded, but "
                + "notification failed for user "
                + "{UserId}.",
                recipientUserId);
        }
    }

    private static TeamSummary BuildTeamSummary(
        Project project,
        User? fallbackOwner)
    {
        var members =
            project.Members
                .Where(member =>
                    member.Status ==
                        "active" &&
                    member.User != null)
                .Select(member =>
                    new TeamMemberSummary(
                        member.UserId,
                        member.User!.FullName,
                        member.User.Email,
                        member.Role,
                        member.User.StudentProfile?.Major
                        ?? "",
                        member.User.StudentProfile
                            ?.PreferredDomain
                        ?? "",
                        member.User.StudentProfile?.Skills
                        ?? ""))
                .ToList();

        if (fallbackOwner != null &&
            members.All(member =>
                member.UserId !=
                    fallbackOwner.Id))
        {
            members.Add(
                new TeamMemberSummary(
                    fallbackOwner.Id,
                    fallbackOwner.FullName,
                    fallbackOwner.Email,
                    "owner",
                    fallbackOwner.StudentProfile?.Major
                    ?? "",
                    fallbackOwner.StudentProfile
                        ?.PreferredDomain
                    ?? "",
                    fallbackOwner.StudentProfile?.Skills
                    ?? ""));
        }

        var owner =
            members.FirstOrDefault(member =>
                member.Role.Equals(
                    "owner",
                    StringComparison.OrdinalIgnoreCase))
            ?? members.FirstOrDefault(member =>
                member.UserId ==
                    project.StudentId)
            ?? members.FirstOrDefault();

        return new TeamSummary(
            OwnerName:
                owner?.FullName
                ?? fallbackOwner?.FullName
                ?? "Project owner",

            OwnerEmail:
                owner?.Email
                ?? fallbackOwner?.Email
                ?? "",

            OwnerMajor:
                owner?.Major
                ?? "",

            PreferredDomain:
                string.Join(
                    ", ",
                    members
                        .Select(member =>
                            member.PreferredDomain)
                        .Where(value =>
                            !string.IsNullOrWhiteSpace(
                                value))
                        .Distinct(
                            StringComparer.OrdinalIgnoreCase)),

            TeamSkills:
                string.Join(
                    ", ",
                    members
                        .Select(member =>
                            member.Skills)
                        .Where(value =>
                            !string.IsNullOrWhiteSpace(
                                value))
                        .Distinct(
                            StringComparer.OrdinalIgnoreCase)),

            MemberNames:
                string.Join(
                    ", ",
                    members
                        .Select(member =>
                            member.FullName)
                        .Distinct(
                            StringComparer.OrdinalIgnoreCase)),

            MemberCount:
                members
                    .Select(member =>
                        member.UserId)
                    .Distinct()
                    .Count());
    }

    private static string BuildMemberApprovedEmail(
        string memberName,
        string projectTitle,
        string supervisorName,
        string ideaTitle,
        string memberNames)
    {
        return BuildEmail(
            "Project Supervisor Approved",
            memberName,
            $"""
            The official supervisor for project
            <strong>{Encode(projectTitle)}</strong>
            is now <strong>{Encode(supervisorName)}</strong>.
            """,
            $"""
            <strong>Official idea:</strong>
            {Encode(ideaTitle)}<br/>
            <strong>Project members:</strong>
            {Encode(memberNames)}
            """,
            "You can now open Supervisor Feedback, "
            + "communicate with the supervisor, and "
            + "receive project meeting updates.");
    }

    private static string BuildSupervisorApprovedEmail(
        string supervisorName,
        string projectTitle,
        string ideaTitle,
        string memberNames)
    {
        return BuildEmail(
            "New Project Assigned",
            supervisorName,
            $"""
            You are now the official supervisor for
            <strong>{Encode(projectTitle)}</strong>.
            """,
            $"""
            <strong>Official idea:</strong>
            {Encode(ideaTitle)}<br/>
            <strong>Students:</strong>
            {Encode(memberNames)}
            """,
            "You can now evaluate the shared project, "
            + "provide feedback, and create meetings "
            + "for all active project members.");
    }

    private static string BuildMemberRejectedEmail(
        string memberName,
        string projectTitle,
        string supervisorName,
        string adminNote)
    {
        return BuildEmail(
            "Supervisor Request Not Approved",
            memberName,
            $"""
            The request for
            <strong>{Encode(supervisorName)}</strong>
            for project
            <strong>{Encode(projectTitle)}</strong>
            was not approved.
            """,
            $"""
            <strong>Admin note:</strong>
            {Encode(adminNote)}
            """,
            "The project owner may choose another "
            + "available supervisor.");
    }

    private static string BuildMemberTransferEmail(
        string memberName,
        string projectTitle,
        string oldSupervisorName,
        string newSupervisorName,
        string ideaTitle,
        string reason)
    {
        return BuildEmail(
            "Project Supervisor Changed",
            memberName,
            $"""
            The supervisor for
            <strong>{Encode(projectTitle)}</strong>
            changed from
            <strong>{Encode(oldSupervisorName)}</strong>
            to
            <strong>{Encode(newSupervisorName)}</strong>.
            """,
            $"""
            <strong>Official idea:</strong>
            {Encode(ideaTitle)}<br/>
            <strong>Reason:</strong>
            {Encode(reason)}
            """,
            "All project members now share the new "
            + "official supervisor.");
    }

    private static string BuildOldSupervisorTransferEmail(
        string supervisorName,
        string projectTitle,
        string newSupervisorName,
        string memberNames,
        string reason)
    {
        return BuildEmail(
            "Project Transferred",
            supervisorName,
            $"""
            Project
            <strong>{Encode(projectTitle)}</strong>
            was transferred to
            <strong>{Encode(newSupervisorName)}</strong>.
            """,
            $"""
            <strong>Students:</strong>
            {Encode(memberNames)}<br/>
            <strong>Reason:</strong>
            {Encode(reason)}
            """,
            "You no longer have active supervision "
            + "access to this project.");
    }

    private static string BuildNewSupervisorTransferEmail(
        string supervisorName,
        string projectTitle,
        string oldSupervisorName,
        string ideaTitle,
        string memberNames,
        string reason)
    {
        return BuildEmail(
            "Project Transferred To You",
            supervisorName,
            $"""
            Project
            <strong>{Encode(projectTitle)}</strong>
            was transferred to your supervision.
            """,
            $"""
            <strong>Previous supervisor:</strong>
            {Encode(oldSupervisorName)}<br/>
            <strong>Official idea:</strong>
            {Encode(ideaTitle)}<br/>
            <strong>Students:</strong>
            {Encode(memberNames)}<br/>
            <strong>Reason:</strong>
            {Encode(reason)}
            """,
            "You can now evaluate the project and "
            + "schedule meetings for all active members.");
    }

    private static string BuildEmail(
        string heading,
        string recipientName,
        string mainHtml,
        string detailsHtml,
        string closingText)
    {
        return $"""
        <div style="font-family:Arial,sans-serif;
                    background:#F7F8FA;
                    padding:28px;">
            <div style="max-width:640px;
                        margin:auto;
                        background:#ffffff;
                        border:1px solid #E8ECF2;
                        border-radius:18px;
                        padding:26px;">
                <h2 style="color:#28385E;
                           margin-top:0;">
                    {Encode(heading)}
                </h2>

                <p style="color:#475569;
                          line-height:1.7;">
                    Hello {Encode(recipientName)},
                </p>

                <p style="color:#475569;
                          line-height:1.7;">
                    {mainHtml}
                </p>

                <div style="background:#F7F8FA;
                            border:1px solid #E8ECF2;
                            border-radius:14px;
                            padding:14px;
                            color:#28385E;
                            line-height:1.7;">
                    {detailsHtml}
                </div>

                <p style="color:#475569;
                          line-height:1.7;
                          margin-top:18px;">
                    {Encode(closingText)}
                </p>

                <p style="color:#94A3B8;
                          font-size:12px;
                          margin-top:24px;">
                    FYPilot automated notification
                </p>
            </div>
        </div>
        """;
    }

    private static string Encode(
        string? value)
    {
        return WebUtility.HtmlEncode(
            value ?? "");
    }

    private int GetCurrentUserId()
    {
        var userIdClaim =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                userIdClaim,
                out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException(
            "Unable to identify the current "
            + "logged-in administrator.");
    }

    private static string NormalizeStatus(
        string? status)
    {
        return string.IsNullOrWhiteSpace(
            status)
                ? "unassigned"
                : status.Trim()
                    .ToLowerInvariant();
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(
            title)
                ? "Untitled Project"
                : title.Trim();
    }

    private sealed record ProjectRecipient(
        int UserId,
        string FullName,
        string Email);

    private sealed record TeamMemberSummary(
        int UserId,
        string FullName,
        string Email,
        string Role,
        string Major,
        string PreferredDomain,
        string Skills);

    private sealed record TeamSummary(
        string OwnerName,
        string OwnerEmail,
        string OwnerMajor,
        string PreferredDomain,
        string TeamSkills,
        string MemberNames,
        int MemberCount);

    public class AdminAssignmentStats
    {
        public int PendingRequests { get; set; }

        public int ActiveAssignments { get; set; }

        public int FullSupervisors { get; set; }

        public int AvailableSupervisors { get; set; }
    }

    public class PendingAssignmentRow
    {
        public int AssignmentId { get; set; }

        public int ProjectId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string ProjectMembers { get; set; } = "";

        public int ProjectMemberCount { get; set; }

        public string OwnerName { get; set; } = "";

        public string OwnerEmail { get; set; } = "";

        /*
         * Legacy names retained temporarily so the
         * existing design continues compiling.
         */
        public int StudentId { get; set; }

        public string StudentName { get; set; } = "";

        public string StudentEmail { get; set; } = "";

        public string StudentMajor { get; set; } = "";

        public string PreferredDomain { get; set; } = "";

        public string StudentSkills { get; set; } = "";

        public string StudentIdeaTitle { get; set; } = "";

        public string StudentIdeaDomain { get; set; } = "";

        public int RequestedSupervisorId { get; set; }

        public string RequestedSupervisorName { get; set; } =
            "";

        public string RequestedSupervisorDepartment
        {
            get;
            set;
        } = "";

        public string RequestedSupervisorSpecialization
        {
            get;
            set;
        } = "";

        public int RequestedSupervisorCapacityUsed
        {
            get;
            set;
        }

        public int RequestedSupervisorCapacityLimit
        {
            get;
            set;
        }

        public bool RequestedSupervisorIsFull
        {
            get;
            set;
        }

        public string StudentMessage { get; set; } = "";

        public DateTime RequestedAt { get; set; }
    }

    public class SupervisorCapacityRow
    {
        public int SupervisorId { get; set; }

        public string FullName { get; set; } = "";

        public string Email { get; set; } = "";

        public string Department { get; set; } = "";

        public string Specialization { get; set; } = "";

        public string ResearchAreas { get; set; } = "";

        public int ActiveCount { get; set; }

        public int PendingCount { get; set; }

        public int CapacityLimit { get; set; }

        public bool IsFull { get; set; }
    }

    public class AssignableSupervisorOption
    {
        public int SupervisorId { get; set; }

        public string FullName { get; set; } = "";

        public string Department { get; set; } = "";

        public string Specialization { get; set; } = "";

        public int CapacityUsed { get; set; }

        public int CapacityLimit { get; set; }
    }

    public class ActiveAssignmentRow
    {
        public int AssignmentId { get; set; }

        public int ProjectId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string ProjectMembers { get; set; } = "";

        public int ProjectMemberCount { get; set; }

        /*
         * Legacy names retained temporarily for the
         * current Razor page.
         */
        public int StudentId { get; set; }

        public int SupervisorId { get; set; }

        public string StudentName { get; set; } = "";

        public string StudentEmail { get; set; } = "";

        public string StudentMajor { get; set; } = "";

        public string PreferredDomain { get; set; } = "";

        public string SupervisorName { get; set; } = "";

        public string SupervisorSpecialization
        {
            get;
            set;
        } = "";

        public string IdeaTitle { get; set; } = "";

        public DateTime ApprovedAt { get; set; }
    }

    public class RejectedAssignmentRow
    {
        public int AssignmentId { get; set; }

        public int ProjectId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string ProjectMembers { get; set; } = "";

        /*
         * Legacy name retained temporarily.
         */
        public string StudentName { get; set; } = "";

        public string SupervisorName { get; set; } = "";

        public string AdminNote { get; set; } = "";

        public DateTime RejectedAt { get; set; }
    }
}