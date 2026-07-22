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

    public int MaximumMembers =>
    Math.Clamp(
        CurrentProject?.MaximumMembers ?? 1,
        1,
        3);

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
         * A Dashboard without a project context is not allowed.
         * The student must choose a project from My Projects.
         */
        if (ProjectId <= 0)
        {
            TempData["Error"] =
                "Choose a project before opening its Dashboard.";

            return RedirectToPage("/Student/MyProjects");
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
        var progress =
            Math.Clamp(
                CurrentProject?.ProgressPercentage ?? 0,
                0,
                100);

        Stats =
        [
            new StatCard(
                Label: "Project Progress",
                Value: $"{progress}%",
                Icon: "graph-up-arrow",
                Note: "Progress for this project"),

            new StatCard(
                Label: "Team Members",
                Value:
                    $"{ActiveMembersCount}/{MaximumMembers}",
                Icon: "people",
                Note: "Active project members"),

            new StatCard(
                Label: "Selected Idea",
                Value: HasSelectedIdea ? "1" : "0",
                Icon: "lightbulb",
                Note: HasSelectedIdea
                    ? "Shared project direction"
                    : "No idea selected yet"),

            new StatCard(
                Label: "Roadmaps",
                Value: RoadmapCount.ToString(),
                Icon: "map",
                Note: HasRoadmaps
                    ? "Planning has started"
                    : "No roadmap created")
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