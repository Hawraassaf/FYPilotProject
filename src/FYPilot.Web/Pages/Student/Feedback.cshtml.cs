using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class FeedbackModel(ApplicationDbContext db) : PageModel
{
    public List<FeedbackItem> Feedbacks { get; private set; } = [];

    public record FeedbackItem(
        ProjectIdea Idea,
        SupervisorEvaluation Evaluation,
        string SupervisorName);

    public async Task OnGetAsync()
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        var ideaIds = await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .Select(i => i.Id)
            .ToListAsync();

        var evals = await db.SupervisorEvaluations
            .Where(e => ideaIds.Contains(e.IdeaId))
            .Include(e => e.Idea)
            .Include(e => e.Supervisor)
            .OrderByDescending(e => e.UpdatedAt)
            .ToListAsync();

        Feedbacks = evals.Select(e => new FeedbackItem(
            e.Idea!,
            e,
            e.Supervisor?.FullName ?? "Supervisor"
        )).ToList();
    }
}
