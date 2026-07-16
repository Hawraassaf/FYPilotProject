using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class AnalyticsModel(ApplicationDbContext db) : PageModel
{
    private const int SupervisorCapacity = 4;

    public int TotalStudents { get; private set; }
    public int TotalIdeas { get; private set; }

    public int StudentsWithSupervisor { get; private set; }
    public int StudentsWithoutSupervisor { get; private set; }

    public int StudentsWithApprovedIdea { get; private set; }
    public int StudentsWithoutApprovedIdea { get; private set; }

    public int StudentsNeedingIntervention { get; private set; }

    public int ReviewCompletionRate { get; private set; }
    public double AverageSupervisorLoad { get; private set; }
    public int AverageSupervisorLoadPercent { get; private set; }

    public int FullSupervisors { get; private set; }
    public int AlmostFullSupervisors { get; private set; }
    public int AvailableSupervisors { get; private set; }

    public string MostLoadedSupervisorName { get; private set; } = "N/A";
    public int MostLoadedSupervisorCount { get; private set; }

    public int ApprovedIdeas { get; private set; }
    public int PendingIdeas { get; private set; }
    public int NeedsRevisionIdeas { get; private set; }
    public int RejectedIdeas { get; private set; }

    public int HighRiskStudents { get; private set; }
    public int MediumRiskStudents { get; private set; }
    public int OnTrackStudents { get; private set; }

    public int UpcomingMeetings { get; private set; }
    public int CompletedMeetings { get; private set; }
    public int StudentsWithNoMeeting { get; private set; }

    public List<DomainRow> DomainDistribution { get; private set; } = [];

    public record DomainRow(
        string Domain,
        int Count,
        int Percentage
    );

    public async Task OnGetAsync()
    {
        var users = await db.Users
            .AsNoTracking()
            .ToListAsync();

        var students = users
            .Where(u => u.Role == "student")
            .ToList();

        var supervisors = users
            .Where(u => u.Role == "supervisor")
            .ToList();

        var ideas = await db.ProjectIdeas
            .AsNoTracking()
            .ToListAsync();

        var assignments = await db.SupervisorAssignments
            .AsNoTracking()
            .ToListAsync();

        var evaluations = await db.SupervisorEvaluations
            .AsNoTracking()
            .ToListAsync();

        var meetings = await db.Meetings
            .AsNoTracking()
            .ToListAsync();

        var activeAssignments = assignments
            .Where(a => a.Status == "active")
            .ToList();
        // Keep only the latest evaluation for each project idea.
        var latestEvaluationByIdea = evaluations
            .GroupBy(e => e.IdeaId)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderByDescending(e =>
                        e.UpdatedAt == default
                            ? e.CreatedAt
                            : e.UpdatedAt)
                    .First());
        TotalStudents = students.Count;
        TotalIdeas = ideas.Count;

        StudentsWithSupervisor = students.Count(student =>
            activeAssignments.Any(a => a.StudentId == student.Id));

        StudentsWithoutSupervisor = Math.Max(0, TotalStudents - StudentsWithSupervisor);

        var approvedIdeaIds = ideas
       .Where(idea =>
           latestEvaluationByIdea.TryGetValue(
               idea.Id,
               out var evaluation) &&
           NormalizeStatus(evaluation.Status) == "approved")
       .Select(idea => idea.Id)
       .ToHashSet();

        StudentsWithApprovedIdea = students.Count(student =>
            ideas.Any(idea =>
                idea.UserId == student.Id &&
                approvedIdeaIds.Contains(idea.Id)));

        StudentsWithoutApprovedIdea =
            Math.Max(0, TotalStudents - StudentsWithApprovedIdea);

        // Only completed decisions count as reviewed.
        // An empty or pending evaluation does not count.
        var reviewedIdeas = ideas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) != "pending");

        ReviewCompletionRate = TotalIdeas == 0
            ? 0
            : (int)Math.Round(
                reviewedIdeas * 100.0 / TotalIdeas);

        var supervisorLoads = supervisors
            .Select(supervisor => new
            {
                Supervisor = supervisor,
                Count = activeAssignments.Count(a => a.SupervisorId == supervisor.Id)
            })
            .ToList();

        AverageSupervisorLoad = supervisorLoads.Any()
            ? Math.Round(supervisorLoads.Average(x => x.Count), 1)
            : 0;

        AverageSupervisorLoadPercent = SupervisorCapacity == 0
            ? 0
            : (int)Math.Round(AverageSupervisorLoad * 100.0 / SupervisorCapacity);

        FullSupervisors = supervisorLoads.Count(x => x.Count >= SupervisorCapacity);
        AlmostFullSupervisors = supervisorLoads.Count(x => x.Count == SupervisorCapacity - 1);
        AvailableSupervisors = supervisorLoads.Count(x => x.Count <= SupervisorCapacity - 2);

        var mostLoaded = supervisorLoads
            .OrderByDescending(x => x.Count)
            .FirstOrDefault();

        if (mostLoaded != null)
        {
            MostLoadedSupervisorName = mostLoaded.Supervisor.FullName;
            MostLoadedSupervisorCount = mostLoaded.Count;
        }
        ApprovedIdeas = ideas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) == "approved");

        NeedsRevisionIdeas = ideas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) == "needs_revision");

        RejectedIdeas = ideas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) == "rejected");

        PendingIdeas = ideas.Count(idea =>
            !latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) ||
            NormalizeStatus(evaluation.Status) == "pending");


        string GetStudentRiskLevel(int studentId)
        {
            var hasActiveSupervisor = activeAssignments.Any(a =>
                a.StudentId == studentId);

            // A student without an active supervisor needs urgent attention.
            if (!hasActiveSupervisor)
            {
                return "high";
            }

            // Use only the student's currently selected idea.
            var selectedIdea = ideas
                .Where(i =>
                    i.UserId == studentId &&
                    i.IsSelected)
                .OrderByDescending(i => i.CreatedAt)
                .FirstOrDefault();

            // An assigned student without a selected project is high risk.
            if (selectedIdea == null)
            {
                return "high";
            }

            // A selected idea that has not been reviewed yet is medium risk.
            if (!latestEvaluationByIdea.TryGetValue(
                    selectedIdea.Id,
                    out var latestEvaluation))
            {
                return "medium";
            }

            return NormalizeStatus(latestEvaluation.Status) switch
            {
                "approved" => "on_track",
                "needs_revision" => "medium",
                "rejected" => "high",
                _ => "medium"
            };
        }

        var studentRiskLevels = students
            .ToDictionary(
                student => student.Id,
                student => GetStudentRiskLevel(student.Id));

        HighRiskStudents = studentRiskLevels.Values.Count(
            level => level == "high");

        MediumRiskStudents = studentRiskLevels.Values.Count(
            level => level == "medium");

        OnTrackStudents = studentRiskLevels.Values.Count(
            level => level == "on_track");

        StudentsNeedingIntervention =
            HighRiskStudents + MediumRiskStudents;
        // Canceled meetings must not count as valid student meetings.
        var validMeetings = meetings
            .Where(m =>
            {
                var status = NormalizeMeetingStatus(m.Status);

                return status == "scheduled" ||
                       status == "completed";
            })
            .ToList();

        UpcomingMeetings = validMeetings.Count(m =>
            NormalizeMeetingStatus(m.Status) == "scheduled" &&
            m.ScheduledAt >= DateTime.UtcNow);

        CompletedMeetings = validMeetings.Count(m =>
            NormalizeMeetingStatus(m.Status) == "completed");

        var studentsWithMeetingIds = validMeetings
            .Where(m => m.StudentId.HasValue)
            .Select(m => m.StudentId!.Value)
            .Distinct()
            .ToHashSet();

        StudentsWithNoMeeting = students.Count(student =>
            !studentsWithMeetingIds.Contains(student.Id));

        DomainDistribution = ideas
            .GroupBy(i => string.IsNullOrWhiteSpace(i.Domain) ? "Uncategorized" : i.Domain)
            .Select(g => new DomainRow(
                g.Key,
                g.Count(),
                TotalIdeas == 0 ? 0 : (int)Math.Round(g.Count() * 100.0 / TotalIdeas)
            ))
            .OrderByDescending(x => x.Count)
            .Take(5)
            .ToList();
    }

    private static string NormalizeStatus(string? status)
    {
        var value = string.IsNullOrWhiteSpace(status)
            ? "pending"
            : status.Trim().ToLowerInvariant();

        return value switch
        {
            "approved" => "approved",
            "needs_revision" => "needs_revision",
            "revision" => "needs_revision",
            "rejected" => "rejected",
            "completed" => "completed",
            "done" => "done",
            _ => "pending"
        };
    }
    private static string NormalizeMeetingStatus(string? status)
    {
        var value = string.IsNullOrWhiteSpace(status)
            ? "unknown"
            : status.Trim().ToLowerInvariant();

        return value switch
        {
            "scheduled" => "scheduled",

            "completed" => "completed",
            "done" => "completed",

            "cancelled" => "cancelled",
            "canceled" => "cancelled",

            _ => "unknown"
        };
    }
}