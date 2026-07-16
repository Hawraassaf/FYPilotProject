using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class ProgressTrackingModel(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess) : PageModel
{
    public List<ProjectListItem> Projects { get; private set; } = [];
    public ProgressProject? SelectedProject { get; private set; }

    public async Task OnGetAsync(int? ideaId)
    {
        var supervisorId = SupervisorId();

        var assignedStudentIds = await supervisorAccess.GetAssignedStudentIdsAsync(supervisorId);

        var ideas = await db.ProjectIdeas
            .AsNoTracking()
            .Include(i => i.User)
            .Where(i =>
                i.IsSelected &&
                assignedStudentIds.Contains(i.UserId))
            .OrderByDescending(i => i.CreatedAt)
            .ToListAsync();

        var ideaIds = ideas.Select(i => i.Id).ToList();

        var evaluations = await db.SupervisorEvaluations
            .AsNoTracking()
            .Where(e =>
                e.SupervisorId == supervisorId &&
                ideaIds.Contains(e.IdeaId))
            .OrderByDescending(e => e.Id)
            .ToListAsync();

        var evaluationByIdea = evaluations
            .GroupBy(e => e.IdeaId)
            .ToDictionary(g => g.Key, g => g.First());

        Projects = ideas
            .Select(idea =>
            {
                evaluationByIdea.TryGetValue(idea.Id, out var evaluation);

                return new ProjectListItem
                {
                    IdeaId = idea.Id,
                    StudentName = idea.User?.FullName ?? "Student",
                    ProjectTitle = idea.Title,
                    Domain = string.IsNullOrWhiteSpace(idea.Domain) ? "Uncategorized" : idea.Domain,
                    Status = NormalizeStatus(evaluation?.Status),
                    CreatedAt = idea.CreatedAt
                };
            })
            .ToList();

        var selectedIdea = ideaId.HasValue
            ? ideas.FirstOrDefault(i => i.Id == ideaId.Value)
            : ideas.FirstOrDefault();

        if (selectedIdea == null)
        {
            SelectedProject = null;
            return;
        }

        evaluationByIdea.TryGetValue(
    selectedIdea.Id,
    out var selectedEvaluation);

        var selectedRoadmap = await db.ProjectRoadmaps
            .AsNoTracking()
            .Include(r => r.Phases)
            .Where(r =>
                r.IdeaId == selectedIdea.Id &&
                r.UserId == selectedIdea.UserId)
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();

        var selectedPhases = selectedRoadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .ToList() ?? [];

        SelectedProject = BuildProgressProject(
            selectedIdea,
            selectedEvaluation,
            selectedPhases);
    }

    private ProgressProject BuildProgressProject(
        ProjectIdea idea,
        SupervisorEvaluation? evaluation,
        IReadOnlyList<RoadmapPhase> phases)
    {
        var status = NormalizeStatus(evaluation?.Status);

        var startDate = idea.CreatedAt.Date;

        var orderedPhases = phases
            .OrderBy(p => p.PhaseNumber)
            .ToList();

        var plannedWeeks = orderedPhases.Count > 0
            ? orderedPhases.Sum(
                phase => Math.Max(1, phase.EstimatedWeeks))
            : idea.ExpectedDurationWeeks > 0
                ? idea.ExpectedDurationWeeks
                : 16;

        var endDate =
            startDate.AddDays(plannedWeeks * 7);

        var today = DateTime.UtcNow.Date;

        var totalDays = Math.Max(
            1,
            (endDate - startDate).Days);

        var daysElapsed = Math.Max(
            0,
            (today - startDate).Days);

        var daysRemaining = Math.Max(
            0,
            (endDate - today).Days);

        var completedPhases = orderedPhases.Count(
            phase => phase.IsCompleted);

        var totalPhases = orderedPhases.Count;

        var progress = totalPhases == 0
            ? 0
            : (int)Math.Round(
                completedPhases * 100.0 /
                totalPhases);

        var roadmap = BuildRoadmap(
            orderedPhases,
            startDate);

        var nextStep = roadmap.FirstOrDefault(step =>
            step.State == "current" ||
            step.State == "upcoming");

        var risks = BuildRisks(
            idea,
            evaluation,
            status);

        var updates = BuildUpdates(
            idea,
            evaluation,
            status);

        return new ProgressProject
        {
            IdeaId = idea.Id,

            StudentName =
                idea.User?.FullName ?? "Student",

            StudentEmail =
                idea.User?.Email ?? "",

            StudentInitials =
                Initials(idea.User?.FullName),

            ProjectCode =
                $"FYP-{idea.Id:0000}",

            ProjectTitle = idea.Title,

            Department =
                "Computer Science",

            Domain =
                string.IsNullOrWhiteSpace(idea.Domain)
                    ? "Uncategorized"
                    : idea.Domain,

            DifficultyLevel =
                string.IsNullOrWhiteSpace(
                    idea.DifficultyLevel)
                    ? "Not specified"
                    : idea.DifficultyLevel,

            SupervisorName =
                CurrentSupervisorName(),

            Status = status,

            StatusLabel =
                ProjectStatusLabel(status),

            StatusClass =
                ProjectStatusClass(status),

            StartDate = startDate,
            EndDate = endDate,

            OverallProgress = progress,

            MilestonesCompleted =
                completedPhases,

            MilestonesTotal =
                totalPhases,

            DaysElapsed =
                Math.Min(daysElapsed, totalDays),

            TotalDays = totalDays,
            DaysRemaining = daysRemaining,

            DeliverablesSubmitted =
                completedPhases,

            DeliverablesTotal =
                totalPhases,

            NextDue = nextStep?.Title ??
                (totalPhases == 0
                    ? "Roadmap not generated"
                    : "All phases completed"),

            NextDueDate =
                nextStep?.Date ?? endDate,

            Roadmap = roadmap,
            Risks = risks,
            Updates = updates,

            OriginalityScore =
                evaluation?.OriginalityScore ?? 0,

            SimilarityScore =
                evaluation?.SimilarityScore ?? 0
        };
    }

    private List<RoadmapStep> BuildRoadmap(
      IReadOnlyList<RoadmapPhase> phases,
      DateTime startDate)
    {
        var steps = new List<RoadmapStep>();

        var phaseDate = startDate;
        var currentPhaseAssigned = false;

        foreach (var phase in phases
                     .OrderBy(p => p.PhaseNumber))
        {
            string state;

            if (phase.IsCompleted)
            {
                state = "completed";
            }
            else if (!currentPhaseAssigned)
            {
                state = "current";
                currentPhaseAssigned = true;
            }
            else
            {
                state = "upcoming";
            }

            steps.Add(new RoadmapStep
            {
                Number = phase.PhaseNumber,

                Title = string.IsNullOrWhiteSpace(
                    phase.Name)
                    ? $"Phase {phase.PhaseNumber}"
                    : phase.Name,

                Date = phaseDate,
                State = state
            });

            phaseDate = phaseDate.AddDays(
                Math.Max(
                    1,
                    phase.EstimatedWeeks) * 7);
        }

        return steps;
    }

    private List<RiskItem> BuildRisks(ProjectIdea idea, SupervisorEvaluation? evaluation, string status)
    {
        var risks = new List<RiskItem>();

        if (status == "needs_revision")
        {
            risks.Add(new RiskItem
            {
                Title = "Revision Required",
                Description = string.IsNullOrWhiteSpace(evaluation?.ImprovementSuggestions)
                    ? "The idea needs supervisor guidance before moving forward."
                    : evaluation.ImprovementSuggestions,
                Severity = "Medium",
                IdentifiedOn = DateTime.UtcNow.Date,
                Impact = "Medium"
            });
        }

        if (status == "rejected")
        {
            risks.Add(new RiskItem
            {
                Title = "Project Direction Blocked",
                Description = "The current proposal was rejected and the student needs a new direction.",
                Severity = "High",
                IdentifiedOn = DateTime.UtcNow.Date,
                Impact = "High"
            });
        }

        if (string.IsNullOrWhiteSpace(idea.Domain))
        {
            risks.Add(new RiskItem
            {
                Title = "Domain Not Defined",
                Description = "The project domain is missing, which may make supervisor matching and tracking less clear.",
                Severity = "Low",
                IdentifiedOn = idea.CreatedAt.Date,
                Impact = "Low"
            });
        }

        if (!risks.Any())
        {
            risks.Add(new RiskItem
            {
                Title = "No Active Blockers",
                Description = "No major blocker is currently detected from the available review data.",
                Severity = "Low",
                IdentifiedOn = DateTime.UtcNow.Date,
                Impact = "Low"
            });
        }

        return risks.Take(3).ToList();
    }

    private List<UpdateItem> BuildUpdates(ProjectIdea idea, SupervisorEvaluation? evaluation, string status)
    {
        var updates = new List<UpdateItem>
        {
            new()
            {
                Title = "Project idea submitted",
                Subtitle = idea.Title,
                Date = idea.CreatedAt,
                Type = "Project",
                By = idea.User?.FullName ?? "Student",
                Icon = "bi bi-file-earmark-text"
            }
        };

        if (evaluation != null)
        {
            updates.Add(new UpdateItem
            {
                Title = status switch
                {
                    "approved" => "Idea approved by supervisor",
                    "needs_revision" => "Revision feedback added",
                    "rejected" => "Idea rejected by supervisor",
                    _ => "Supervisor review is pending"
                },
                Subtitle = string.IsNullOrWhiteSpace(evaluation.Comment)
                    ? "Evaluation status updated."
                    : evaluation.Comment,
                Date = evaluation.UpdatedAt == default ? evaluation.CreatedAt : evaluation.UpdatedAt,
                Type = "Evaluation",
                By = CurrentSupervisorName(),
                Icon = status switch
                {
                    "approved" => "bi bi-check-circle",
                    "needs_revision" => "bi bi-arrow-repeat",
                    "rejected" => "bi bi-x-circle",
                    _ => "bi bi-clock-history"
                }
            });
        }

        return updates
            .OrderByDescending(u => u.Date)
            .Take(4)
            .ToList();
    }

    private string CurrentSupervisorName()
    {
        return User.FindFirst(ClaimTypes.Name)?.Value
            ?? User.Identity?.Name
            ?? "Supervisor";
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

    private static string ProjectStatusLabel(string status)
    {
        return status switch
        {
            "approved" => "On Track",
            "needs_revision" => "Needs Attention",
            "rejected" => "Blocked",
            _ => "Under Review"
        };
    }

    private static string ProjectStatusClass(string status)
    {
        return status switch
        {
            "approved" => "on-track",
            "needs_revision" => "attention",
            "rejected" => "blocked",
            _ => "review"
        };
    }

    private static string Initials(string? name)
    {
        if (string.IsNullOrWhiteSpace(name))
        {
            return "ST";
        }

        var parts = name
            .Split(' ', StringSplitOptions.RemoveEmptyEntries)
            .Take(2)
            .ToList();

        return parts.Count == 0
            ? "ST"
            : string.Join("", parts.Select(p => p[0])).ToUpperInvariant();
    }

    private int SupervisorId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }

    public sealed class ProjectListItem
    {
        public int IdeaId { get; set; }
        public string StudentName { get; set; } = "";
        public string ProjectTitle { get; set; } = "";
        public string Domain { get; set; } = "";
        public string Status { get; set; } = "pending";
        public DateTime CreatedAt { get; set; }
    }

    public sealed class ProgressProject
    {
        public int IdeaId { get; set; }
        public string StudentName { get; set; } = "";
        public string StudentEmail { get; set; } = "";
        public string StudentInitials { get; set; } = "ST";
        public string ProjectCode { get; set; } = "";
        public string ProjectTitle { get; set; } = "";
        public string Department { get; set; } = "";
        public string Domain { get; set; } = "";
        public string DifficultyLevel { get; set; } = "";
        public string SupervisorName { get; set; } = "";
        public string Status { get; set; } = "pending";
        public string StatusLabel { get; set; } = "";
        public string StatusClass { get; set; } = "";
        public DateTime StartDate { get; set; }
        public DateTime EndDate { get; set; }
        public int OverallProgress { get; set; }
        public int MilestonesCompleted { get; set; }
        public int MilestonesTotal { get; set; }
        public int DaysElapsed { get; set; }
        public int TotalDays { get; set; }
        public int DaysRemaining { get; set; }
        public int DeliverablesSubmitted { get; set; }
        public int DeliverablesTotal { get; set; }
        public string NextDue { get; set; } = "";
        public DateTime NextDueDate { get; set; }
        public int OriginalityScore { get; set; }
        public int SimilarityScore { get; set; }
        public List<RoadmapStep> Roadmap { get; set; } = [];
        public List<RiskItem> Risks { get; set; } = [];
        public List<UpdateItem> Updates { get; set; } = [];
    }

    public sealed class RoadmapStep
    {
        public int Number { get; set; }
        public string Title { get; set; } = "";
        public DateTime Date { get; set; }
        public string State { get; set; } = "upcoming";
    }

    public sealed class RiskItem
    {
        public string Title { get; set; } = "";
        public string Description { get; set; } = "";
        public string Severity { get; set; } = "Low";
        public DateTime IdentifiedOn { get; set; }
        public string Impact { get; set; } = "Low";
    }

    public sealed class UpdateItem
    {
        public string Title { get; set; } = "";
        public string Subtitle { get; set; } = "";
        public DateTime Date { get; set; }
        public string Type { get; set; } = "";
        public string By { get; set; } = "";
        public string Icon { get; set; } = "";
    }
}