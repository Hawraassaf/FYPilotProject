using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class DashboardModel(ApplicationDbContext db) : PageModel
{
    private const int SupervisorCapacity = 4;

    public List<StatCard> Stats { get; private set; } = [];
    public List<AttentionCard> AttentionCards { get; private set; } = [];
    public List<SupervisorWorkloadRow> SupervisorWorkload { get; private set; } = [];
    public List<ProjectRiskRow> ProjectRisks { get; private set; } = [];
    public List<UserRow> RecentUsers { get; private set; } = [];
    public List<ProjectIdea> RecentIdeas { get; private set; } = [];

    public record StatCard(
        string Label,
        string Value,
        string Helper,
        string Icon,
        string ColorClass
    );

    public record AttentionCard(
        string Title,
        string Count,
        string Description,
        string Icon,
        string ColorClass,
        string ActionText,
        string ActionUrl
    );

    public record SupervisorWorkloadRow(
        int SupervisorId,
        string SupervisorName,
        int AssignedStudents,
        int Capacity,
        int PendingReviews,
        int UpcomingMeetings,
        string Status,
        string StatusClass
    );

    public record ProjectRiskRow(
        int IdeaId,
        string Title,
        string StudentName,
        string Domain,
        int FeasibilityScore,
        string RiskReason,
        string RiskClass
    );

    public record UserRow(
        string Name,
        string Role,
        DateTime CreatedAt
    );

    public async Task OnGetAsync()
    {
        var users = await db.Users
            .AsNoTracking()
            .ToListAsync();

        var ideas = await db.ProjectIdeas
            .AsNoTracking()
            .Include(i => i.User)
            .OrderByDescending(i => i.CreatedAt)
            .ToListAsync();

        var assignments = await db.SupervisorAssignments
            .AsNoTracking()
            .Include(a => a.Student)
            .Include(a => a.Supervisor)
            .ToListAsync();

        var evaluations = await db.SupervisorEvaluations
            .AsNoTracking()
            .ToListAsync();

        var meetings = await db.Meetings
            .AsNoTracking()
            .ToListAsync();

        var totalUsers = users.Count;
        var totalStudents = users.Count(u => u.Role == "student");
        var totalSupervisors = users.Count(u => u.Role == "supervisor");
        var totalIdeas = ideas.Count;

        var activeAssignments = assignments
            .Where(a => a.Status == "active")
            .ToList();

        var pendingSupervisorRequests = assignments
            .Count(a => a.Status == "pending_admin");

        var studentsWithoutSupervisor = users
            .Where(u => u.Role == "student")
            .Count(student => !activeAssignments.Any(a => a.StudentId == student.Id));

        var supervisorsAtCapacity = users
            .Where(u => u.Role == "supervisor")
            .Count(supervisor =>
                activeAssignments.Count(a => a.SupervisorId == supervisor.Id) >= SupervisorCapacity);

        var ideaIdsWithApprovedEvaluation = evaluations
            .Where(e => NormalizeStatus(e.Status) == "approved")
            .Select(e => e.IdeaId)
            .Distinct()
            .ToHashSet();

        var studentsWithoutApprovedIdea = users
            .Where(u => u.Role == "student")
            .Count(student =>
                !ideas.Any(i =>
                    i.UserId == student.Id &&
                    ideaIdsWithApprovedEvaluation.Contains(i.Id)));

        var projectsNeedingRevision = evaluations
            .Count(e => NormalizeStatus(e.Status) == "needs_revision");

        var lowFeasibilityProjects = ideas
            .Count(i => i.FeasibilityScore > 0 && i.FeasibilityScore < 55);

        Stats =
        [
            new("Total Users", totalUsers.ToString(), "Registered accounts", "people", "blue"),
            new("Students", totalStudents.ToString(), "Student accounts", "person-badge", "purple"),
            new("Supervisors", totalSupervisors.ToString(), "Available reviewers", "person-check", "green"),
            new("Project Ideas", totalIdeas.ToString(), "Generated/submitted ideas", "stars", "yellow")
        ];

        AttentionCards =
        [
            new(
                "Pending Supervisor Requests",
                pendingSupervisorRequests.ToString(),
                "Students are waiting for admin approval.",
                "person-lines-fill",
                pendingSupervisorRequests > 0 ? "orange" : "green",
                "Review Requests",
                "/Admin/SupervisorAssignments"
            ),

            new(
                "Students Without Supervisor",
                studentsWithoutSupervisor.ToString(),
                "Students need an active supervisor assignment.",
                "person-exclamation",
                studentsWithoutSupervisor > 0 ? "red" : "green",
                "Assign Supervisor",
                "/Admin/SupervisorAssignments"
            ),

            new(
                "Supervisors At Capacity",
                supervisorsAtCapacity.ToString(),
                $"Capacity limit is {SupervisorCapacity} students per supervisor.",
                "speedometer2",
                supervisorsAtCapacity > 0 ? "orange" : "blue",
                "View Workload",
                "/Admin/SupervisorAssignments"
            ),

            new(
                "Students Without Approved Idea",
                studentsWithoutApprovedIdea.ToString(),
                "Students still do not have an approved project idea.",
                "journal-x",
                studentsWithoutApprovedIdea > 0 ? "red" : "green",
               "Review Projects",
               "/Admin/Analytics"
            ),

            new(
                "Projects Needing Revision",
                projectsNeedingRevision.ToString(),
                "Projects require supervisor guidance before moving forward.",
                "arrow-repeat",
                projectsNeedingRevision > 0 ? "orange" : "green",
               "Open Projects",
                "/Admin/Analytics"
            ),

            new(
                "Low Feasibility Projects",
                lowFeasibilityProjects.ToString(),
                "Projects with weak feasibility scores may need intervention.",
                "exclamation-triangle",
                lowFeasibilityProjects > 0 ? "red" : "green",
                "Check Quality",
                "/Admin/Analytics"
            )
        ];

        SupervisorWorkload = users
            .Where(u => u.Role == "supervisor")
            .OrderBy(u => u.FullName)
            .Select(supervisor =>
            {
                var assignedStudentIds = activeAssignments
                    .Where(a => a.SupervisorId == supervisor.Id)
                    .Select(a => a.StudentId)
                    .ToList();

                var supervisorIdeaIds = ideas
                    .Where(i => assignedStudentIds.Contains(i.UserId))
                    .Select(i => i.Id)
                    .ToList();

                var supervisorEvaluations = evaluations
                    .Where(e =>
                        e.SupervisorId == supervisor.Id &&
                        supervisorIdeaIds.Contains(e.IdeaId))
                    .ToList();

                var pendingReviews = supervisorIdeaIds.Count(ideaId =>
                {
                    var evaluation = supervisorEvaluations
                        .OrderByDescending(e => e.UpdatedAt)
                        .FirstOrDefault(e => e.IdeaId == ideaId);

                    var status = NormalizeStatus(evaluation?.Status);

                    return evaluation == null ||
                           status == "pending" ||
                           status == "needs_revision";
                });

                var upcomingMeetings = meetings.Count(m =>
                    m.SupervisorId == supervisor.Id &&
                    m.Status == "scheduled" &&
                    m.ScheduledAt >= DateTime.UtcNow);

                var assignedCount = assignedStudentIds.Count;

                var status = assignedCount >= SupervisorCapacity
                    ? "Full"
                    : assignedCount >= SupervisorCapacity - 1
                        ? "Almost Full"
                        : "Available";

                var statusClass = assignedCount >= SupervisorCapacity
                    ? "danger"
                    : assignedCount >= SupervisorCapacity - 1
                        ? "warning"
                        : "success";

                return new SupervisorWorkloadRow(
                    supervisor.Id,
                    supervisor.FullName,
                    assignedCount,
                    SupervisorCapacity,
                    pendingReviews,
                    upcomingMeetings,
                    status,
                    statusClass);
            })
            .ToList();

        ProjectRisks = ideas
            .Select(idea =>
            {
                var latestEvaluation = evaluations
                    .Where(e => e.IdeaId == idea.Id)
                    .OrderByDescending(e => e.UpdatedAt)
                    .FirstOrDefault();

                var status = NormalizeStatus(latestEvaluation?.Status);

                var reason = "";
                var riskClass = "safe";

                if (status == "rejected")
                {
                    reason = "Rejected by supervisor";
                    riskClass = "danger";
                }
                else if (status == "needs_revision")
                {
                    reason = "Needs revision";
                    riskClass = "warning";
                }
                else if (idea.FeasibilityScore > 0 && idea.FeasibilityScore < 55)
                {
                    reason = "Low feasibility";
                    riskClass = "danger";
                }
                else if (string.IsNullOrWhiteSpace(idea.Domain))
                {
                    reason = "Missing domain";
                    riskClass = "warning";
                }
                else if (latestEvaluation == null)
                {
                    reason = "No supervisor review";
                    riskClass = "warning";
                }

                return new
                {
                    Idea = idea,
                    Reason = reason,
                    RiskClass = riskClass
                };
            })
            .Where(x => !string.IsNullOrWhiteSpace(x.Reason))
            .OrderByDescending(x => x.RiskClass == "danger")
            .ThenByDescending(x => x.Idea.CreatedAt)
            .Take(6)
            .Select(x => new ProjectRiskRow(
                x.Idea.Id,
                x.Idea.Title,
                x.Idea.User?.FullName ?? "Student",
                string.IsNullOrWhiteSpace(x.Idea.Domain) ? "Uncategorized" : x.Idea.Domain,
                x.Idea.FeasibilityScore,
                x.Reason,
                x.RiskClass))
            .ToList();

        RecentUsers = users
            .OrderByDescending(u => u.CreatedAt)
            .Take(6)
            .Select(u => new UserRow(u.FullName, u.Role, u.CreatedAt))
            .ToList();

        RecentIdeas = ideas
            .Take(6)
            .ToList();
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
}