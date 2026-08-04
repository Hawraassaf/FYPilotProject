using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class ProjectsModel(
    ApplicationDbContext db)
    : PageModel
{
    private static readonly string[] LifecycleActionTypes =
    [
        "project_archived",
        "project_restored",
        "member_removed",
        "project_removed",
        "ownership_transferred"
    ];

    public List<ProjectDirectoryRow> Projects
    { get; private set; } = [];

    public ProjectDirectoryStats Stats
    { get; private set; } = new();

    public List<ProjectStatusOption> AvailableStatuses
    { get; private set; } = [];

    public async Task OnGetAsync(
        CancellationToken cancellationToken)
    {
        /*
         * Stable display order:
         * the oldest created project is Project #1,
         * followed by Project #2, Project #3, and so on.
         *
         * The displayed number is intentionally separate
         * from the database ID, so database ID gaps are not
         * shown to the administrator.
         *
         * Do not filter IsDeleted here. Administrators need
         * oversight of both active and soft-deleted projects.
         */
        var entities = await db.Projects
            .AsNoTracking()
            .Include(project =>
                project.Student)
            .Include(project =>
                project.DeletedByUser)
            .Include(project =>
                project.ProjectIdea)
            .Include(project =>
                project.Members)
                .ThenInclude(member =>
                    member.User)
            .OrderBy(project =>
                project.CreatedAt)
            .ThenBy(project =>
                project.Id)
            .AsSplitQuery()
            .ToListAsync(
                cancellationToken);

        var projectIds = entities
            .Select(project => project.Id)
            .ToList();

        /*
         * Lifecycle activities are loaded separately so the
         * project query does not pull every historical activity.
         * These records preserve archive, restore, removal, and
         * ownership-transfer history for administrator review.
         */
        List<ProjectActivity> lifecycleActivities =
            projectIds.Count == 0
                ? []
                : await db.ProjectActivities
                    .AsNoTracking()
                    .Where(activity =>
                        projectIds.Contains(activity.ProjectId) &&
                        LifecycleActionTypes.Contains(
                            activity.ActionType))
                    .OrderByDescending(activity =>
                        activity.CreatedAtUtc)
                    .ThenByDescending(activity =>
                        activity.Id)
                    .ToListAsync(
                        cancellationToken);

        var lifecycleActivitiesByProject =
            lifecycleActivities
                .GroupBy(activity => activity.ProjectId)
                .ToDictionary(
                    group => group.Key,
                    group => group
                        .Take(10)
                        .Select(BuildLifecycleActivityRow)
                        .ToList());

        var supervisorIds = entities
            .Where(project =>
                project.SupervisorId.HasValue)
            .Select(project =>
                project.SupervisorId!.Value)
            .Distinct()
            .ToList();

        var supervisors =
            supervisorIds.Count == 0
                ? new Dictionary<int, SupervisorSummary>()
                : await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        supervisorIds.Contains(user.Id))
                    .Select(user =>
                        new SupervisorSummary(
                            user.Id,
                            user.FullName,
                            user.Email))
                    .ToDictionaryAsync(
                        item => item.Id,
                        cancellationToken);

        Projects = entities
            .Select((project, index) =>
            {
                /*
                 * Membership status is independent from personal
                 * archive state:
                 * - Status active + IsArchived true: still a member,
                 *   but their own workspace is read-only.
                 * - Status removed: former member retained as history.
                 */
                var memberships = project.Members
                    .Where(member => member.User != null)
                    .GroupBy(member => member.UserId)
                    .Select(group => group
                        .OrderByDescending(member => member.JoinedAt)
                        .ThenByDescending(member => member.Id)
                        .First())
                    .ToList();

                var activeMemberships = memberships
                    .Where(member =>
                        IsActiveMembership(member.Status))
                    .ToList();

                var activeOwnerMembership =
                    activeMemberships.FirstOrDefault(member =>
                        member.UserId == project.StudentId);

                var removedOwnerMembership =
                    memberships.FirstOrDefault(member =>
                        member.UserId == project.StudentId &&
                        IsRemovedMembership(member));

                var collaborators = activeMemberships
                    .Where(member =>
                        member.UserId != project.StudentId)
                    .Select(BuildMemberRow)
                    .OrderBy(member =>
                        member.FullName)
                    .ToList();

                var archivedMembers = activeMemberships
                    .Where(member => member.IsArchived)
                    .Select(BuildMemberRow)
                    .OrderBy(member =>
                        member.ArchivedAtUtc ?? DateTime.MaxValue)
                    .ThenBy(member =>
                        member.FullName)
                    .ToList();

                var removedMembers = memberships
                    .Where(IsRemovedMembership)
                    .Select(BuildMemberRow)
                    .OrderByDescending(member =>
                        member.RemovedAtUtc ?? DateTime.MinValue)
                    .ThenBy(member =>
                        member.FullName)
                    .ToList();

                List<ProjectLifecycleActivityRow> projectLifecycleActivities =
                    lifecycleActivitiesByProject.TryGetValue(
                        project.Id,
                        out var activityRows)
                        ? activityRows
                        : [];

                var hasOwnershipTransfer =
                    projectLifecycleActivities.Any(activity =>
                        activity.ActionType ==
                            "ownership_transferred");

                var lifecycleFilters =
                    BuildLifecycleFilters(
                        project.IsDeleted,
                        archivedMembers.Count > 0,
                        removedMembers.Count > 0,
                        hasOwnershipTransfer);

                var status =
                    NormalizeProjectStatus(
                        project.Status);

                var supervisorStatus =
                    NormalizeSupervisorStatus(
                        project
                            .SupervisorAssignmentStatus);

                SupervisorSummary? supervisor =
                    null;

                if (project.SupervisorId.HasValue)
                {
                    supervisors.TryGetValue(
                        project.SupervisorId.Value,
                        out supervisor);
                }

                var hasActiveSupervisor =
                    !project.IsDeleted &&
                    supervisorStatus == "active" &&
                    supervisor != null;

                var ownerName =
                    SafeText(
                        project.Student?.FullName,
                        "Project owner");

                var ownerEmail =
                    project.Student?.Email ?? "";

                var selectedIdeaTitle =
                    project.ProjectIdea?.Title
                    ?? "No official idea selected";

                var domain =
                    SafeText(
                        project.ProjectIdea?.Domain,
                        "Domain not specified");

                var supervisorName =
                    hasActiveSupervisor
                        ? SafeText(
                            supervisor!.FullName,
                            "Assigned supervisor")
                        : project.IsDeleted
                            ? "Assignment preserved as history"
                            : SupervisorPlaceholder(
                                supervisorStatus);

                var supervisorEmail =
                    hasActiveSupervisor
                        ? supervisor!.Email ?? ""
                        : "";

                var deletedByName =
                    project.IsDeleted
                        ? SafeText(
                            project.DeletedByUser?.FullName,
                            ownerName)
                        : "";

                var searchParts =
                    new List<string>
                    {
                        project.Title ?? "",
                        project.Description ?? "",
                        selectedIdeaTitle,
                        domain,
                        ownerName,
                        ownerEmail,
                        supervisorName,
                        supervisorEmail,
                        DisplayStatus(status),
                        DisplaySupervisorStatus(
                            supervisorStatus),
                        project.IsDeleted
                            ? "soft deleted read only historical"
                            : "active workspace",
                        deletedByName,
                        hasOwnershipTransfer
                            ? "ownership transferred"
                            : ""
                    };

                foreach (var member in collaborators)
                {
                    searchParts.Add(member.FullName);
                    searchParts.Add(member.Email);
                }

                foreach (var member in archivedMembers)
                {
                    searchParts.Add(member.FullName);
                    searchParts.Add(member.Email);
                    searchParts.Add("archived workspace");
                }

                foreach (var member in removedMembers)
                {
                    searchParts.Add(member.FullName);
                    searchParts.Add(member.Email);
                    searchParts.Add("removed member");
                }

                foreach (var activity
                    in projectLifecycleActivities)
                {
                    searchParts.Add(activity.Label);
                    searchParts.Add(activity.Description);
                }

                return new ProjectDirectoryRow
                {
                    ProjectId =
                        project.Id,

                    DisplayNumber =
                        index + 1,

                    Title =
                        SafeText(
                            project.Title,
                            "Untitled Project"),

                    Description =
                        SafeText(
                            project.Description,
                            "No project description is available."),

                    Status =
                        status,

                    StatusLabel =
                        DisplayStatus(status),

                    StatusCssClass =
                        StatusCss(status),

                    IsDeleted =
                        project.IsDeleted,

                    DeletedAtUtc =
                        project.DeletedAtUtc,

                    DeletedByName =
                        deletedByName,

                    LifecycleFilterValue =
                        string.Join(" ", lifecycleFilters),

                    HasOwnershipTransfer =
                        hasOwnershipTransfer,

                    OwnerName =
                        ownerName,

                    OwnerEmail =
                        ownerEmail,

                    OwnerInitials =
                        Initials(ownerName),

                    OwnerMembershipIsActive =
                        activeOwnerMembership != null,

                    OwnerIsArchived =
                        activeOwnerMembership?.IsArchived == true,

                    OwnerArchivedAtUtc =
                        activeOwnerMembership?.ArchivedAtUtc,

                    OwnerRemovedAtUtc =
                        removedOwnerMembership?.RemovedAtUtc,

                    Collaborators =
                        collaborators,

                    ArchivedMembers =
                        archivedMembers,

                    RemovedMembers =
                        removedMembers,

                    ActiveMemberCount =
                        activeMemberships.Count,

                    MaximumMembers =
                        Math.Max(
                            project.MaximumMembers,
                            1),

                    HasSelectedIdea =
                        project.ProjectIdeaId.HasValue &&
                        project.ProjectIdea != null,

                    SelectedIdeaTitle =
                        selectedIdeaTitle,

                    Domain =
                        domain,

                    SupervisorName =
                        supervisorName,

                    SupervisorEmail =
                        supervisorEmail,

                    SupervisorStatus =
                        supervisorStatus,

                    SupervisorStatusLabel =
                        DisplaySupervisorStatus(
                            supervisorStatus),

                    SupervisorStatusCssClass =
                        SupervisorStatusCss(
                            supervisorStatus),

                    HasActiveSupervisor =
                        hasActiveSupervisor,

                    LifecycleActivities =
                        projectLifecycleActivities,

                    SearchText =
                        string.Join(
                            " ",
                            searchParts)
                        .ToLowerInvariant()
                };
            })
            .ToList();

        AvailableStatuses = Projects
            .GroupBy(
                project => project.Status,
                StringComparer.OrdinalIgnoreCase)
            .Select(group =>
                new ProjectStatusOption
                {
                    Value =
                        group.Key,

                    Label =
                        group.First().StatusLabel
                })
            .OrderBy(option =>
                option.Label)
            .ToList();

        Stats = new ProjectDirectoryStats
        {
            TotalProjects =
                Projects.Count,

            ActiveProjects =
                Projects.Count(project =>
                    !project.IsDeleted),

            WithSelectedIdea =
                Projects.Count(project =>
                    !project.IsDeleted &&
                    project.HasSelectedIdea),

            CollaborativeProjects =
                Projects.Count(project =>
                    !project.IsDeleted &&
                    project.Collaborators.Count > 0),

            WithAssignedSupervisor =
                Projects.Count(project =>
                    !project.IsDeleted &&
                    project.HasActiveSupervisor),

            WithoutAssignedSupervisor =
                Projects.Count(project =>
                    !project.IsDeleted &&
                    !project.HasActiveSupervisor),

            ArchivedMemberships =
                Projects
                    .Where(project =>
                        !project.IsDeleted)
                    .Sum(project =>
                        project.ArchivedMembers.Count),

            RemovedMemberships =
                Projects.Sum(project =>
                    project.RemovedMembers.Count),

            SoftDeletedProjects =
                Projects.Count(project =>
                    project.IsDeleted),

            OwnershipTransferProjects =
                Projects.Count(project =>
                    project.HasOwnershipTransfer)
        };
    }

    private static ProjectMemberRow BuildMemberRow(
        ProjectMember member)
    {
        var role =
            NormalizeMembershipRole(member.Role);

        return new ProjectMemberRow
        {
            UserId =
                member.UserId,

            FullName =
                SafeText(
                    member.User?.FullName,
                    role == "owner"
                        ? "Project owner"
                        : "Collaborator"),

            Email =
                member.User?.Email ?? "",

            Initials =
                Initials(
                    member.User?.FullName),

            Role =
                role,

            RoleLabel =
                DisplayMembershipRole(role),

            Status =
                NormalizeMembershipStatus(
                    member.Status),

            IsArchived =
                member.IsArchived,

            ArchivedAtUtc =
                member.ArchivedAtUtc,

            RemovedAtUtc =
                member.RemovedAtUtc
                    ?? member.LeftAt
        };
    }

    private static ProjectLifecycleActivityRow
        BuildLifecycleActivityRow(
            ProjectActivity activity)
    {
        var actionType =
            NormalizeLifecycleAction(
                activity.ActionType);

        return new ProjectLifecycleActivityRow
        {
            ActionType =
                actionType,

            Label =
                DisplayLifecycleAction(actionType),

            Description =
                SafeText(
                    activity.Description,
                    "Project lifecycle updated."),

            CreatedAtUtc =
                activity.CreatedAtUtc,

            CssClass =
                LifecycleActivityCss(actionType),

            IconCssClass =
                LifecycleActivityIcon(actionType)
        };
    }

    private static List<string> BuildLifecycleFilters(
        bool isDeleted,
        bool hasArchivedMembers,
        bool hasRemovedMembers,
        bool hasOwnershipTransfer)
    {
        var filters = new List<string>
        {
            isDeleted
                ? "soft_deleted"
                : "active"
        };

        if (hasArchivedMembers)
        {
            filters.Add("archived");
        }

        if (hasRemovedMembers)
        {
            filters.Add("removed");
        }

        if (hasOwnershipTransfer)
        {
            filters.Add("transferred");
        }

        return filters;
    }

    private static bool IsActiveMembership(
        string? status)
    {
        return string.Equals(
            NormalizeMembershipStatus(status),
            "active",
            StringComparison.Ordinal);
    }

    private static bool IsRemovedMembership(
        ProjectMember member)
    {
        return member.RemovedAtUtc.HasValue ||
               string.Equals(
                   NormalizeMembershipStatus(member.Status),
                   "removed",
                   StringComparison.Ordinal);
    }

    private static string NormalizeProjectStatus(
        string? status)
    {
        var value = SafeText(
                status,
                "draft")
            .ToLowerInvariant()
            .Replace(" ", "_");

        return value switch
        {
            "inprogress" => "in_progress",
            _ => value
        };
    }

    private static string NormalizeSupervisorStatus(
        string? status)
    {
        var value = SafeText(
                status,
                "unassigned")
            .ToLowerInvariant()
            .Replace(" ", "_");

        return value switch
        {
            "pending" => "pending_admin",
            _ => value
        };
    }

    private static string NormalizeMembershipRole(
        string? role)
    {
        var value = SafeText(
                role,
                "collaborator")
            .ToLowerInvariant()
            .Replace(" ", "_");

        return value == "owner"
            ? "owner"
            : "collaborator";
    }

    private static string NormalizeMembershipStatus(
        string? status)
    {
        return SafeText(
                status,
                "removed")
            .ToLowerInvariant()
            .Replace(" ", "_");
    }

    private static string NormalizeLifecycleAction(
        string? actionType)
    {
        return SafeText(
                actionType,
                "lifecycle_updated")
            .ToLowerInvariant()
            .Replace(" ", "_");
    }

    private static string DisplayStatus(
        string status)
    {
        return status switch
        {
            "draft" => "Draft",
            "planning" => "Planning",
            "active" => "Active",
            "in_progress" => "In Progress",
            "completed" => "Completed",
            "archived" => "Archived",
            _ => ToTitleCase(status)
        };
    }

    private static string StatusCss(
        string status)
    {
        return status switch
        {
            "active" => "is-active",
            "in_progress" => "is-progress",
            "completed" => "is-complete",
            "planning" => "is-planning",
            "archived" => "is-archived",
            _ => "is-draft"
        };
    }

    private static string DisplayMembershipRole(
        string role)
    {
        return role == "owner"
            ? "Owner"
            : "Collaborator";
    }

    private static string DisplayLifecycleAction(
        string actionType)
    {
        return actionType switch
        {
            "project_archived" =>
                "Workspace archived",
            "project_restored" =>
                "Workspace restored",
            "member_removed" =>
                "Member removed project",
            "project_removed" =>
                "Project soft-deleted",
            "ownership_transferred" =>
                "Ownership transferred",
            _ =>
                "Lifecycle updated"
        };
    }

    private static string LifecycleActivityCss(
        string actionType)
    {
        return actionType switch
        {
            "project_archived" => "is-archived",
            "project_restored" => "is-restored",
            "member_removed" => "is-removed",
            "project_removed" => "is-deleted",
            "ownership_transferred" => "is-transfer",
            _ => "is-neutral"
        };
    }

    private static string LifecycleActivityIcon(
        string actionType)
    {
        return actionType switch
        {
            "project_archived" => "bi-archive",
            "project_restored" => "bi-arrow-counterclockwise",
            "member_removed" => "bi-person-x",
            "project_removed" => "bi-trash3",
            "ownership_transferred" => "bi-arrow-left-right",
            _ => "bi-clock-history"
        };
    }

    private static string DisplaySupervisorStatus(
        string status)
    {
        return status switch
        {
            "active" => "Assigned",
            "pending_admin" =>
                "Pending Admin Approval",
            "requested" =>
                "Requested",
            "rejected" =>
                "Not Assigned",
            "transferred" =>
                "Transferred",
            _ =>
                "Not Assigned"
        };
    }

    private static string SupervisorStatusCss(
        string status)
    {
        return status switch
        {
            "active" => "is-assigned",
            "pending_admin" => "is-pending",
            "requested" => "is-pending",
            _ => "is-unassigned"
        };
    }

    private static string SupervisorPlaceholder(
        string status)
    {
        return status switch
        {
            "pending_admin" =>
                "Waiting for admin approval",
            "requested" =>
                "Supervisor requested",
            _ =>
                "No supervisor assigned"
        };
    }

    private static string SafeText(
        string? value,
        string fallback)
    {
        return string.IsNullOrWhiteSpace(value)
            ? fallback
            : value.Trim();
    }

    private static string ToTitleCase(
        string value)
    {
        return string.Join(
            " ",
            value
                .Split(
                    '_',
                    StringSplitOptions
                        .RemoveEmptyEntries)
                .Select(word =>
                    char.ToUpperInvariant(
                        word[0]) +
                    word[1..]));
    }

    private static string Initials(
        string? fullName)
    {
        var parts = (fullName ?? "")
            .Split(
                ' ',
                StringSplitOptions
                    .RemoveEmptyEntries);

        if (parts.Length == 0)
        {
            return "?";
        }

        if (parts.Length == 1)
        {
            return parts[0][0]
                .ToString()
                .ToUpperInvariant();
        }

        return string.Concat(
                parts[0][0],
                parts[^1][0])
            .ToUpperInvariant();
    }

    private sealed record SupervisorSummary(
        int Id,
        string? FullName,
        string? Email);

    public sealed class ProjectDirectoryStats
    {
        public int TotalProjects { get; set; }

        public int ActiveProjects { get; set; }

        public int WithSelectedIdea { get; set; }

        public int CollaborativeProjects { get; set; }

        public int WithAssignedSupervisor { get; set; }

        public int WithoutAssignedSupervisor { get; set; }

        public int ArchivedMemberships { get; set; }

        public int RemovedMemberships { get; set; }

        public int SoftDeletedProjects { get; set; }

        public int OwnershipTransferProjects { get; set; }
    }

    public sealed class ProjectStatusOption
    {
        public string Value { get; set; } = "";

        public string Label { get; set; } = "";
    }

    public sealed class ProjectDirectoryRow
    {
        public int ProjectId { get; set; }

        public int DisplayNumber { get; set; }

        public string Title { get; set; } = "";

        public string Description { get; set; } = "";

        public string Status { get; set; } = "";

        public string StatusLabel { get; set; } = "";

        public string StatusCssClass { get; set; } = "";

        public bool IsDeleted { get; set; }

        public DateTime? DeletedAtUtc { get; set; }

        public string DeletedByName { get; set; } = "";

        public string LifecycleFilterValue { get; set; } = "";

        public bool HasOwnershipTransfer { get; set; }

        public string OwnerName { get; set; } = "";

        public string OwnerEmail { get; set; } = "";

        public string OwnerInitials { get; set; } = "";

        public bool OwnerMembershipIsActive { get; set; }

        public bool OwnerIsArchived { get; set; }

        public DateTime? OwnerArchivedAtUtc { get; set; }

        public DateTime? OwnerRemovedAtUtc { get; set; }

        public List<ProjectMemberRow> Collaborators
        { get; set; } = [];

        public List<ProjectMemberRow> ArchivedMembers
        { get; set; } = [];

        public List<ProjectMemberRow> RemovedMembers
        { get; set; } = [];

        public int ActiveMemberCount { get; set; }

        public int MaximumMembers { get; set; }

        public bool HasSelectedIdea { get; set; }

        public string SelectedIdeaTitle
        { get; set; } = "";

        public string Domain { get; set; } = "";

        public string SupervisorName
        { get; set; } = "";

        public string SupervisorEmail
        { get; set; } = "";

        public string SupervisorStatus
        { get; set; } = "";

        public string SupervisorStatusLabel
        { get; set; } = "";

        public string SupervisorStatusCssClass
        { get; set; } = "";

        public bool HasActiveSupervisor
        { get; set; }

        public List<ProjectLifecycleActivityRow>
            LifecycleActivities
        { get; set; } = [];

        public string SearchText
        { get; set; } = "";
    }

    public sealed class ProjectMemberRow
    {
        public int UserId { get; set; }

        public string FullName { get; set; } = "";

        public string Email { get; set; } = "";

        public string Initials { get; set; } = "";

        public string Role { get; set; } = "";

        public string RoleLabel { get; set; } = "";

        public string Status { get; set; } = "";

        public bool IsArchived { get; set; }

        public DateTime? ArchivedAtUtc { get; set; }

        public DateTime? RemovedAtUtc { get; set; }
    }

    public sealed class ProjectLifecycleActivityRow
    {
        public string ActionType { get; set; } = "";

        public string Label { get; set; } = "";

        public string Description { get; set; } = "";

        public DateTime CreatedAtUtc { get; set; }

        public string CssClass { get; set; } = "";

        public string IconCssClass { get; set; } = "";
    }
}