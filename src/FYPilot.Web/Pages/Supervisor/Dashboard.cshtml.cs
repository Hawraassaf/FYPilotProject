using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class DashboardModel(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess) : PageModel
{
    public List<StatCard> Stats { get; private set; } = [];
    public List<PendingItem> PendingIdeas { get; private set; } = [];
    public List<EvalItem> RecentEvals { get; private set; } = [];

    public record StatCard(
        string Label,
        string Value,
        string Icon,
        string Color,
        string BgColor);

    public record PendingItem(
        int IdeaId,
        string StudentName,
        string IdeaTitle,
        string Domain,
        int Feasibility,
        int Innovation,
        string Status);

    public record EvalItem(
        string StudentName,
        string IdeaTitle,
        string Status);

    public async Task OnGetAsync()
    {
        var supervisorId = int.Parse(
            User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        // Only students actively assigned to this supervisor.
        var assignedStudentIds =
            await supervisorAccess.GetAssignedStudentIdsAsync(
                supervisorId);

        // Load the selected idea of each assigned student.
        var selectedIdeaEntities = await db.ProjectIdeas
            .AsNoTracking()
            .Include(i => i.User)
            .Where(i =>
                i.IsSelected &&
                assignedStudentIds.Contains(i.UserId))
            .OrderByDescending(i => i.CreatedAt)
            .ToListAsync();

        // Protect against inconsistent data where a student
        // may accidentally have more than one selected idea.
        var selectedIdeas = selectedIdeaEntities
            .GroupBy(i => i.UserId)
            .Select(group => group.First())
            .ToList();

        var ideaIds = selectedIdeas
            .Select(i => i.Id)
            .ToList();

        List<SupervisorEvaluation> evaluations;

        if (ideaIds.Count == 0)
        {
            evaluations = [];
        }
        else
        {
            evaluations = await db.SupervisorEvaluations
                .AsNoTracking()
                .Where(e =>
                    e.SupervisorId == supervisorId &&
                    ideaIds.Contains(e.IdeaId))
                .ToListAsync();
        }

        // Use only the latest evaluation for every idea.
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

        var pendingCount = selectedIdeas.Count(idea =>
            !latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) ||
            NormalizeStatus(evaluation.Status) == "pending");

        var approvedCount = selectedIdeas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) == "approved");

        var reviewedCount = selectedIdeas.Count(idea =>
            latestEvaluationByIdea.TryGetValue(
                idea.Id,
                out var evaluation) &&
            NormalizeStatus(evaluation.Status) != "pending");

        Stats =
        [
            new(
                "Pending Reviews",
                pendingCount.ToString(),
                "card-checklist",
                "#f59e0b",
                "#fffbeb"),

            new(
                "Approved Ideas",
                approvedCount.ToString(),
                "check-circle",
                "#10b981",
                "#f0fdf4"),

            new(
                "Total Reviews",
                reviewedCount.ToString(),
                "star-half",
                "#3b82f6",
                "#eff6ff")
        ];

        PendingIdeas = selectedIdeas
            .Where(idea =>
                !latestEvaluationByIdea.TryGetValue(
                    idea.Id,
                    out var evaluation) ||
                NormalizeStatus(evaluation.Status) == "pending")
            .Select(idea => new PendingItem(
                idea.Id,
                idea.User?.FullName ?? "Student",
                idea.Title,
                idea.Domain ?? "",
                idea.FeasibilityScore,
                idea.InnovationScore,
                "pending"))
            .ToList();

        var ideaById = selectedIdeas
            .ToDictionary(i => i.Id);

        RecentEvals = latestEvaluationByIdea.Values
            .Where(e =>
                NormalizeStatus(e.Status) != "pending" &&
                ideaById.ContainsKey(e.IdeaId))
            .OrderByDescending(e =>
                e.UpdatedAt == default
                    ? e.CreatedAt
                    : e.UpdatedAt)
            .Take(5)
            .Select(e =>
            {
                var idea = ideaById[e.IdeaId];

                return new EvalItem(
                    idea.User?.FullName ?? "Student",
                    idea.Title,
                    NormalizeStatus(e.Status));
            })
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