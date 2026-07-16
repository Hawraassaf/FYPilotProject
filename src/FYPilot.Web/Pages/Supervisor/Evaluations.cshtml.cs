using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class EvaluationsModel(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess) : PageModel
{
    public List<EvalRow> Evals { get; private set; } = [];

    public record EvalRow(
        int IdeaId,
        string StudentName,
        string IdeaTitle,
        string Domain,
        string Status,
        int OriginalityScore,
        int SimilarityScore,
        string Comment,
        string ImprovementSuggestions,
        DateTime UpdatedAt
    );

    public async Task OnGetAsync()
    {
        var supervisorId = SupervisorId();

        var assignedStudentIds = await supervisorAccess.GetAssignedStudentIdsAsync(supervisorId);

        var evals = await db.SupervisorEvaluations
            .AsNoTracking()
            .Include(e => e.Idea)
                .ThenInclude(i => i!.User)
            .Where(e =>
                e.SupervisorId == supervisorId &&
                e.Idea != null &&
                assignedStudentIds.Contains(e.Idea.UserId))
            .OrderByDescending(e => e.UpdatedAt)
            .ToListAsync();

        Evals = evals
            .Select(e => new EvalRow(
                e.IdeaId,
                e.Idea?.User?.FullName ?? "Student",
                e.Idea?.Title ?? "Untitled Project",
                string.IsNullOrWhiteSpace(e.Idea?.Domain) ? "Uncategorized" : e.Idea.Domain,
                NormalizeStatus(e.Status),
                Math.Clamp(e.OriginalityScore, 0, 100),
                Math.Clamp(e.SimilarityScore, 0, 100),
                e.Comment ?? "",
                e.ImprovementSuggestions ?? "",
                e.UpdatedAt == default ? e.CreatedAt : e.UpdatedAt
            ))
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

    private int SupervisorId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}