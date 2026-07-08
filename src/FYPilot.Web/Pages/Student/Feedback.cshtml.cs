using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class FeedbackModel(ApplicationDbContext db) : PageModel
{
    public List<FeedbackItem> Feedbacks { get; private set; } = [];

    public FeedbackItem? SelectedFeedback { get; private set; }

    public int CurrentUserId { get; private set; }

    [BindProperty]
    public int SelectedEvaluationId { get; set; }

    [BindProperty]
    public string ReplyText { get; set; } = "";

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public record FeedbackItem(
        ProjectIdea Idea,
        SupervisorEvaluation Evaluation,
        string SupervisorName,
        List<FeedbackMessage> Messages);

    public async Task OnGetAsync(int? evaluationId)
    {
        await LoadFeedbackAsync(evaluationId);
    }

    public async Task<IActionResult> OnPostReplyAsync()
    {
        CurrentUserId = GetCurrentUserId();

        if (SelectedEvaluationId <= 0)
        {
            ErrorMessage = "Please select a feedback item before sending a reply.";
            return RedirectToPage();
        }

        if (string.IsNullOrWhiteSpace(ReplyText))
        {
            ErrorMessage = "Please write a reply before sending.";
            return RedirectToPage(new { evaluationId = SelectedEvaluationId });
        }

        var evaluation = await db.SupervisorEvaluations
            .Include(e => e.Idea)
            .FirstOrDefaultAsync(e =>
                e.Id == SelectedEvaluationId &&
                e.Idea != null &&
                e.Idea.UserId == CurrentUserId);

        if (evaluation == null)
        {
            ErrorMessage = "This feedback was not found or does not belong to your account.";
            return RedirectToPage();
        }

        db.FeedbackMessages.Add(new FeedbackMessage
        {
            EvaluationId = SelectedEvaluationId,
            SenderUserId = CurrentUserId,
            MessageText = ReplyText.Trim(),
            CreatedAt = DateTime.UtcNow
        });

        await db.SaveChangesAsync();

        SuccessMessage = "Reply sent successfully.";

        return RedirectToPage(new { evaluationId = SelectedEvaluationId });
    }

    private async Task LoadFeedbackAsync(int? evaluationId)
    {
        CurrentUserId = GetCurrentUserId();

        var ideaIds = await db.ProjectIdeas
            .Where(i => i.UserId == CurrentUserId)
            .Select(i => i.Id)
            .ToListAsync();

        if (!ideaIds.Any())
        {
            Feedbacks = [];
            SelectedFeedback = null;
            return;
        }

        var evals = await db.SupervisorEvaluations
            .Where(e => ideaIds.Contains(e.IdeaId))
            .Include(e => e.Idea)
            .Include(e => e.Supervisor)
            .OrderByDescending(e => e.UpdatedAt)
            .ToListAsync();

        var evaluationIds = evals
            .Select(e => e.Id)
            .ToList();

        var messages = await db.FeedbackMessages
            .Where(m => evaluationIds.Contains(m.EvaluationId))
            .OrderBy(m => m.CreatedAt)
            .ToListAsync();

        Feedbacks = evals
            .Where(e => e.Idea != null)
            .Select(e => new FeedbackItem(
                e.Idea!,
                e,
                e.Supervisor?.FullName ?? "Supervisor",
                messages.Where(m => m.EvaluationId == e.Id).ToList()
            ))
            .ToList();

        if (!Feedbacks.Any())
        {
            SelectedFeedback = null;
            return;
        }

        SelectedFeedback = evaluationId.HasValue
            ? Feedbacks.FirstOrDefault(f => f.Evaluation.Id == evaluationId.Value)
            : Feedbacks.FirstOrDefault();

        SelectedFeedback ??= Feedbacks.FirstOrDefault();

        SelectedEvaluationId = SelectedFeedback?.Evaluation.Id ?? 0;
    }

    private int GetCurrentUserId()
    {
        var userIdClaim = User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdClaim, out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException("Unable to identify the current logged-in user.");
    }
}
