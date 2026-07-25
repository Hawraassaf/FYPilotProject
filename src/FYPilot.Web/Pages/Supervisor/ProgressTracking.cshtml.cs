using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class ProgressTrackingModel(
    ApplicationDbContext db) : PageModel
{
    public List<ProjectListItem> Projects { get; private set; } = [];

    public ProgressProject? SelectedProject { get; private set; }

    public async Task OnGetAsync(
        int? projectId,
        int? ideaId,
        CancellationToken cancellationToken)
    {
        var supervisorId = SupervisorId();

        var projects = await db.Projects
            .AsNoTracking()
            .Include(project => project.Student)
            .Include(project => project.ProjectIdea)
                .ThenInclude(idea => idea!.User)
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
                .OrderByDescending(evaluation =>
                    evaluation.UpdatedAt == default
                        ? evaluation.CreatedAt
                        : evaluation.UpdatedAt)
                .ToListAsync(cancellationToken);

        var evaluationByProject = evaluations
            .GroupBy(evaluation => evaluation.ProjectId!.Value)
            .ToDictionary(
                group => group.Key,
                group => group
                    .OrderByDescending(evaluation =>
                        evaluation.UpdatedAt == default
                            ? evaluation.CreatedAt
                            : evaluation.UpdatedAt)
                    .First());

        Projects = projects
            .Select(project =>
            {
                evaluationByProject.TryGetValue(
                    project.Id,
                    out var evaluation);

                var members = BuildMemberSummaries(project);

                return new ProjectListItem
                {
                    ProjectId = project.Id,
                    IdeaId = project.ProjectIdea!.Id,
                    ProjectTitle = SafeProjectTitle(project.Title),
                    OfficialIdeaTitle = project.ProjectIdea.Title,
                    ProjectMembers = JoinMemberNames(members),
                    ProjectMemberCount = members.Count,
                    Domain = string.IsNullOrWhiteSpace(
                        project.ProjectIdea.Domain)
                        ? "Uncategorized"
                        : project.ProjectIdea.Domain,
                    Status = NormalizeStatus(evaluation?.Status),
                    CreatedAt = project.ProjectIdea.CreatedAt
                };
            })
            .ToList();

        var selectedProject = ResolveSelectedProject(
            projects,
            projectId,
            ideaId);

        if (selectedProject?.ProjectIdea == null)
        {
            SelectedProject = null;
            return;
        }

        evaluationByProject.TryGetValue(
            selectedProject.Id,
            out var selectedEvaluation);

        var selectedIdea = selectedProject.ProjectIdea;

        var selectedRoadmap = await db.ProjectRoadmaps
            .AsNoTracking()
            .Include(roadmap => roadmap.Phases)
            .Where(roadmap =>
                roadmap.IdeaId == selectedIdea.Id)
            .OrderByDescending(roadmap => roadmap.CreatedAt)
            .FirstOrDefaultAsync(cancellationToken);

        var selectedPhases = selectedRoadmap?.Phases
            .OrderBy(phase => phase.PhaseNumber)
            .ToList() ?? [];

        var selectedMembers = BuildMemberSummaries(
            selectedProject);

        SelectedProject = BuildProgressProject(
            selectedProject,
            selectedIdea,
            selectedEvaluation,
            selectedPhases,
            selectedMembers);
    }

    private ProgressProject BuildProgressProject(
        Project project,
        ProjectIdea idea,
        SupervisorEvaluation? evaluation,
        IReadOnlyList<RoadmapPhase> phases,
        IReadOnlyList<MemberSummary> members)
    {
        var status = NormalizeStatus(evaluation?.Status);
        var startDate = idea.CreatedAt.Date;

        var orderedPhases = phases
            .OrderBy(phase => phase.PhaseNumber)
            .ToList();

        var plannedWeeks = orderedPhases.Count > 0
            ? orderedPhases.Sum(
                phase => Math.Max(
                    1,
                    phase.EstimatedWeeks))
            : idea.ExpectedDurationWeeks > 0
                ? idea.ExpectedDurationWeeks
                : 16;

        var endDate = startDate.AddDays(
            plannedWeeks * 7);

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

        var nextStep = roadmap.FirstOrDefault(
            step =>
                step.State == "current" ||
                step.State == "upcoming");

        var risks = BuildRisks(
            idea,
            evaluation,
            status);

        var updates = BuildUpdates(
            idea,
            evaluation,
            status,
            members);

        return new ProgressProject
        {
            ProjectId = project.Id,
            IdeaId = idea.Id,
            ProjectName = SafeProjectTitle(project.Title),
            ProjectMembers = JoinMemberNames(members),
            ProjectMemberCount = members.Count,
            ProjectInitials = Initials(project.Title),
            ProjectCode = $"FYP-{project.Id:0000}",
            OfficialIdeaTitle = idea.Title,
            Department = "Computer Science",
            Domain = string.IsNullOrWhiteSpace(idea.Domain)
                ? "Uncategorized"
                : idea.Domain,
            DifficultyLevel =
                string.IsNullOrWhiteSpace(idea.DifficultyLevel)
                    ? "Not specified"
                    : idea.DifficultyLevel,
            SupervisorName = CurrentSupervisorName(),
            Status = status,
            StatusLabel = ProjectStatusLabel(status),
            StatusClass = ProjectStatusClass(status),
            StartDate = startDate,
            EndDate = endDate,
            OverallProgress = progress,
            MilestonesCompleted = completedPhases,
            MilestonesTotal = totalPhases,
            DaysElapsed = Math.Min(daysElapsed, totalDays),
            TotalDays = totalDays,
            DaysRemaining = daysRemaining,
            DeliverablesSubmitted = completedPhases,
            DeliverablesTotal = totalPhases,
            NextDue = nextStep?.Title ??
                (totalPhases == 0
                    ? "Roadmap not generated"
                    : "All phases completed"),
            NextDueDate = nextStep?.Date ?? endDate,
            Roadmap = roadmap,
            Risks = risks,
            Updates = updates,
            OriginalityScore =
                evaluation?.OriginalityScore ?? 0,
            SimilarityScore =
                evaluation?.SimilarityScore ?? 0
        };
    }

    private static Project? ResolveSelectedProject(
        IReadOnlyList<Project> projects,
        int? projectId,
        int? ideaId)
    {
        if (projectId.HasValue)
        {
            var byProject = projects.FirstOrDefault(
                project =>
                    project.Id == projectId.Value);

            if (byProject != null)
            {
                return byProject;
            }
        }

        if (ideaId.HasValue)
        {
            var byIdea = projects.FirstOrDefault(
                project =>
                    project.ProjectIdeaId == ideaId.Value);

            if (byIdea != null)
            {
                return byIdea;
            }
        }

        return projects.FirstOrDefault();
    }

    private static List<MemberSummary> BuildMemberSummaries(
        Project project)
    {
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
                project.Student?.FullName ??
                project.ProjectIdea?.User?.FullName ??
                "Project owner"));
        }

        return members
            .GroupBy(member => member.UserId)
            .Select(group => group.First())
            .OrderBy(member => member.FullName)
            .ToList();
    }

    private static List<RoadmapStep> BuildRoadmap(
        IReadOnlyList<RoadmapPhase> phases,
        DateTime startDate)
    {
        var steps = new List<RoadmapStep>();
        var phaseDate = startDate;
        var currentPhaseAssigned = false;

        foreach (var phase in phases
                     .OrderBy(phase => phase.PhaseNumber))
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
                Title = string.IsNullOrWhiteSpace(phase.Name)
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

    private static List<RiskItem> BuildRisks(
        ProjectIdea idea,
        SupervisorEvaluation? evaluation,
        string status)
    {
        var risks = new List<RiskItem>();

        if (status == "needs_revision")
        {
            risks.Add(new RiskItem
            {
                Title = "Revision Required",
                Description =
                    string.IsNullOrWhiteSpace(
                        evaluation?.ImprovementSuggestions)
                        ? "The project team needs supervisor guidance before moving forward."
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
                Description =
                    "The official proposal was rejected and the project team needs a new direction.",
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
                Description =
                    "The official idea has no project domain, which can reduce tracking clarity.",
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
                Description =
                    "No major blocker is currently detected from the shared project review data.",
                Severity = "Low",
                IdentifiedOn = DateTime.UtcNow.Date,
                Impact = "Low"
            });
        }

        return risks
            .Take(3)
            .ToList();
    }

    private List<UpdateItem> BuildUpdates(
        ProjectIdea idea,
        SupervisorEvaluation? evaluation,
        string status,
        IReadOnlyList<MemberSummary> members)
    {
        var updates = new List<UpdateItem>
        {
            new()
            {
                Title = "Official project idea selected",
                Subtitle = idea.Title,
                Date = idea.CreatedAt,
                Type = "Project",
                By = members.Count == 0
                    ? "Project team"
                    : JoinMemberNames(members),
                Icon = "bi bi-file-earmark-text"
            }
        };

        if (evaluation != null)
        {
            updates.Add(new UpdateItem
            {
                Title = status switch
                {
                    "approved" =>
                        "Project idea approved by supervisor",
                    "needs_revision" =>
                        "Revision feedback added",
                    "rejected" =>
                        "Project idea rejected by supervisor",
                    _ =>
                        "Supervisor review is pending"
                },
                Subtitle =
                    string.IsNullOrWhiteSpace(evaluation.Comment)
                        ? "Shared evaluation status updated."
                        : evaluation.Comment,
                Date = evaluation.UpdatedAt == default
                    ? evaluation.CreatedAt
                    : evaluation.UpdatedAt,
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
            .OrderByDescending(update => update.Date)
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
            return "PR";
        }

        var parts = name
            .Split(
                ' ',
                StringSplitOptions.RemoveEmptyEntries)
            .Take(2)
            .ToList();

        return parts.Count == 0
            ? "PR"
            : string.Join(
                    "",
                    parts.Select(part => part[0]))
                .ToUpperInvariant();
    }

    private static string JoinMemberNames(
        IEnumerable<MemberSummary> members)
    {
        return string.Join(
            ", ",
            members.Select(member => member.FullName));
    }

    private static string SafeProjectTitle(string? title)
    {
        return string.IsNullOrWhiteSpace(title)
            ? "Untitled Project"
            : title.Trim();
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

    private sealed record MemberSummary(
        int UserId,
        string FullName);

    public sealed class ProjectListItem
    {
        public int ProjectId { get; set; }
        public int IdeaId { get; set; }
        public string ProjectTitle { get; set; } = "";
        public string OfficialIdeaTitle { get; set; } = "";
        public string ProjectMembers { get; set; } = "";
        public int ProjectMemberCount { get; set; }
        public string Domain { get; set; } = "";
        public string Status { get; set; } = "pending";
        public DateTime CreatedAt { get; set; }
    }

    public sealed class ProgressProject
    {
        public int ProjectId { get; set; }
        public int IdeaId { get; set; }
        public string ProjectName { get; set; } = "";
        public string ProjectMembers { get; set; } = "";
        public int ProjectMemberCount { get; set; }
        public string ProjectInitials { get; set; } = "PR";
        public string ProjectCode { get; set; } = "";
        public string OfficialIdeaTitle { get; set; } = "";
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