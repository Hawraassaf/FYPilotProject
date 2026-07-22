using System.Data;
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
public class MyProjectsModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    ILogger<MyProjectsModel> logger) : PageModel
{
    public List<ProjectCardViewModel> Projects { get; private set; } = [];

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

        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        var currentUser = await db.Users
            .AsNoTracking()
            .Where(user => user.Id == userId.Value)
            .Select(user => new
            {
                user.LastActiveProjectId
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (currentUser == null)
        {
            return RedirectToPage("/Account/Login");
        }

        var projects = await db.Projects
            .AsNoTracking()
            .Where(project =>
                project.Members.Any(member =>
                    member.UserId == userId.Value &&
                    member.Status == "active"))
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
                    Math.Clamp(project.MaximumMembers, 1, 3);

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
            var selectedTeamSize = await db.StudentProfiles
               .AsNoTracking()
             .Where(profile =>
                profile.UserId == user.Id)
             .Select(profile =>
              (int?)profile.TeamMembers)
             .FirstOrDefaultAsync(cancellationToken);

            var projectTeamSize = Math.Clamp(
                selectedTeamSize ?? 1,
                1,
                3);
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
            CancellationToken cancellationToken)
    {
        var userId = CurrentUserId();

        if (userId == null)
        {
            return RedirectToPage("/Account/Login");
        }

        var destination =
    await activeProjectService
        .ActivateProjectAsync(
            userId.Value,
            projectId,
            requestedPage: "/Student/Dashboard",
            cancellationToken);

        if (destination == null)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage();
        }

        return RedirectToPage(
            destination.PageName,
            new
            {
                projectId = destination.ProjectId
            });
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

        if (access == null ||
            (!access.IsOwner &&
             !access.IsCollaborator))
        {
            TempData["Error"] =
                "You do not have permission "
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
                    item => item.Id == projectId,
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