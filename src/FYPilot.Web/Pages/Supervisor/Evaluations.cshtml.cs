using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class EvaluationsModel(ApplicationDbContext db) : PageModel
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
        var supId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        var evals = await db.SupervisorEvaluations
            .AsNoTracking()
            .Include(e => e.Idea)
                .ThenInclude(i => i!.User)
            .Where(e => e.SupervisorId == supId)
            .OrderByDescending(e => e.UpdatedAt)
            .ToListAsync();

        Evals = evals
            .Select(e => new EvalRow(
                e.IdeaId,
                e.Idea?.User?.FullName ?? "Student",
                e.Idea?.Title ?? "Untitled Project",
                string.IsNullOrWhiteSpace(e.Idea?.Domain) ? "Uncategorized" : e.Idea.Domain,
                string.IsNullOrWhiteSpace(e.Status) ? "pending" : e.Status,
                Math.Clamp(e.OriginalityScore, 0, 100),
                Math.Clamp(e.SimilarityScore, 0, 100),
                e.Comment ?? "",
                e.ImprovementSuggestions ?? "",
                e.UpdatedAt
            ))
            .ToList();
    }
}
