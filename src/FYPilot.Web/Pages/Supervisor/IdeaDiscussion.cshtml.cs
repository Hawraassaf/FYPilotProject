using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class IdeaDiscussionModel(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess) : PageModel
{
    public ProjectIdea? Idea { get; private set; }
    public SupervisorEvaluation? Evaluation { get; private set; }
    public List<FeedbackMessageItem> Messages { get; private set; } = [];

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    [BindProperty]
    public MessageInputModel MessageInput { get; set; } = new();

    public sealed class FeedbackMessageItem
    {
        public int Id { get; set; }
        public int SenderUserId { get; set; }
        public string SenderName { get; set; } = "";
        public string SenderRole { get; set; } = "";
        public bool IsSupervisor { get; set; }
        public string MessageText { get; set; } = "";
        public DateTime CreatedAt { get; set; }
        public DateTime? SeenAt { get; set; }
        public DateTime? EditedAt { get; set; }
        public DateTime? DeletedAt { get; set; }
        public int? ReplyToMessageId { get; set; }
        public string? ReplyPreview { get; set; }
    }

    public sealed class MessageInputModel
    {
        public int IdeaId { get; set; }
        public string? MessageText { get; set; }
    }

    public async Task<IActionResult> OnGetAsync(int ideaId)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        await LoadDiscussionAsync(ideaId);

        if (Idea == null)
        {
            TempData["Error"] = "The selected idea was not found or it does not belong to one of your assigned students.";
            return RedirectToPage("/Supervisor/IdeaReview");
        }

        return Page();
    }

    public async Task<IActionResult> OnPostSendMessageAsync()
    {
        var supervisorId = SupervisorId();

        if (MessageInput.IdeaId <= 0)
        {
            TempData["Error"] = "Please select an idea before sending a message.";
            return RedirectToPage("/Supervisor/IdeaReview");
        }

        if (string.IsNullOrWhiteSpace(MessageInput.MessageText))
        {
            TempData["Error"] = "Please write a message before sending.";
            return RedirectToPage(new { ideaId = MessageInput.IdeaId });
        }

        var idea = await db.ProjectIdeas
        .AsNoTracking()
        .FirstOrDefaultAsync(i =>
            i.Id == MessageInput.IdeaId &&
            i.IsSelected);

        if (idea == null)
        {
            TempData["Error"] = "The selected idea was not found.";
            return RedirectToPage("/Supervisor/IdeaReview");
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            idea.UserId);

        if (!canAccessStudent)
        {
            TempData["Error"] = "You can only discuss ideas submitted by students assigned to you.";
            return RedirectToPage("/Supervisor/IdeaReview");
        }

        var evaluation = await GetOrCreateEvaluationAsync(MessageInput.IdeaId, supervisorId);

        db.FeedbackMessages.Add(new FeedbackMessage
        {
            EvaluationId = evaluation.Id,
            SenderUserId = supervisorId,
            MessageText = MessageInput.MessageText.Trim(),
            CreatedAt = DateTime.UtcNow
        });

        await db.SaveChangesAsync();

        TempData["Success"] = "Message sent successfully.";
        return RedirectToPage(new { ideaId = MessageInput.IdeaId });
    }

    private async Task LoadDiscussionAsync(int ideaId)
    {
        var supervisorId = SupervisorId();

        var idea = await db.ProjectIdeas
           .AsNoTracking()
           .Include(i => i.User)
           .FirstOrDefaultAsync(i =>
               i.Id == ideaId &&
               i.IsSelected);

        if (idea == null)
        {
            Idea = null;
            Evaluation = null;
            Messages = [];
            return;
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            idea.UserId);

        if (!canAccessStudent)
        {
            Idea = null;
            Evaluation = null;
            Messages = [];
            return;
        }

        Idea = idea;

        // Opening the page must not create a new evaluation.
        // Only load an evaluation when one already exists.
        var existingEvaluation = await db.SupervisorEvaluations
            .AsNoTracking()
            .FirstOrDefaultAsync(e =>
                e.IdeaId == ideaId &&
                e.SupervisorId == supervisorId);

        Evaluation = existingEvaluation;

        MessageInput = new MessageInputModel
        {
            IdeaId = ideaId
        };

        if (existingEvaluation == null)
        {
            Messages = [];
            return;
        }

        var rawMessages = await db.FeedbackMessages
            .AsNoTracking()
            .Where(m =>
                m.EvaluationId == existingEvaluation.Id)
            .OrderBy(m => m.CreatedAt)
            .ToListAsync();

        var messageById = rawMessages.ToDictionary(m => m.Id);

        Messages = rawMessages
            .Select(m =>
            {
                FeedbackMessage? replyTarget = null;

                if (m.ReplyToMessageId.HasValue)
                {
                    messageById.TryGetValue(m.ReplyToMessageId.Value, out replyTarget);
                }

                return new FeedbackMessageItem
                {
                    Id = m.Id,
                    SenderUserId = m.SenderUserId,
                    SenderName = m.SenderUserId == supervisorId
                        ? "You"
                        : (Idea.User != null ? Idea.User.FullName : "Student"),
                    SenderRole = m.SenderUserId == supervisorId ? "Supervisor" : "Student",
                    IsSupervisor = m.SenderUserId == supervisorId,
                    MessageText = m.MessageText,
                    CreatedAt = m.CreatedAt,
                    SeenAt = m.SeenAt,
                    EditedAt = m.EditedAt,
                    DeletedAt = m.DeletedAt,
                    ReplyToMessageId = m.ReplyToMessageId,
                    ReplyPreview = replyTarget == null || replyTarget.DeletedAt != null
                        ? null
                        : TrimPreview(replyTarget.MessageText)
                };
            })
            .ToList();
    }

    private async Task<SupervisorEvaluation> GetOrCreateEvaluationAsync(int ideaId, int supervisorId)
    {
        var evaluation = await db.SupervisorEvaluations
            .FirstOrDefaultAsync(e => e.IdeaId == ideaId && e.SupervisorId == supervisorId);

        if (evaluation != null)
        {
            return evaluation;
        }

        evaluation = new SupervisorEvaluation
        {
            IdeaId = ideaId,
            SupervisorId = supervisorId,
            Status = "pending",
            Comment = "",
            ImprovementSuggestions = "",
            OriginalityScore = 0,
            SimilarityScore = 0,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };

        db.SupervisorEvaluations.Add(evaluation);
        await db.SaveChangesAsync();

        return evaluation;
    }

    private static string TrimPreview(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }

        value = value.Trim();

        return value.Length <= 80 ? value : value[..80] + "...";
    }

    private int SupervisorId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}