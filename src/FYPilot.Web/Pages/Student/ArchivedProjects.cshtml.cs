using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Projects;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ArchivedProjectsModel(
    ApplicationDbContext db,
    IProjectLifecycleService projectLifecycleService)
    : PageModel
{
    private const int MaximumCollaborators = 20;

    private const int MaximumProjectMembers =
        MaximumCollaborators + 1;

    public List<ArchivedProjectCardViewModel> Projects
    {
        get;
        private set;
    } = [];

    public string? SuccessMessage
    {
        get;
        private set;
    }

    public string? ErrorMessage
    {
        get;
        private set;
    }

    public int TotalArchivedProjects =>
        Projects.Count;

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

        /*
         * Load projects as the query root instead of loading a
         * ProjectMember and including Project.Members back through it.
         * The previous no-tracking query created the cycle
         * ProjectMember -> Project -> Members and EF Core rejected it.
         */
        var archivedProjects =
            await db.Projects
                .AsNoTracking()
                .Where(project =>
                    !project.IsDeleted &&
                    project.Members.Any(member =>
                        member.UserId == userId.Value &&
                        member.Status == "active" &&
                        member.IsArchived))
                .Include(project =>
                    project.ProjectIdea)
                .Include(project =>
                    project.Student)
                .Include(project =>
                    project.Supervisor)
                .Include(project =>
                    project.Members
                        .Where(projectMember =>
                            projectMember.Status ==
                            "active"))
                .ThenInclude(projectMember =>
                    projectMember.User)
                .AsSplitQuery()
                .ToListAsync(
                    cancellationToken);

        Projects = archivedProjects
            .Select(project =>
            {
                var archivedMembership =
                    project.Members.First(member =>
                        member.UserId == userId.Value &&
                        member.Status == "active" &&
                        member.IsArchived);

                var hasSelectedIdea =
                    project.ProjectIdeaId.HasValue &&
                    project.ProjectIdea != null;

                var isOwner =
                    project.StudentId ==
                    userId.Value;

                var role =
                    isOwner
                        ? "owner"
                        : "collaborator";

                var domain =
                    hasSelectedIdea &&
                    !string.IsNullOrWhiteSpace(
                        project.ProjectIdea!.Domain)
                        ? project.ProjectIdea.Domain.Trim()
                        : "Idea not selected";

                var description =
                    !string.IsNullOrWhiteSpace(
                        project.Description)
                        ? project.Description.Trim()
                        : hasSelectedIdea
                            ? project.ProjectIdea!.WhyUseful
                            : "Create or select an idea "
                              + "to begin building "
                              + "this project.";

                var activeMembers =
                    project.Members
                        .Where(projectMember =>
                            projectMember.Status ==
                                "active" &&
                            projectMember.User != null)
                        .OrderBy(projectMember =>
                            projectMember.UserId ==
                                project.StudentId
                                ? 0
                                : 1)
                        .ThenBy(projectMember =>
                            projectMember.JoinedAt)
                        .Select(projectMember =>
                            new ArchivedMemberViewModel(
                                UserId:
                                    projectMember.UserId,
                                FullName:
                                    projectMember.User!
                                        .FullName,
                                Initials:
                                    Initials(
                                        projectMember.User
                                            .FullName),
                                Role:
                                    projectMember.UserId ==
                                        project.StudentId
                                        ? "owner"
                                        : "collaborator"))
                        .ToList();

                return new ArchivedProjectCardViewModel(
                    Id:
                        project.Id,

                    Title:
                        SafeProjectTitle(
                            project.Title),

                    SelectedIdeaTitle:
                        hasSelectedIdea
                            ? project.ProjectIdea!.Title
                            : "No idea selected",

                    HasSelectedIdea:
                        hasSelectedIdea,

                    Domain:
                        domain,

                    Description:
                        Shorten(
                            description,
                            180),

                    Role:
                        role,

                    RoleLabel:
                        role == "owner"
                            ? "Owner"
                            : "Collaborator",

                    IsOwner:
                        isOwner,

                    OwnerName:
                        SafeName(
                            project.Student?.FullName),

                    SupervisorName:
                        project.Supervisor == null
                            ? "Not assigned"
                            : SafeName(
                                project.Supervisor
                                    .FullName),

                    Status:
                        NormalizeStatus(
                            project.Status),

                    StatusLabel:
                        ToDisplayText(
                            project.Status),

                    ProgressPercentage:
                        Math.Clamp(
                            project.ProgressPercentage,
                            0,
                            100),

                    ActiveMembersCount:
                        activeMembers.Count,

                    MaximumMembers:
                        Math.Clamp(
                            project.MaximumMembers,
                            1,
                            MaximumProjectMembers),

                    Members:
                        activeMembers,

                    ArchivedAtUtc:
                        archivedMembership.ArchivedAtUtc,

                    CreatedAt:
                        project.CreatedAt,

                    UpdatedAt:
                        project.UpdatedAt);
            })
            .OrderByDescending(project =>
                project.ArchivedAtUtc)
            .ThenByDescending(project =>
                project.Id)
            .ToList();

        return Page();
    }

    public async Task<IActionResult>
        OnPostRestoreAsync(
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
            await projectLifecycleService
                .RestoreAsync(
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
                StatusCode =
                    StatusCodes.Status401Unauthorized
            };
        }

        var preview =
            await projectLifecycleService
                .GetRemovalPreviewAsync(
                    projectId,
                    userId.Value,
                    cancellationToken);

        return new JsonResult(
            preview);
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
            await projectLifecycleService
                .RemoveAsync(
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

    private int? CurrentUserId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        return int.TryParse(
            value,
            out var userId)
                ? userId
                : null;
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(
            title)
                ? "Untitled Project"
                : title.Trim();
    }

    private static string SafeName(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "Not available"
                : value.Trim();
    }

    private static string Shorten(
        string? value,
        int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(
                value))
        {
            return "A collaborative final year "
                   + "project workspace.";
        }

        var clean =
            value.Trim();

        return clean.Length <= maximumLength
            ? clean
            : $"{clean[..maximumLength].TrimEnd()}…";
    }

    private static string Initials(
        string? fullName)
    {
        if (string.IsNullOrWhiteSpace(
                fullName))
        {
            return "ST";
        }

        var parts =
            fullName.Split(
                    ' ',
                    StringSplitOptions
                        .RemoveEmptyEntries)
                .Take(2)
                .ToList();

        return parts.Count == 0
            ? "ST"
            : string.Join(
                    "",
                    parts.Select(part =>
                        part[0]))
                .ToUpperInvariant();
    }

    private static string NormalizeStatus(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "draft"
                : value.Trim()
                    .ToLowerInvariant()
                    .Replace(" ", "_")
                    .Replace("-", "_");
    }

    private static string ToDisplayText(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(
                value))
        {
            return "Draft";
        }

        var words =
            value.Trim()
                .Replace("_", " ")
                .Replace("-", " ")
                .Split(
                    ' ',
                    StringSplitOptions
                        .RemoveEmptyEntries);

        return string.Join(
            " ",
            words.Select(word =>
                word.Length == 1
                    ? word.ToUpperInvariant()
                    : char.ToUpperInvariant(
                        word[0])
                      + word[1..]
                          .ToLowerInvariant()));
    }

    public sealed record ArchivedProjectCardViewModel(
        int Id,
        string Title,
        string SelectedIdeaTitle,
        bool HasSelectedIdea,
        string Domain,
        string Description,
        string Role,
        string RoleLabel,
        bool IsOwner,
        string OwnerName,
        string SupervisorName,
        string Status,
        string StatusLabel,
        int ProgressPercentage,
        int ActiveMembersCount,
        int MaximumMembers,
        List<ArchivedMemberViewModel> Members,
        DateTime? ArchivedAtUtc,
        DateTime CreatedAt,
        DateTime UpdatedAt);

    public sealed record ArchivedMemberViewModel(
        int UserId,
        string FullName,
        string Initials,
        string Role);
}