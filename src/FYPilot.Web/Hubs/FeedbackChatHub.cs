using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Hubs;

[Authorize]
public class FeedbackChatHub(
    ApplicationDbContext db,
    ILogger<FeedbackChatHub> logger)
    : Hub
{
    private const int MaximumMessageLength = 2000;

    public async Task JoinFeedbackRoom(
        int ideaId,
        int evaluationId)
    {
        var userId =
            CurrentUserId();

        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId,
                userId,
                createEvaluationForSupervisor: false);

        if (context == null)
        {
            throw new HubException(
                "You do not have access to this "
                + "project feedback.");
        }

        await Groups.AddToGroupAsync(
            Context.ConnectionId,
            ProjectGroupName(
                context.Project.Id),
            Context.ConnectionAborted);

        if (context.Evaluation != null &&
            context.Project.SupervisorId.HasValue)
        {
            await MarkSeenInternalAsync(
                context.Project.Id,
                context.Evaluation.IdeaId,
                context.Project.SupervisorId.Value,
                userId);
        }
    }

    public async Task LeaveFeedbackRoom(
        int ideaId,
        int evaluationId)
    {
        var userId =
            CurrentUserId();

        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId,
                userId,
                createEvaluationForSupervisor: false);

        if (context == null)
        {
            return;
        }

        await Groups.RemoveFromGroupAsync(
            Context.ConnectionId,
            ProjectGroupName(
                context.Project.Id),
            Context.ConnectionAborted);
    }

    public async Task SendFeedbackMessage(
        int evaluationId,
        int ideaId,
        string messageText,
        int? replyToMessageId = null)
    {
        var userId =
            CurrentUserId();

        if (ideaId <= 0)
        {
            throw new HubException(
                "Choose a valid project idea.");
        }

        var cleanMessageText =
            messageText?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(
                cleanMessageText))
        {
            throw new HubException(
                "Write a message before sending.");
        }

        if (cleanMessageText.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Message cannot exceed "
                + $"{MaximumMessageLength} characters.");
        }

        /*
         * Chat messages belong to an existing evaluation.
         * Starting a discussion must never create a hidden
         * evaluation round.
         */
        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId,
                userId,
                createEvaluationForSupervisor: false);

        if (context?.Evaluation == null)
        {
            throw new HubException(
                "The project evaluation is not "
                + "available yet.");
        }

        var evaluation =
            context.Evaluation;

        FeedbackMessage? replyMessage =
            null;

        if (replyToMessageId.HasValue &&
            replyToMessageId.Value > 0)
        {
            var projectEvaluationIds =
                await db.SupervisorEvaluations
                    .AsNoTracking()
                    .Where(item =>
                        item.ProjectId ==
                            context.Project.Id &&
                        item.IdeaId ==
                            evaluation.IdeaId &&
                        item.SupervisorId ==
                            evaluation.SupervisorId)
                    .Select(item =>
                        item.Id)
                    .ToListAsync(
                        Context.ConnectionAborted);

            replyMessage =
                await db.FeedbackMessages
                    .AsNoTracking()
                    .FirstOrDefaultAsync(
                        message =>
                            message.Id ==
                                replyToMessageId.Value &&
                            projectEvaluationIds.Contains(
                                message.EvaluationId) &&
                            message.DeletedAt == null,
                        Context.ConnectionAborted);

            if (replyMessage == null)
            {
                throw new HubException(
                    "The message being replied to "
                    + "is unavailable in this project "
                    + "discussion.");
            }
        }

        var user =
            await db.Users
                .AsNoTracking()
                .Where(item =>
                    item.Id == userId)
                .Select(item => new
                {
                    item.FullName,
                    item.Role
                })
                .FirstOrDefaultAsync(
                    Context.ConnectionAborted);

        if (user == null)
        {
            throw new HubException(
                "The current user could not be found.");
        }

        var now =
            DateTime.UtcNow;

        var message =
            new FeedbackMessage
            {
                EvaluationId =
                    evaluation.Id,

                SenderUserId =
                    userId,

                MessageText =
                    cleanMessageText,

                ReplyToMessageId =
                    replyMessage?.Id,

                CreatedAt =
                    now
            };

        db.FeedbackMessages.Add(
            message);

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        await Groups.AddToGroupAsync(
            Context.ConnectionId,
            ProjectGroupName(
                context.Project.Id),
            Context.ConnectionAborted);

        var payload =
            new
            {
                id =
                    message.Id,

                projectId =
                    context.Project.Id,

                evaluationId =
                    evaluation.Id,

                ideaId =
                    evaluation.IdeaId,

                senderUserId =
                    message.SenderUserId,

                senderName =
                    SafeName(
                        user.FullName),

                senderRole =
                    NormalizeRole(
                        user.Role),

                messageText =
                    message.MessageText,

                createdAt =
                    message.CreatedAt,

                seenAt =
                    message.SeenAt,

                editedAt =
                    message.EditedAt,

                deletedAt =
                    message.DeletedAt,

                replyToMessageId =
                    message.ReplyToMessageId,

                replyPreview =
                    replyMessage == null
                        ? null
                        : TrimPreview(
                            replyMessage.MessageText)
            };

        await SendToProjectParticipantsAsync(
            context.Project,
            "ReceiveFeedbackMessage",
            payload);
    }

    public async Task EditFeedbackMessage(
        int messageId,
        string newText)
    {
        var userId =
            CurrentUserId();

        if (messageId <= 0)
        {
            throw new HubException(
                "Choose a valid message.");
        }

        var cleanText =
            newText?.Trim() ?? "";

        if (string.IsNullOrWhiteSpace(
                cleanText))
        {
            throw new HubException(
                "Edited message cannot be empty.");
        }

        if (cleanText.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Message cannot exceed "
                + $"{MaximumMessageLength} characters.");
        }

        var message =
            await db.FeedbackMessages
                .Include(item =>
                    item.Evaluation)
                .ThenInclude(evaluation =>
                    evaluation!.Project)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == messageId,
                    Context.ConnectionAborted);

        if (message?.Evaluation?.Project == null)
        {
            throw new HubException(
                "The message could not be found.");
        }

        if (message.DeletedAt.HasValue)
        {
            throw new HubException(
                "A deleted message cannot be edited.");
        }

        if (message.SenderUserId !=
            userId)
        {
            throw new HubException(
                "You can edit only your own messages.");
        }

        if (!await CanAccessEvaluationAsync(
                message.Evaluation,
                userId))
        {
            throw new HubException(
                "You no longer have access to this "
                + "project feedback.");
        }

        if (string.Equals(
                message.MessageText,
                cleanText,
                StringComparison.Ordinal))
        {
            return;
        }

        message.MessageText =
            cleanText;

        message.EditedAt =
            DateTime.UtcNow;

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        var payload =
            new
            {
                id =
                    message.Id,

                projectId =
                    message.Evaluation.Project.Id,

                evaluationId =
                    message.EvaluationId,

                ideaId =
                    message.Evaluation.IdeaId,

                messageText =
                    message.MessageText,

                editedAt =
                    message.EditedAt
            };

        await SendToProjectParticipantsAsync(
            message.Evaluation.Project,
            "FeedbackMessageEdited",
            payload);
    }

    public async Task DeleteFeedbackMessage(
        int messageId)
    {
        var userId =
            CurrentUserId();

        if (messageId <= 0)
        {
            throw new HubException(
                "Choose a valid message.");
        }

        var message =
            await db.FeedbackMessages
                .Include(item =>
                    item.Evaluation)
                .ThenInclude(evaluation =>
                    evaluation!.Project)
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == messageId,
                    Context.ConnectionAborted);

        if (message?.Evaluation?.Project == null)
        {
            throw new HubException(
                "The message could not be found.");
        }

        if (message.DeletedAt.HasValue)
        {
            return;
        }

        if (message.SenderUserId !=
            userId)
        {
            throw new HubException(
                "You can delete only your own messages.");
        }

        if (!await CanAccessEvaluationAsync(
                message.Evaluation,
                userId))
        {
            throw new HubException(
                "You no longer have access to this "
                + "project feedback.");
        }

        message.MessageText =
            "";

        message.DeletedAt =
            DateTime.UtcNow;

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        var payload =
            new
            {
                id =
                    message.Id,

                projectId =
                    message.Evaluation.Project.Id,

                evaluationId =
                    message.EvaluationId,

                ideaId =
                    message.Evaluation.IdeaId,

                deletedAt =
                    message.DeletedAt
            };

        await SendToProjectParticipantsAsync(
            message.Evaluation.Project,
            "FeedbackMessageDeleted",
            payload);
    }

    public async Task TypingFeedbackRoom(
        int ideaId)
    {
        if (ideaId <= 0)
        {
            return;
        }

        var userId =
            CurrentUserId();

        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId: 0,
                userId,
                createEvaluationForSupervisor: false);

        if (context == null)
        {
            return;
        }

        var user =
            await db.Users
                .AsNoTracking()
                .Where(item =>
                    item.Id == userId)
                .Select(item => new
                {
                    item.FullName,
                    item.Role
                })
                .FirstOrDefaultAsync(
                    Context.ConnectionAborted);

        await Groups.AddToGroupAsync(
            Context.ConnectionId,
            ProjectGroupName(
                context.Project.Id),
            Context.ConnectionAborted);

        var payload =
            new
            {
                projectId =
                    context.Project.Id,

                ideaId,

                userId,

                name =
                    SafeName(
                        user?.FullName),

                role =
                    NormalizeRole(
                        user?.Role ??
                        RoleName())
            };

        await SendToProjectParticipantsAsync(
            context.Project,
            "FeedbackUserTyping",
            payload,
            excludedUserId:
                userId);
    }

    public async Task StopTypingFeedbackRoom(
        int ideaId)
    {
        if (ideaId <= 0)
        {
            return;
        }

        var userId =
            CurrentUserId();

        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId: 0,
                userId,
                createEvaluationForSupervisor: false);

        if (context == null)
        {
            return;
        }

        var payload =
            new
            {
                projectId =
                    context.Project.Id,

                ideaId,

                userId
            };

        await SendToProjectParticipantsAsync(
            context.Project,
            "FeedbackUserStoppedTyping",
            payload,
            excludedUserId:
                userId);
    }

    public async Task MarkFeedbackSeenByIdea(
        int ideaId,
        int evaluationId)
    {
        if (ideaId <= 0 ||
            evaluationId <= 0)
        {
            return;
        }

        var userId =
            CurrentUserId();

        var context =
            await ResolveFeedbackContextAsync(
                ideaId,
                evaluationId,
                userId,
                createEvaluationForSupervisor: false);

        if (context?.Evaluation == null)
        {
            return;
        }

        if (!context.Project.SupervisorId.HasValue)
        {
            return;
        }

        await MarkSeenInternalAsync(
            context.Project.Id,
            ideaId,
            context.Project.SupervisorId.Value,
            userId);
    }

    private async Task<FeedbackContext?>
        ResolveFeedbackContextAsync(
            int ideaId,
            int evaluationId,
            int userId,
            bool createEvaluationForSupervisor)
    {
        if (ideaId <= 0)
        {
            return null;
        }

        /*
         * Prefer the explicit evaluation because it
         * gives us the exact project scope.
         */
        if (evaluationId > 0)
        {
            var existingEvaluation =
                await db.SupervisorEvaluations
                    .Include(evaluation =>
                        evaluation.Idea)
                    .Include(evaluation =>
                        evaluation.Project)
                    .FirstOrDefaultAsync(
                        evaluation =>
                            evaluation.Id ==
                                evaluationId &&
                            evaluation.IdeaId ==
                                ideaId,
                        Context.ConnectionAborted);

            if (existingEvaluation?.Project != null &&
                await CanAccessEvaluationAsync(
                    existingEvaluation,
                    userId))
            {
                return new FeedbackContext(
                    existingEvaluation.Project,
                    existingEvaluation);
            }
        }

        /*
         * Find the project where this idea is the
         * current official selected idea.
         */
        var project =
            await ResolveAccessibleProjectForIdeaAsync(
                ideaId,
                userId);

        if (project == null)
        {
            return null;
        }

        var evaluation =
            await db.SupervisorEvaluations
                .Include(item =>
                    item.Idea)
                .Where(item =>
                    item.ProjectId ==
                        project.Id &&
                    item.IdeaId ==
                        ideaId &&
                    item.SupervisorId ==
                        project.SupervisorId)
                .OrderByDescending(item =>
                    item.UpdatedAt)
                .ThenByDescending(item =>
                    item.Id)
                .FirstOrDefaultAsync(
                    Context.ConnectionAborted);

        if (evaluation != null)
        {
            if (evaluation.SupervisorId !=
                project.SupervisorId)
            {
                return null;
            }

            return new FeedbackContext(
                project,
                evaluation);
        }

        /*
         * No evaluation exists yet. The supervisor must
         * save an evaluation from the Feedback Workspace
         * before the project discussion can begin.
         */
        return new FeedbackContext(
            project,
            null);
    }

    private async Task<Project?>
        ResolveAccessibleProjectForIdeaAsync(
            int ideaId,
            int userId)
    {
        var role =
            RoleName();

        IQueryable<Project> query =
            db.Projects
                .Where(project =>
                    project.ProjectIdeaId ==
                        ideaId &&
                    project.SupervisorId.HasValue &&
                    project.SupervisorAssignmentStatus ==
                        "active");

        if (role.Equals(
                "supervisor",
                StringComparison.OrdinalIgnoreCase))
        {
            query =
                query.Where(project =>
                    project.SupervisorId ==
                        userId);
        }
        else if (role.Equals(
                     "student",
                     StringComparison.OrdinalIgnoreCase))
        {
            query =
                query.Where(project =>
                    project.StudentId ==
                        userId ||
                    project.Members.Any(
                        member =>
                            member.UserId ==
                                userId &&
                            member.Status ==
                                "active"));
        }
        else
        {
            return null;
        }

        return await query
            .OrderByDescending(project =>
                project.UpdatedAt)
            .FirstOrDefaultAsync(
                Context.ConnectionAborted);
    }

    private async Task<bool>
        CanAccessEvaluationAsync(
            SupervisorEvaluation evaluation,
            int userId)
    {
        if (!evaluation.ProjectId.HasValue)
        {
            return false;
        }

        var project =
            evaluation.Project;

        if (project == null)
        {
            project =
                await db.Projects
                    .AsNoTracking()
                    .FirstOrDefaultAsync(
                        item =>
                            item.Id ==
                                evaluation.ProjectId.Value,
                        Context.ConnectionAborted);
        }

        if (project == null)
        {
            return false;
        }

        /*
         * The evaluation must belong to the project's
         * current official idea and official supervisor.
         */
        if (project.ProjectIdeaId !=
                evaluation.IdeaId ||
            project.SupervisorId !=
                evaluation.SupervisorId ||
            !project.SupervisorId.HasValue ||
            !string.Equals(
                project.SupervisorAssignmentStatus,
                "active",
                StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        var role =
            RoleName();

        if (role.Equals(
                "supervisor",
                StringComparison.OrdinalIgnoreCase))
        {
            return project.SupervisorId ==
                   userId;
        }

        if (role.Equals(
                "student",
                StringComparison.OrdinalIgnoreCase))
        {
            if (project.StudentId ==
                userId)
            {
                return true;
            }

            return await db.ProjectMembers
                .AsNoTracking()
                .AnyAsync(
                    member =>
                        member.ProjectId ==
                            project.Id &&
                        member.UserId ==
                            userId &&
                        member.Status ==
                            "active",
                    Context.ConnectionAborted);
        }

        return false;
    }

    private async Task MarkSeenInternalAsync(
        int projectId,
        int ideaId,
        int supervisorId,
        int userId)
    {
        var evaluationIds =
            await db.SupervisorEvaluations
                .AsNoTracking()
                .Where(evaluation =>
                    evaluation.ProjectId ==
                        projectId &&
                    evaluation.IdeaId ==
                        ideaId &&
                    evaluation.SupervisorId ==
                        supervisorId)
                .Select(evaluation =>
                    evaluation.Id)
                .ToListAsync(
                    Context.ConnectionAborted);

        if (evaluationIds.Count == 0)
        {
            return;
        }

        var now =
            DateTime.UtcNow;

        var messages =
            await db.FeedbackMessages
                .Where(message =>
                    evaluationIds.Contains(
                        message.EvaluationId) &&
                    message.SenderUserId !=
                        userId &&
                    message.SeenAt == null &&
                    message.DeletedAt == null)
                .ToListAsync(
                    Context.ConnectionAborted);

        if (messages.Count == 0)
        {
            return;
        }

        foreach (var message in messages)
        {
            message.SeenAt =
                now;
        }

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        var project =
            await db.Projects
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == projectId,
                    Context.ConnectionAborted);

        if (project == null)
        {
            return;
        }

        await SendToProjectParticipantsAsync(
            project,
            "FeedbackMessagesSeen",
            new
            {
                projectId,
                ideaId,
                seenByUserId =
                    userId,
                seenAt =
                    now
            });
    }

    private async Task SendToProjectParticipantsAsync(
        Project project,
        string methodName,
        object payload,
        int? excludedUserId = null)
    {
        var participantIds =
            await LoadProjectParticipantIdsAsync(
                project);

        if (excludedUserId.HasValue)
        {
            participantIds.Remove(
                excludedUserId.Value);
        }

        if (participantIds.Count == 0)
        {
            return;
        }

        await Clients
            .Users(
                participantIds.Select(
                    participantId =>
                        participantId.ToString()))
            .SendAsync(
                methodName,
                payload,
                Context.ConnectionAborted);
    }

    private async Task<HashSet<int>>
        LoadProjectParticipantIdsAsync(
            Project project)
    {
        var participantIdList =
    await db.ProjectMembers
        .AsNoTracking()
        .Where(member =>
            member.ProjectId ==
                project.Id &&
            member.Status ==
                "active")
        .Select(member =>
            member.UserId)
        .Distinct()
        .ToListAsync(
            Context.ConnectionAborted);

        var participantIds =
            participantIdList.ToHashSet();

        /*
         * Preserve compatibility with projects where
         * the owner is stored in Project.StudentId but
         * an older database does not yet contain the
         * matching ProjectMember row.
         */
        participantIds.Add(
            project.StudentId);

        if (project.SupervisorId.HasValue &&
            string.Equals(
                project.SupervisorAssignmentStatus,
                "active",
                StringComparison.OrdinalIgnoreCase))
        {
            participantIds.Add(
                project.SupervisorId.Value);
        }

        return participantIds;
    }

    private int CurrentUserId()
    {
        var value =
            Context.User?
                .FindFirst(
                    ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                value,
                out var userId))
        {
            return userId;
        }

        throw new HubException(
            "User is not authenticated.");
    }

    private string RoleName()
    {
        return Context.User?
            .FindFirst(
                ClaimTypes.Role)
            ?.Value ?? "";
    }

    private static string ProjectGroupName(
        int projectId)
    {
        return $"feedback-project-{projectId}";
    }

    private static string SafeName(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "User"
                : value.Trim();
    }

    private static string NormalizeRole(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "student"
                : value.Trim()
                    .ToLowerInvariant();
    }

    private static string TrimPreview(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(
                value))
        {
            return "";
        }

        var clean =
            value.Trim();

        return clean.Length <= 80
            ? clean
            : clean[..80] + "...";
    }

    private sealed record FeedbackContext(
        Project Project,
        SupervisorEvaluation? Evaluation);
}