using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class DashboardModel(
    ApplicationDbContext db) : PageModel
{
    public List<ProjectDashboardItem> AllProjects { get; private set; } = [];

    public List<UpcomingMeetingItem> UpcomingMeetings { get; private set; } = [];

    public async Task OnGetAsync(
        CancellationToken cancellationToken)
    {
        var supervisorId = SupervisorId();

        var projects = await db.Projects
            .AsNoTracking()
            .Include(project => project.Student)
            .Include(project => project.ProjectIdea)
            .Include(project => project.Members
                .Where(member => member.Status == "active"))
                .ThenInclude(member => member.User)
            .Where(project =>
                project.SupervisorId == supervisorId &&
                project.SupervisorAssignmentStatus == "active" &&
                project.ProjectIdeaId.HasValue &&
                project.ProjectIdea != null)
            .OrderByDescending(project => project.UpdatedAt)
            .AsSplitQuery()
            .ToListAsync(cancellationToken);

        var projectIds = projects
            .Select(project => project.Id)
            .ToList();

        var evaluations = projectIds.Count == 0
            ? []
            : await db.SupervisorEvaluations
                .AsNoTracking()
                .Where(evaluation =>
                    evaluation.SupervisorId == supervisorId &&
                    evaluation.ProjectId.HasValue &&
                    projectIds.Contains(evaluation.ProjectId.Value))
                .ToListAsync(cancellationToken);

        var latestEvaluationByProject = evaluations
            .GroupBy(evaluation => evaluation.ProjectId!.Value)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderByDescending(evaluation =>
                        evaluation.UpdatedAt == default
                            ? evaluation.CreatedAt
                            : evaluation.UpdatedAt)
                    .First());

        AllProjects = projects
            .Select(project =>
            {
                var idea = project.ProjectIdea!;

                latestEvaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation);

                var members = project.Members
                    .Where(member =>
                        member.Status == "active" &&
                        member.User != null)
                    .Select(member => new MemberSummary(
                        member.UserId,
                        member.User!.FullName))
                    .ToList();

                if (members.All(member =>
                        member.UserId != project.StudentId))
                {
                    members.Add(new MemberSummary(
                        project.StudentId,
                        project.Student?.FullName ?? "Project owner"));
                }

                var distinctMembers = members
                    .GroupBy(member => member.UserId)
                    .Select(group => group.First())
                    .OrderBy(member => member.FullName)
                    .ToList();

                var evaluationUpdatedAt = evaluation == null
                    ? DateTime.MinValue
                    : evaluation.UpdatedAt == default
                        ? evaluation.CreatedAt
                        : evaluation.UpdatedAt;

                var lastActivityAt = new[]
                {
                    project.UpdatedAt,
                    idea.CreatedAt,
                    evaluationUpdatedAt
                }.Max();

                return new ProjectDashboardItem
                {
                    ProjectId = project.Id,
                    IdeaId = idea.Id,
                    ProjectTitle = SafeProjectTitle(project.Title),
                    IdeaTitle = idea.Title,
                    MemberNames = string.Join(
                        ", ",
                        distinctMembers.Select(member => member.FullName)),
                    MemberCount = distinctMembers.Count,
                    Domain = string.IsNullOrWhiteSpace(idea.Domain)
                        ? "Uncategorized"
                        : idea.Domain,
                    DifficultyLevel =
                        string.IsNullOrWhiteSpace(idea.DifficultyLevel)
                            ? "Not specified"
                            : idea.DifficultyLevel,
                    CreatedAt = idea.CreatedAt,
                    LastActivityAt = lastActivityAt,
                    FeasibilityScore = idea.FeasibilityScore,
                    InnovationScore = idea.InnovationScore,
                    Status = NormalizeStatus(evaluation?.Status)
                };
            })
            .OrderByDescending(project => project.LastActivityAt)
            .ToList();

        if (projectIds.Count == 0)
        {
            UpcomingMeetings = [];
            return;
        }

        var now = DateTime.UtcNow;

        var meetings = await db.Meetings
            .AsNoTracking()
            .Where(meeting =>
                meeting.SupervisorId == supervisorId &&
                meeting.ProjectId.HasValue &&
                projectIds.Contains(meeting.ProjectId.Value) &&
                meeting.Status == "scheduled" &&
                meeting.ScheduledAt
                    .AddMinutes(meeting.DurationMinutes) >= now)
            .OrderBy(meeting => meeting.ScheduledAt)
            .Take(6)
            .ToListAsync(cancellationToken);

        var projectById = AllProjects
            .ToDictionary(project => project.ProjectId);

        UpcomingMeetings = meetings
            .Where(meeting =>
                meeting.ProjectId.HasValue &&
                projectById.ContainsKey(meeting.ProjectId.Value))
            .Select(meeting =>
            {
                var project = projectById[meeting.ProjectId!.Value];

                return new UpcomingMeetingItem
                {
                    MeetingId = meeting.Id,
                    ProjectId = project.ProjectId,
                    IdeaId = project.IdeaId,
                    ProjectTitle = project.ProjectTitle,
                    MeetingTitle = meeting.Title,
                    MemberNames = project.MemberNames,
                    MemberCount = project.MemberCount,
                    ScheduledAt = meeting.ScheduledAt,
                    DurationMinutes = meeting.DurationMinutes
                };
            })
            .ToList();
    }

    private int SupervisorId()
    {
        var value = User
            .FindFirst(ClaimTypes.NameIdentifier)
            ?.Value;

        if (int.TryParse(value, out var supervisorId))
        {
            return supervisorId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in supervisor.");
    }

    private static string NormalizeStatus(string? status)
    {
        var normalized = string.IsNullOrWhiteSpace(status)
            ? "pending"
            : status.Trim().ToLowerInvariant();

        return normalized switch
        {
            "approved" => "approved",
            "needs_revision" => "needs_revision",
            "rejected" => "rejected",
            _ => "pending"
        };
    }

    private static string SafeProjectTitle(string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
    }

    private sealed record MemberSummary(
        int UserId,
        string FullName);

    public sealed class ProjectDashboardItem
    {
        public int ProjectId { get; set; }

        public int IdeaId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string IdeaTitle { get; set; } = "";

        public string MemberNames { get; set; } = "";

        public int MemberCount { get; set; }

        public string Domain { get; set; } = "";

        public string DifficultyLevel { get; set; } = "";

        public DateTime CreatedAt { get; set; }

        public DateTime LastActivityAt { get; set; }

        public int FeasibilityScore { get; set; }

        public int InnovationScore { get; set; }

        public string Status { get; set; } = "pending";
    }

    public sealed class UpcomingMeetingItem
    {
        public int MeetingId { get; set; }

        public int ProjectId { get; set; }

        public int IdeaId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string MeetingTitle { get; set; } = "";

        public string MemberNames { get; set; } = "";

        public int MemberCount { get; set; }

        public DateTime ScheduledAt { get; set; }

        public int DurationMinutes { get; set; }
    }
}