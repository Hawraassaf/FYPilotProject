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

        /*
         * SupervisorAssignment.ProjectId is the authoritative
         * source for active supervisor access.
         */
        var assignedProjectIds =
            await db.SupervisorAssignments
                .AsNoTracking()
                .Where(assignment =>
                    assignment.SupervisorId == supervisorId &&
                    assignment.Status == "active" &&
                    assignment.ProjectId.HasValue)
                .Select(assignment =>
                    assignment.ProjectId!.Value)
                .Distinct()
                .ToListAsync(cancellationToken);

        var projects = await db.Projects
            .AsNoTracking()
            .Include(project => project.Student)
            .Include(project => project.DeletedByUser)
            .Include(project => project.ProjectIdea)
            .Include(project => project.Members)
                .ThenInclude(member => member.User)
            .Where(project =>
                assignedProjectIds.Contains(project.Id))
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
                var idea = project.ProjectIdea;

                latestEvaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation);

                var activeMemberships = project.Members
                    .Where(member =>
                        NormalizeMembershipStatus(member.Status) == "active" &&
                        member.User != null)
                    .ToList();

                var archivedMemberships = activeMemberships
                    .Where(member => member.IsArchived)
                    .OrderBy(member => member.ArchivedAtUtc)
                    .ToList();

                var removedMemberships = project.Members
                    .Where(member =>
                        NormalizeMembershipStatus(member.Status) == "removed" &&
                        member.User != null)
                    .OrderByDescending(member => member.RemovedAtUtc)
                    .ToList();

                var ownerMembership = activeMemberships
                    .FirstOrDefault(member =>
                        member.UserId == project.StudentId);

                var members = activeMemberships
                    .Select(member => new MemberSummary(
                        member.UserId,
                        member.User!.FullName))
                    .ToList();

                /*
                 * Keep legacy projects readable when their owner row is
                 * temporarily missing, but never present a removed owner
                 * as an active member of a soft-deleted project.
                 */
                if (!project.IsDeleted &&
                    members.All(member =>
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

                var lifecycleActivityAt = project.Members
                    .SelectMany(member => new[]
                    {
                        member.ArchivedAtUtc,
                        member.RemovedAtUtc
                    })
                    .Append(project.DeletedAtUtc)
                    .Where(value => value.HasValue)
                    .Select(value => value!.Value)
                    .DefaultIfEmpty(DateTime.MinValue)
                    .Max();

                var lastActivityAt = new[]
                {
                    project.UpdatedAt,
                    idea?.CreatedAt ?? DateTime.MinValue,
                    evaluationUpdatedAt,
                    lifecycleActivityAt
                }.Max();

                return new ProjectDashboardItem
                {
                    ProjectId = project.Id,
                    IdeaId = idea?.Id ?? 0,
                    HasOfficialIdea = idea != null,
                    ProjectTitle = SafeProjectTitle(project.Title),
                    IdeaTitle = idea?.Title
                        ?? "No official idea selected yet",
                    MemberNames = distinctMembers.Count == 0
                        ? "No active members"
                        : string.Join(
                            ", ",
                            distinctMembers.Select(member => member.FullName)),
                    MemberCount = distinctMembers.Count,
                    IsDeleted = project.IsDeleted,
                    DeletedAtUtc = project.DeletedAtUtc,
                    DeletedByName = project.DeletedByUser?.FullName
                        ?? project.Student?.FullName
                        ?? "A project member",
                    OwnerIsArchived = ownerMembership?.IsArchived == true,
                    OwnerArchivedAtUtc = ownerMembership?.ArchivedAtUtc,
                    ArchivedMemberCount = archivedMemberships.Count,
                    ArchivedMemberNames = string.Join(
                        ", ",
                        archivedMemberships
                            .Select(member => member.User!.FullName)),
                    RemovedMemberCount = removedMemberships.Count,
                    RemovedMemberNames = string.Join(
                        ", ",
                        removedMemberships
                            .Select(member => member.User!.FullName)),
                    Domain = idea == null
                        ? "Awaiting official idea"
                        : string.IsNullOrWhiteSpace(idea.Domain)
                            ? "Uncategorized"
                            : idea.Domain,
                    DifficultyLevel =
                        idea == null
                            ? "Not available"
                            : string.IsNullOrWhiteSpace(idea.DifficultyLevel)
                                ? "Not specified"
                                : idea.DifficultyLevel,
                    CreatedAt = idea?.CreatedAt ?? project.CreatedAt,
                    LastActivityAt = lastActivityAt,
                    FeasibilityScore = idea?.FeasibilityScore ?? 0,
                    InnovationScore = idea?.InnovationScore ?? 0,
                    Status = idea == null
                        ? "awaiting_idea"
                        : NormalizeStatus(evaluation?.Status)
                };
            })
            .OrderByDescending(project => project.LastActivityAt)
            .ToList();

        if (projectIds.Count == 0)
        {
            UpcomingMeetings = [];
            return;
        }

        var activeProjectIds = projects
            .Where(project => !project.IsDeleted)
            .Select(project => project.Id)
            .ToList();

        if (activeProjectIds.Count == 0)
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
                activeProjectIds.Contains(meeting.ProjectId.Value) &&
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

    private static string NormalizeMembershipStatus(
        string? status)
    {
        return string.IsNullOrWhiteSpace(status)
            ? string.Empty
            : status.Trim().ToLowerInvariant();
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
            "awaiting_idea" => "awaiting_idea",
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

        public bool HasOfficialIdea { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string IdeaTitle { get; set; } = "";

        public string MemberNames { get; set; } = "";

        public int MemberCount { get; set; }

        public bool IsDeleted { get; set; }

        public DateTime? DeletedAtUtc { get; set; }

        public string DeletedByName { get; set; } = "";

        public bool OwnerIsArchived { get; set; }

        public DateTime? OwnerArchivedAtUtc { get; set; }

        public int ArchivedMemberCount { get; set; }

        public string ArchivedMemberNames { get; set; } = "";

        public int RemovedMemberCount { get; set; }

        public string RemovedMemberNames { get; set; } = "";

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