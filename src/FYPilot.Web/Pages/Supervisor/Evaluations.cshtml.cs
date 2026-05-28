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

    public record EvalRow(int IdeaId, string StudentName, string IdeaTitle, string Domain,
        string Status, int OriginalityScore, int SimilarityScore, DateTime UpdatedAt);

    public async Task OnGetAsync()
    {
        var supId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
        var evals = await db.SupervisorEvaluations.Include(e => e.Idea).ThenInclude(i => i!.User)
                        .Where(e => e.SupervisorId == supId).ToListAsync();

        Evals = evals.Select(e => new EvalRow(
            e.IdeaId,
            e.Idea?.User?.FullName ?? "Student",
            e.Idea?.Title ?? "Untitled",
            e.Idea?.Domain ?? "",
            e.Status, e.OriginalityScore, e.SimilarityScore, e.UpdatedAt)).ToList();
    }
}
