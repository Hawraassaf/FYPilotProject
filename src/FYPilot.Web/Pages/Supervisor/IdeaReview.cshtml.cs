using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using System.Security.Claims;
using FYPilot.Web.Services.Notifications;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class IdeaReviewModel(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess,
    INotificationService notificationService,
    ILogger<IdeaReviewModel> logger) : PageModel
{
    public List<IdeaListItem> AllIdeas { get; private set; } = [];
    public ProjectIdea? SelectedIdea { get; private set; }
    public SupervisorEvaluation? Evaluation { get; private set; }
    public List<FeedbackMessageItem> Messages { get; private set; } = [];

    public int TotalIdeas { get; private set; }
    public int PendingReviews { get; private set; }
    public int ReviewedIdeas { get; private set; }
    public int NeedsRevisionIdeas { get; private set; }

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    [BindProperty]
    public EvaluationInputModel EvaluationInput { get; set; } = new();

    [BindProperty]
    public MessageInputModel MessageInput { get; set; } = new();

    public sealed class IdeaListItem
    {
        public int Id { get; set; }
        public string Title { get; set; } = "";
        public string StudentName { get; set; } = "";
        public string? Domain { get; set; }
        public string? DifficultyLevel { get; set; }
        public DateTime CreatedAt { get; set; }
        public int FeasibilityScore { get; set; }
        public string Status { get; set; } = "pending";
        public bool IsSelected { get; set; }
    }

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

    public sealed class EvaluationInputModel
    {
        public int IdeaId { get; set; }
        public string Status { get; set; } = "pending";
        public string? Comment { get; set; }
        public string? ImprovementSuggestions { get; set; }
        public int? OriginalityScore { get; set; }
        public int? SimilarityScore { get; set; }
    }

    public sealed class MessageInputModel
    {
        public int IdeaId { get; set; }
        public string? MessageText { get; set; }
    }

    public async Task OnGetAsync(int? ideaId)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        await LoadPageAsync(ideaId);
    }

    public async Task<IActionResult> OnPostSaveEvaluationAsync()
    {
        var supervisorId = SupervisorId();

        var idea = await db.ProjectIdeas
      .AsNoTracking()
      .FirstOrDefaultAsync(i =>
          i.Id == EvaluationInput.IdeaId &&
          i.IsSelected);

        if (idea == null)
        {
            TempData["Error"] = "This idea is no longer the student's selected idea.";
            return RedirectToPage();
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            idea.UserId);

        if (!canAccessStudent)
        {
            TempData["Error"] = "You can only review ideas for students assigned to you.";
            return RedirectToPage();
        }

        var status = NormalizeStatus(EvaluationInput.Status);

        var comment =
            EvaluationInput.Comment?.Trim() ?? "";

        var suggestions =
            EvaluationInput.ImprovementSuggestions?.Trim() ?? "";

        if (EvaluationInput.OriginalityScore.HasValue &&
            (EvaluationInput.OriginalityScore.Value < 0 ||
             EvaluationInput.OriginalityScore.Value > 100))
        {
            TempData["Error"] =
                "Originality score must be between 0 and 100.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        if (EvaluationInput.SimilarityScore.HasValue &&
            (EvaluationInput.SimilarityScore.Value < 0 ||
             EvaluationInput.SimilarityScore.Value > 100))
        {
            TempData["Error"] =
                "Similarity score must be between 0 and 100.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        // Final decisions must include both scores.
        if (status != "pending" &&
            (!EvaluationInput.OriginalityScore.HasValue ||
             !EvaluationInput.SimilarityScore.HasValue))
        {
            TempData["Error"] =
                "Enter both originality and similarity scores before saving a final decision.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        if (status == "approved" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a supervisor comment explaining why the idea was approved.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        if (status == "needs_revision" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a comment explaining what needs revision.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        if (status == "needs_revision" &&
            string.IsNullOrWhiteSpace(suggestions))
        {
            TempData["Error"] =
                "Write improvement suggestions for the student.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        if (status == "rejected" &&
            string.IsNullOrWhiteSpace(comment))
        {
            TempData["Error"] =
                "Write a clear reason before rejecting the idea.";

            return RedirectToPage(new
            {
                ideaId = EvaluationInput.IdeaId
            });
        }

        var originalityScore =
            EvaluationInput.OriginalityScore ?? 0;

        var similarityScore =
            EvaluationInput.SimilarityScore ?? 0;

        var evaluation = await db.SupervisorEvaluations
            .FirstOrDefaultAsync(e => e.IdeaId == EvaluationInput.IdeaId && e.SupervisorId == supervisorId);
        var previousStatus = evaluation == null
    ? null
    : NormalizeStatus(evaluation.Status);
        if (evaluation == null)
        {
            evaluation = new SupervisorEvaluation
            {
                IdeaId = EvaluationInput.IdeaId,
                SupervisorId = supervisorId,
                CreatedAt = DateTime.UtcNow
            };

            db.SupervisorEvaluations.Add(evaluation);
        }

        evaluation.Status = status;
        evaluation.Comment = comment;
        evaluation.ImprovementSuggestions = suggestions;
        evaluation.OriginalityScore = originalityScore;
        evaluation.SimilarityScore = similarityScore;
        evaluation.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        // Notify only when a real decision changes.
        // Saving the same status again does not create duplicate notifications.
        if (status != "pending" &&
            !string.Equals(
                previousStatus,
                status,
                StringComparison.OrdinalIgnoreCase))
        {
            var notificationTitle = status switch
            {
                "approved" => "Project Idea Approved",
                "needs_revision" => "Project Idea Needs Revision",
                "rejected" => "Project Idea Rejected",
                _ => "Project Evaluation Updated"
            };

            var notificationMessage = status switch
            {
                "approved" =>
                    $"Your project idea \"{idea.Title}\" was approved by your supervisor.",

                "needs_revision" =>
                    $"Your project idea \"{idea.Title}\" needs revision. Open the feedback page to review the supervisor's suggestions.",

                "rejected" =>
                    $"Your project idea \"{idea.Title}\" was not approved. Open the feedback page to review the supervisor's comments.",

                _ =>
                    $"The evaluation for \"{idea.Title}\" was updated."
            };

            try
            {
                await notificationService.NotifyUserAsync(
                    idea.UserId,
                    notificationTitle,
                    notificationMessage,
                    "evaluation_updated",
                    $"/Student/Feedback?ideaId={idea.Id}");
            }
            catch (Exception ex)
            {
                // Evaluation remains saved even when notification delivery fails.
                logger.LogError(
                    ex,
                    "Failed to notify student {StudentId} about evaluation for idea {IdeaId}.",
                    idea.UserId,
                    idea.Id);
            }
        }

        TempData["Success"] = "Evaluation saved successfully.";
        return RedirectToPage(new { ideaId = EvaluationInput.IdeaId });
    }

    public async Task<IActionResult> OnPostSendMessageAsync()
    {
        var supervisorId = SupervisorId();

        if (MessageInput.IdeaId <= 0)
        {
            TempData["Error"] = "Please select an idea before sending a message.";
            return RedirectToPage();
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
            TempData["Error"] = "This idea is no longer the student's selected idea.";
            return RedirectToPage();
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            idea.UserId);

        if (!canAccessStudent)
        {
            TempData["Error"] = "You can only message students assigned to you.";
            return RedirectToPage();
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

    private async Task LoadPageAsync(int? ideaId)
    {
        var supervisorId = SupervisorId();

        var assignedStudentIds = await supervisorAccess.GetAssignedStudentIdsAsync(supervisorId);

        var ideas = await db.ProjectIdeas
      .AsNoTracking()
      .Include(i => i.User)
      .Where(i => assignedStudentIds.Contains(i.UserId))
      .Where(i => i.IsSelected)
      .OrderByDescending(i => i.CreatedAt)
      .ToListAsync();

        var ideaIds = ideas.Select(i => i.Id).ToList();

        var evaluations = await db.SupervisorEvaluations
            .AsNoTracking()
            .Where(e =>
                e.SupervisorId == supervisorId &&
                ideaIds.Contains(e.IdeaId))
            .ToListAsync();

        var evaluationByIdea = evaluations
            .GroupBy(e => e.IdeaId)
            .ToDictionary(g => g.Key, g => g.OrderByDescending(e => e.UpdatedAt).First());

        TotalIdeas = ideas.Count;

        PendingReviews = ideas.Count(idea =>
            !evaluationByIdea.TryGetValue(idea.Id, out var evaluation) ||
            evaluation.Status == "pending");

        ReviewedIdeas = ideas.Count(idea =>
            evaluationByIdea.TryGetValue(idea.Id, out var evaluation) &&
            evaluation.Status != "pending");

        NeedsRevisionIdeas = ideas.Count(idea =>
            evaluationByIdea.TryGetValue(idea.Id, out var evaluation) &&
            evaluation.Status == "needs_revision");

        var selectedIdeaId =
            ideaId.HasValue && ideas.Any(i => i.Id == ideaId.Value)
                ? ideaId.Value
                : ideas.FirstOrDefault()?.Id;

        if (ideaId.HasValue && selectedIdeaId != ideaId.Value)
        {
            ErrorMessage ??= "You can only view ideas submitted by students assigned to you.";
        }

        AllIdeas = ideas
            .Select(idea =>
            {
                evaluationByIdea.TryGetValue(idea.Id, out var evaluation);

                return new IdeaListItem
                {
                    Id = idea.Id,
                    Title = idea.Title,
                    StudentName = idea.User?.FullName ?? "Student",
                    Domain = idea.Domain,
                    DifficultyLevel = idea.DifficultyLevel,
                    CreatedAt = idea.CreatedAt,
                    FeasibilityScore = idea.FeasibilityScore,
                    Status = evaluation?.Status ?? "pending",
                    IsSelected = selectedIdeaId.HasValue && idea.Id == selectedIdeaId.Value
                };
            })
            .ToList();

        if (!selectedIdeaId.HasValue)
        {
            SelectedIdea = null;
            Evaluation = null;
            Messages = [];

            EvaluationInput = new EvaluationInputModel();
            MessageInput = new MessageInputModel();

            return;
        }

        SelectedIdea = await db.ProjectIdeas
    .AsNoTracking()
    .Include(i => i.User)
    .FirstOrDefaultAsync(i =>
        i.Id == selectedIdeaId.Value &&
        i.IsSelected &&
        assignedStudentIds.Contains(i.UserId));

        if (SelectedIdea == null)
        {
            Evaluation = null;
            Messages = [];

            EvaluationInput = new EvaluationInputModel();
            MessageInput = new MessageInputModel();

            return;
        }

        Evaluation = await db.SupervisorEvaluations
            .AsNoTracking()
            .FirstOrDefaultAsync(e => e.IdeaId == SelectedIdea.Id && e.SupervisorId == supervisorId);

        EvaluationInput = new EvaluationInputModel
        {
            IdeaId = SelectedIdea.Id,
            Status = Evaluation?.Status ?? "pending",
            Comment = Evaluation?.Comment,
            ImprovementSuggestions = Evaluation?.ImprovementSuggestions,
            OriginalityScore = Evaluation != null && Evaluation.OriginalityScore > 0 ? Evaluation.OriginalityScore : null,
            SimilarityScore = Evaluation != null && Evaluation.SimilarityScore > 0 ? Evaluation.SimilarityScore : null
        };

        MessageInput = new MessageInputModel
        {
            IdeaId = SelectedIdea.Id
        };

        if (Evaluation == null)
        {
            Messages = [];
            return;
        }

        var rawMessages = await db.FeedbackMessages
            .AsNoTracking()
            .Where(m => m.EvaluationId == Evaluation.Id)
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
                        : (SelectedIdea.User != null ? SelectedIdea.User.FullName : "Student"),
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