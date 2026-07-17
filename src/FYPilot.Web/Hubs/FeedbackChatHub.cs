using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using FYPilot.Web.Services.Supervisors;

namespace FYPilot.Web.Hubs;

[Authorize]
public class FeedbackChatHub(
    ApplicationDbContext db,
    SupervisorAccessService supervisorAccess) : Hub
{
    private const int MaximumMessageLength = 2000;
    public async Task JoinFeedbackRoom(int ideaId, int evaluationId)
    {
        var userId = CurrentUserId();

        if (!await CanAccessIdeaAsync(ideaId, userId))
        {
            return;
        }

        await Groups.AddToGroupAsync(Context.ConnectionId, IdeaGroupName(ideaId));

        if (evaluationId > 0 && await CanAccessEvaluationAsync(evaluationId, userId))
        {
            await MarkSeenInternalAsync(evaluationId, ideaId, userId);
        }
    }

    public async Task SendFeedbackMessage(
        int evaluationId,
        int ideaId,
        string messageText,
        int? replyToMessageId = null)
    {
        var userId = CurrentUserId();

        if (ideaId <= 0 ||
            string.IsNullOrWhiteSpace(messageText))
        {
            throw new HubException(
                "Write a message before sending.");
        }

        var cleanMessageText = messageText.Trim();

        if (cleanMessageText.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Message cannot exceed {MaximumMessageLength} characters.");
        }

        if (!await CanAccessIdeaAsync(ideaId, userId))
        {
            return;
        }

        var evaluation = await ResolveEvaluationAsync(evaluationId, ideaId, userId);

        if (evaluation == null)
        {
            return;
        }

        FeedbackMessage? replyMessage = null;

        if (replyToMessageId.HasValue && replyToMessageId.Value > 0)
        {
            replyMessage = await db.FeedbackMessages
                .AsNoTracking()
                .FirstOrDefaultAsync(m =>
                    m.Id == replyToMessageId.Value &&
                    m.EvaluationId == evaluation.Id &&
                    m.DeletedAt == null);
        }

        var user = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == userId);

        var senderName = user?.FullName ?? "User";
        var senderRole = user?.Role ?? RoleName();

        var message = new FeedbackMessage
        {
            EvaluationId = evaluation.Id,
            SenderUserId = userId,
            MessageText = cleanMessageText,
            ReplyToMessageId = replyMessage?.Id,
            CreatedAt = DateTime.UtcNow
        };

        db.FeedbackMessages.Add(message);
        await db.SaveChangesAsync();

        await Groups.AddToGroupAsync(Context.ConnectionId, IdeaGroupName(ideaId));

        await Clients.Group(IdeaGroupName(ideaId)).SendAsync("ReceiveFeedbackMessage", new
        {
            id = message.Id,
            evaluationId = evaluation.Id,
            ideaId = evaluation.IdeaId,
            senderUserId = message.SenderUserId,
            senderName,
            senderRole,
            messageText = message.MessageText,
            createdAt = message.CreatedAt,
            seenAt = message.SeenAt,
            editedAt = message.EditedAt,
            deletedAt = message.DeletedAt,
            replyToMessageId = message.ReplyToMessageId,
            replyPreview = replyMessage == null ? null : TrimPreview(replyMessage.MessageText)
        });
    }

    public async Task EditFeedbackMessage(
     int messageId,
     string newText)
    {
        var userId = CurrentUserId();

        if (messageId <= 0 ||
            string.IsNullOrWhiteSpace(newText))
        {
            throw new HubException(
                "Edited message cannot be empty.");
        }

        var cleanText = newText.Trim();

        if (cleanText.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Message cannot exceed {MaximumMessageLength} characters.");
        }

        var message = await db.FeedbackMessages
            .Include(m => m.Evaluation)
            .FirstOrDefaultAsync(m => m.Id == messageId);

        if (message?.Evaluation == null || message.DeletedAt != null)
        {
            return;
        }

        if (message.SenderUserId != userId)
        {
            return;
        }

        if (!await CanAccessEvaluationAsync(message.EvaluationId, userId))
        {
            return;
        }

        message.MessageText = cleanText;
        message.EditedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        await Clients.Group(IdeaGroupName(message.Evaluation.IdeaId)).SendAsync("FeedbackMessageEdited", new
        {
            id = message.Id,
            evaluationId = message.EvaluationId,
            ideaId = message.Evaluation.IdeaId,
            messageText = message.MessageText,
            editedAt = message.EditedAt
        });
    }

    public async Task DeleteFeedbackMessage(int messageId)
    {
        var userId = CurrentUserId();

        if (messageId <= 0)
        {
            return;
        }

        var message = await db.FeedbackMessages
            .Include(m => m.Evaluation)
            .FirstOrDefaultAsync(m => m.Id == messageId);

        if (message?.Evaluation == null || message.DeletedAt != null)
        {
            return;
        }

        if (message.SenderUserId != userId)
        {
            return;
        }

        if (!await CanAccessEvaluationAsync(message.EvaluationId, userId))
        {
            return;
        }

        message.MessageText = "";
        message.DeletedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        await Clients.Group(IdeaGroupName(message.Evaluation.IdeaId)).SendAsync("FeedbackMessageDeleted", new
        {
            id = message.Id,
            evaluationId = message.EvaluationId,
            ideaId = message.Evaluation.IdeaId,
            deletedAt = message.DeletedAt
        });
    }

    public async Task TypingFeedbackRoom(int ideaId)
    {
        if (ideaId <= 0)
        {
            return;
        }

        var userId = CurrentUserId();

        if (!await CanAccessIdeaAsync(ideaId, userId))
        {
            return;
        }

        var user = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == userId);

        await Groups.AddToGroupAsync(Context.ConnectionId, IdeaGroupName(ideaId));

        await Clients.OthersInGroup(IdeaGroupName(ideaId)).SendAsync("FeedbackUserTyping", new
        {
            ideaId,
            userId,
            name = user?.FullName ?? "User",
            role = user?.Role ?? RoleName()
        });
    }

    public async Task StopTypingFeedbackRoom(int ideaId)
    {
        if (ideaId <= 0)
        {
            return;
        }

        var userId = CurrentUserId();

        if (!await CanAccessIdeaAsync(ideaId, userId))
        {
            return;
        }

        await Clients.OthersInGroup(IdeaGroupName(ideaId)).SendAsync("FeedbackUserStoppedTyping", new
        {
            ideaId,
            userId
        });
    }

    public async Task MarkFeedbackSeenByIdea(int ideaId, int evaluationId)
    {
        if (ideaId <= 0)
        {
            return;
        }

        var userId = CurrentUserId();

        if (!await CanAccessIdeaAsync(ideaId, userId))
        {
            return;
        }

        var evaluation = await ResolveEvaluationForSeenAsync(evaluationId, ideaId, userId);

        if (evaluation == null)
        {
            return;
        }

        await MarkSeenInternalAsync(evaluation.Id, ideaId, userId);
    }

    private async Task<SupervisorEvaluation?> ResolveEvaluationAsync(int evaluationId, int ideaId, int userId)
    {
        if (evaluationId > 0)
        {
            var existing = await db.SupervisorEvaluations
                .Include(e => e.Idea)
                .FirstOrDefaultAsync(e => e.Id == evaluationId && e.IdeaId == ideaId);

            if (existing != null && await CanAccessEvaluationAsync(existing.Id, userId))
            {
                return existing;
            }
        }

        var role = RoleName();

        if (role.Equals("supervisor", StringComparison.OrdinalIgnoreCase))
        {
            var existing = await db.SupervisorEvaluations
                .FirstOrDefaultAsync(e => e.IdeaId == ideaId && e.SupervisorId == userId);

            if (existing != null)
            {
                return existing;
            }

            var idea = await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(i => i.Id == ideaId);

            if (idea == null)
            {
                return null;
            }

            var evaluation = new SupervisorEvaluation
            {
                IdeaId = ideaId,
                SupervisorId = userId,
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

        if (role.Equals("student", StringComparison.OrdinalIgnoreCase))
        {
            return await db.SupervisorEvaluations
                .Include(e => e.Idea)
                .Where(e => e.IdeaId == ideaId && e.Idea != null && e.Idea.UserId == userId)
                .OrderByDescending(e => e.UpdatedAt)
                .FirstOrDefaultAsync();
        }

        return null;
    }

    private async Task<SupervisorEvaluation?> ResolveEvaluationForSeenAsync(int evaluationId, int ideaId, int userId)
    {
        if (evaluationId > 0)
        {
            var evaluation = await db.SupervisorEvaluations
                .AsNoTracking()
                .Include(e => e.Idea)
                .FirstOrDefaultAsync(e => e.Id == evaluationId && e.IdeaId == ideaId);

            if (evaluation != null && await CanAccessEvaluationAsync(evaluation.Id, userId))
            {
                return evaluation;
            }
        }

        var role = RoleName();

        if (role.Equals("supervisor", StringComparison.OrdinalIgnoreCase))
        {
            return await db.SupervisorEvaluations
                .AsNoTracking()
                .FirstOrDefaultAsync(e => e.IdeaId == ideaId && e.SupervisorId == userId);
        }

        if (role.Equals("student", StringComparison.OrdinalIgnoreCase))
        {
            return await db.SupervisorEvaluations
                .AsNoTracking()
                .Include(e => e.Idea)
                .Where(e => e.IdeaId == ideaId && e.Idea != null && e.Idea.UserId == userId)
                .OrderByDescending(e => e.UpdatedAt)
                .FirstOrDefaultAsync();
        }

        return null;
    }

    private async Task MarkSeenInternalAsync(int evaluationId, int ideaId, int userId)
    {
        var now = DateTime.UtcNow;

        var messages = await db.FeedbackMessages
            .Where(m =>
                m.EvaluationId == evaluationId &&
                m.SenderUserId != userId &&
                m.SeenAt == null &&
                m.DeletedAt == null)
            .ToListAsync();

        if (!messages.Any())
        {
            return;
        }

        foreach (var message in messages)
        {
            message.SeenAt = now;
        }

        await db.SaveChangesAsync();

        await Clients.Group(IdeaGroupName(ideaId)).SendAsync("FeedbackMessagesSeen", new
        {
            evaluationId,
            ideaId,
            seenByUserId = userId,
            seenAt = now
        });
    }

    private async Task<bool> CanAccessEvaluationAsync(int evaluationId, int userId)
    {
        var role = RoleName();

        var evaluation = await db.SupervisorEvaluations
            .AsNoTracking()
            .Include(e => e.Idea)
            .FirstOrDefaultAsync(e => e.Id == evaluationId);

        if (evaluation?.Idea == null)
        {
            return false;
        }

        if (role.Equals(
               "supervisor",
               StringComparison.OrdinalIgnoreCase))
        {
            if (evaluation.SupervisorId != userId)
            {
                return false;
            }

            return await supervisorAccess.CanAccessStudentAsync(
                userId,
                evaluation.Idea.UserId);
        }

        if (role.Equals("student", StringComparison.OrdinalIgnoreCase))
        {
            return evaluation.Idea.UserId == userId;
        }

        return false;
    }

    private async Task<bool> CanAccessIdeaAsync(
     int ideaId,
     int userId)
    {
        var role = RoleName();

        var idea = await db.ProjectIdeas
            .AsNoTracking()
            .FirstOrDefaultAsync(i => i.Id == ideaId);

        if (idea == null)
        {
            return false;
        }

        if (role.Equals(
                "supervisor",
                StringComparison.OrdinalIgnoreCase))
        {
            // A supervisor can access the idea only when the
            // idea owner is actively assigned to that supervisor.
            return await supervisorAccess.CanAccessStudentAsync(
                userId,
                idea.UserId);
        }

        if (role.Equals(
                "student",
                StringComparison.OrdinalIgnoreCase))
        {
            // A student can access only their own idea.
            return idea.UserId == userId;
        }

        return false;
    }

    private int CurrentUserId()
    {
        var value = Context.User?.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(value, out var userId))
        {
            return userId;
        }

        throw new HubException("User is not authenticated.");
    }

    private string RoleName()
    {
        return Context.User?.FindFirst(ClaimTypes.Role)?.Value ?? "";
    }

    private static string IdeaGroupName(int ideaId)
    {
        return $"feedback-idea-{ideaId}";
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
}
