using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class IdeaReviewModel(ApplicationDbContext db) : PageModel
{
    public List<ProjectIdea>       AllIdeas     { get; private set; } = [];
    public ProjectIdea?            SelectedIdea { get; private set; }
    public SupervisorEvaluation?   Evaluation   { get; private set; }

    public async Task OnGetAsync(int? ideaId)
    {
        AllIdeas = await db.ProjectIdeas.Include(i => i.User)
            .OrderByDescending(i => i.CreatedAt).Take(30).ToListAsync();

        if (ideaId.HasValue)
        {
            SelectedIdea = AllIdeas.FirstOrDefault(i => i.Id == ideaId)
                        ?? await db.ProjectIdeas.Include(i => i.User).FirstOrDefaultAsync(i => i.Id == ideaId);
            Evaluation = await db.SupervisorEvaluations
                .FirstOrDefaultAsync(e => e.IdeaId == ideaId && e.SupervisorId == SupervisorId());
        }
    }

    public async Task<IActionResult> OnPostAsync(int IdeaId, string Status, string Comment,
        string ImprovementSuggestions, int OriginalityScore, int SimilarityScore)
    {
        var supId = SupervisorId();
        var eval  = await db.SupervisorEvaluations.FirstOrDefaultAsync(e => e.IdeaId == IdeaId && e.SupervisorId == supId);

        if (eval == null)
        {
            db.SupervisorEvaluations.Add(new SupervisorEvaluation
            {
                IdeaId = IdeaId, SupervisorId = supId, Status = Status,
                Comment = Comment, ImprovementSuggestions = ImprovementSuggestions,
                OriginalityScore = OriginalityScore, SimilarityScore = SimilarityScore,
                UpdatedAt = DateTime.UtcNow,
            });
        }
        else
        {
            eval.Status = Status; eval.Comment = Comment;
            eval.ImprovementSuggestions = ImprovementSuggestions;
            eval.OriginalityScore = OriginalityScore; eval.SimilarityScore = SimilarityScore;
            eval.UpdatedAt = DateTime.UtcNow;
        }
        await db.SaveChangesAsync();
        TempData["Success"] = "Evaluation saved successfully.";
        return RedirectToPage(new { ideaId = IdeaId });
    }

    private int SupervisorId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
