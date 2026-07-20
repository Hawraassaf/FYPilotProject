using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class MyProjectsModel(
    ApplicationDbContext db) : PageModel
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

    public async Task OnGetAsync()
    {
        var userId = CurrentUserId();

        var projects = await db.Projects
            .AsNoTracking()
            .Where(project =>
                project.Members.Any(member =>
                    member.UserId == userId &&
                    member.Status == "active"))
            .Include(project => project.ProjectIdea)
            .Include(project => project.Members
                .Where(member => member.Status == "active"))
            .ThenInclude(member => member.User)
            .AsSplitQuery()
            .OrderByDescending(project => project.UpdatedAt)
            .ThenByDescending(project => project.Id)
            .ToListAsync();

        Projects = projects
            .Select(project =>
            {
                var currentMembership = project.Members
                    .First(member =>
                        member.UserId == userId &&
                        member.Status == "active");

                var members = project.Members
                    .Where(member =>
                        member.Status == "active" &&
                        member.User != null)
                    .OrderBy(member =>
                        member.Role == "owner" ? 0 : 1)
                    .ThenBy(member => member.JoinedAt)
                    .Select(member =>
                        new MemberPreviewViewModel(
                            member.UserId,
                            member.User!.FullName,
                            Initials(member.User.FullName),
                            member.Role))
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

                var domain =
                    string.IsNullOrWhiteSpace(
                        project.ProjectIdea?.Domain)
                        ? "General Software"
                        : project.ProjectIdea.Domain.Trim();

                var icon = ResolveProjectIcon(domain);

                return new ProjectCardViewModel(
                    Id: project.Id,
                    ProjectIdeaId: project.ProjectIdeaId,
                    Title: string.IsNullOrWhiteSpace(project.Title)
                        ? "Untitled Project"
                        : project.Title.Trim(),
                    Domain: domain,
                    Description: Shorten(
                        string.IsNullOrWhiteSpace(project.Description)
                            ? project.ProjectIdea?.WhyUseful
                            : project.Description,
                        185),
                    Role: currentMembership.Role,
                    RoleLabel: ToDisplayText(currentMembership.Role),
                    Status: NormalizeStatus(project.Status),
                    StatusLabel: ToDisplayText(project.Status),
                    StatusCssClass: ResolveStatusClass(project.Status),
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
                    IconCssClass: icon.CssClass,
                    IconClass: icon.IconClass,
                    TeamActionText: ResolveTeamActionText(
                        currentMembership.Role,
                        members.Count,
                        maximumMembers));
            })
            .ToList();
    }

    private int CurrentUserId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(value, out var userId))
        {
            throw new InvalidOperationException(
                "The authenticated user identifier is invalid.");
        }

        return userId;
    }

    private static string Initials(string? fullName)
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
            return "A collaborative final year project workspace.";
        }

        var clean = value.Trim();

        if (clean.Length <= maximumLength)
        {
            return clean;
        }

        return $"{clean[..maximumLength].TrimEnd()}…";
    }

    private static string NormalizeStatus(
        string? status)
    {
        return string.IsNullOrWhiteSpace(status)
            ? "planning"
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
            return "Planning";
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
                    : char.ToUpperInvariant(word[0]) +
                      word[1..].ToLowerInvariant()));
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

        return activeMembers switch
        {
            <= 1 when activeMembers < maximumMembers =>
                "Collaborate",

            _ =>
                "Manage Team"
        };
    }

    private static ProjectIconViewModel ResolveProjectIcon(
        string domain)
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

        return new ProjectIconViewModel(
            "project-icon-navy",
            "bi bi-cpu-fill");
    }

    public sealed record ProjectCardViewModel(
        int Id,
        int? ProjectIdeaId,
        string Title,
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
        string IconCssClass,
        string IconClass,
        string TeamActionText);

    public sealed record MemberPreviewViewModel(
        int UserId,
        string FullName,
        string Initials,
        string Role);

    private sealed record ProjectIconViewModel(
        string CssClass,
        string IconClass);
}