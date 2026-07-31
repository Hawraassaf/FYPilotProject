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
         */
        var entities = await db.Projects
            .AsNoTracking()
            .Include(project =>
                project.Student)
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
                var collaborators = project.Members
                    .Where(member =>
                        member.UserId !=
                            project.StudentId &&
                        string.Equals(
                            member.Status,
                            "active",
                            StringComparison.OrdinalIgnoreCase) &&
                        member.User != null)
                    .GroupBy(member =>
                        member.UserId)
                    .Select(group =>
                    {
                        var member = group.First();

                        return new ProjectMemberRow
                        {
                            UserId = member.UserId,

                            FullName =
                                SafeText(
                                    member.User?.FullName,
                                    "Collaborator"),

                            Email =
                                member.User?.Email ?? "",

                            Initials =
                                Initials(
                                    member.User?.FullName)
                        };
                    })
                    .OrderBy(member =>
                        member.FullName)
                    .ToList();

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
                        : SupervisorPlaceholder(
                            supervisorStatus);

                var supervisorEmail =
                    hasActiveSupervisor
                        ? supervisor!.Email ?? ""
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
                            supervisorStatus)
                    };

                foreach (var collaborator
                    in collaborators)
                {
                    searchParts.Add(
                        collaborator.FullName);

                    searchParts.Add(
                        collaborator.Email);
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

                    OwnerName =
                        ownerName,

                    OwnerEmail =
                        ownerEmail,

                    OwnerInitials =
                        Initials(ownerName),

                    Collaborators =
                        collaborators,

                    ActiveMemberCount =
                        collaborators.Count + 1,

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

            WithSelectedIdea =
                Projects.Count(project =>
                    project.HasSelectedIdea),

            CollaborativeProjects =
                Projects.Count(project =>
                    project.Collaborators.Count > 0),

            WithAssignedSupervisor =
                Projects.Count(project =>
                    project.HasActiveSupervisor),

            WithoutAssignedSupervisor =
                Projects.Count(project =>
                    !project.HasActiveSupervisor)
        };
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

        public int WithSelectedIdea { get; set; }

        public int CollaborativeProjects { get; set; }

        public int WithAssignedSupervisor { get; set; }

        public int WithoutAssignedSupervisor { get; set; }
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

        public string OwnerName { get; set; } = "";

        public string OwnerEmail { get; set; } = "";

        public string OwnerInitials { get; set; } = "";

        public List<ProjectMemberRow> Collaborators
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

        public string SearchText
        { get; set; } = "";
    }

    public sealed class ProjectMemberRow
    {
        public int UserId { get; set; }

        public string FullName { get; set; } = "";

        public string Email { get; set; } = "";

        public string Initials { get; set; } = "";
    }
}