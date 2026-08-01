using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DashboardModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    ILogger<DashboardModel> logger)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public string StudentName { get; private set; } =
        "Student";

    public Project? CurrentProject { get; private set; }

    public ProjectIdea? SelectedIdea { get; private set; }

    public ProjectAccessResult? ProjectAccess { get; private set; }

    public List<StatCard> Stats { get; private set; } = [];

    public List<RiskItem> RiskAlarms { get; private set; } = [];

    public List<MemberItem> Members { get; private set; } = [];

    public List<ActivityItem> RecentActivities { get; private set; } = [];

    public bool HasSkills { get; private set; }

    public bool HasSelectedIdea =>
        SelectedIdea != null;

    public bool HasRoadmaps { get; private set; }

    public int RoadmapCount { get; private set; }

    public int ActiveMembersCount =>
        Members.Count;

    /*
     * Project.MaximumMembers is the shared source of truth.
     *
     * Do not clamp it to 3 here because Team Management may
     * configure a larger project capacity, such as 4.
     */
    public int MaximumMembers =>
        Math.Max(
            CurrentProject?.MaximumMembers ?? 1,
            ActiveMembersCount);

    public string ProjectTitle =>
        string.IsNullOrWhiteSpace(CurrentProject?.Title)
            ? "Untitled Project"
            : CurrentProject.Title.Trim();

    public string ProjectStatus =>
        ToDisplayText(CurrentProject?.Status ?? "draft");

    public string AccessRoleLabel =>
        ToDisplayText(ProjectAccess?.AccessRole ?? "member");

    public record StatCard(
        string Label,
        string Value,
        string Icon,
        string Note);

    public record RiskItem(
        string Title,
        string Message,
        string Severity);

    public record MemberItem(
        int UserId,
        string FullName,
        string Initials,
        string Role);

    public record ActivityItem(
        string ActionType,
        string Description,
        DateTime CreatedAtUtc);

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }

        StudentName =
            User.FindFirst(ClaimTypes.Name)?.Value
            ?? "Student";

        /*
         * Use the projectId from the URL when it exists.
         * Otherwise, restore the student's active project
         * so sidebar navigation keeps the project context.
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
                    "Choose a project before opening its Dashboard.";

                return RedirectToPage("/Student/MyProjects");
            }

            ProjectId = activeProjectId.Value;
        }

        /*
         * Never trust projectId directly from the URL.
         * The centralized service verifies that the student is
         * an active owner or collaborator.
         */
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

            return RedirectToPage("/Student/MyProjects");
        }

        CurrentProject = await db.Projects
            .AsNoTracking()
            .Include(project => project.ProjectIdea)
            .Include(project => project.Members
                .Where(member =>
                    member.Status == "active"))
            .ThenInclude(member => member.User)
            .AsSplitQuery()
            .FirstOrDefaultAsync(
                project => project.Id == ProjectId,
                cancellationToken);

        if (CurrentProject == null)
        {
            TempData["Error"] =
                "The selected project could not be found.";

            return RedirectToPage("/Student/MyProjects");
        }

        SelectedIdea = CurrentProject.ProjectIdea;

        Members = CurrentProject.Members
            .Where(member =>
                member.Status == "active" &&
                member.User != null)
            .OrderBy(member =>
                Normalize(member.Role) == "owner"
                    ? 0
                    : 1)
            .ThenBy(member => member.JoinedAt)
            .Select(member =>
                new MemberItem(
                    UserId: member.UserId,
                    FullName: member.User!.FullName,
                    Initials: Initials(
                        member.User.FullName),
                    Role: ToDisplayText(
                        member.Role)))
            .ToList();

        var totalSkills = await db.StudentSkills
            .AsNoTracking()
            .CountAsync(
                skill => skill.UserId == userId.Value,
                cancellationToken);

        HasSkills = totalSkills > 0;

        /*
         * The roadmap check now follows the selected idea of this
         * project instead of counting every roadmap in the account.
         */
        if (SelectedIdea != null)
        {
            RoadmapCount = await db.ProjectRoadmaps
                .AsNoTracking()
                .CountAsync(
                    roadmap =>
                        roadmap.IdeaId == SelectedIdea.Id,
                    cancellationToken);
        }

        HasRoadmaps = RoadmapCount > 0;

        RecentActivities = await db.ProjectActivities
            .AsNoTracking()
            .Where(activity =>
                activity.ProjectId == ProjectId)
            .OrderByDescending(activity =>
                activity.CreatedAtUtc)
            .Take(6)
            .Select(activity =>
                new ActivityItem(
                    activity.ActionType,
                    activity.Description,
                    activity.CreatedAtUtc))
            .ToListAsync(cancellationToken);

        BuildStatistics(totalSkills);
        BuildRiskAlarms();

        /*
         * Remember that the student successfully opened this
         * project's Dashboard.
         */
        var remembered =
            await activeProjectService.RememberPageAsync(
                userId.Value,
                ProjectId,
                "/Student/Dashboard",
                cancellationToken);

        if (!remembered)
        {
            logger.LogWarning(
                "Could not remember Dashboard context for "
                + "user {UserId}, project {ProjectId}.",
                userId.Value,
                ProjectId);
        }

        return Page();
    }

    private void BuildStatistics(
        int totalSkills)
    {
        var availableTeamPlaces =
            Math.Max(
                MaximumMembers -
                ActiveMembersCount,
                0);

        Stats =
        [
            /*
             * These cards intentionally avoid repeating
             * readiness, selected-idea and roadmap values
             * that already appear elsewhere on Dashboard.
             */
            new StatCard(
                Label: "Assessed Skills",
                Value: totalSkills.ToString(),
                Icon: "tools",
                Note: totalSkills > 0
                    ? "Technical skills in your profile"
                    : "Complete your skill assessment"),

            new StatCard(
                Label: "Team Capacity",
                Value:
                    $"{ActiveMembersCount}/{MaximumMembers}",
                Icon: "people",
                Note: availableTeamPlaces == 0
                    ? "Project team is full"
                    : availableTeamPlaces == 1
                        ? "1 team place available"
                        : $"{availableTeamPlaces} team places available"),

            new StatCard(
                Label: "Your Project Role",
                Value: AccessRoleLabel,
                Icon: "person-badge",
                Note: string.Equals(
                    AccessRoleLabel,
                    "Owner",
                    StringComparison.OrdinalIgnoreCase)
                    ? "You manage this project workspace"
                    : "You collaborate in this project"),

            new StatCard(
                Label: "Recent Activity",
                Value:
                    RecentActivities.Count.ToString(),
                Icon: "clock-history",
                Note: RecentActivities.Count == 0
                    ? "No project events yet"
                    : "Latest recorded project events")
        ];
    }

    private void BuildRiskAlarms()
    {
        if (!HasSkills)
        {
            RiskAlarms.Add(
                new RiskItem(
                    Title: "No Skills Assessed",
                    Message:
                        "Complete your private skill assessment "
                        + "so generated ideas match your level.",
                    Severity: "high"));
        }

        if (!HasSelectedIdea)
        {
            RiskAlarms.Add(
                new RiskItem(
                    Title: "No Idea Selected",
                    Message:
                        "This project has no shared idea yet. "
                        + "Open the Idea Generator and select one.",
                    Severity: "medium"));

            return;
        }

        if (!HasRoadmaps)
        {
            RiskAlarms.Add(
                new RiskItem(
                    Title: "No Roadmap Created",
                    Message:
                        "Create a roadmap for this project's "
                        + "selected idea.",
                    Severity: "medium"));
        }

        if (SelectedIdea!.FeasibilityScore < 50)
        {
            RiskAlarms.Add(
                new RiskItem(
                    Title: "Feasibility Risk",
                    Message:
                        "The selected idea has a low feasibility "
                        + "score. Consider reducing its scope.",
                    Severity: "high"));
        }
    }

    private int? CurrentUserId()
    {
        var value = User.FindFirst(
            ClaimTypes.NameIdentifier)?.Value;

        return int.TryParse(value, out var userId)
            ? userId
            : null;
    }

    private static string Normalize(
        string? value)
    {
        return string.IsNullOrWhiteSpace(value)
            ? string.Empty
            : value.Trim().ToLowerInvariant();
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
}