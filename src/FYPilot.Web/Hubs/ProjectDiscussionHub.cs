using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Hubs;

[Authorize(Roles = "student")]
public class ProjectDiscussionHub(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    INotificationService notificationService,
    ILogger<ProjectDiscussionHub> logger)
    : Hub
{
    private const int MaximumMessageLength = 1000;

    public async Task JoinProject(
        int projectId)
    {
        await EnsureProjectAccessAsync(
            projectId,
            "You do not have access to this project.");

        await Groups.AddToGroupAsync(
            Context.ConnectionId,
            GroupName(projectId),
            Context.ConnectionAborted);
    }

    public async Task LeaveProject(
        int projectId)
    {
        if (projectId <= 0)
        {
            return;
        }

        await Groups.RemoveFromGroupAsync(
            Context.ConnectionId,
            GroupName(projectId),
            Context.ConnectionAborted);
    }

    public async Task SendMessage(
        int projectId,
        string? content,
        int? replyToMessageId = null)
    {
        var (userId, _) =
            await EnsureProjectWriteAccessAsync(
                projectId,
                "You no longer have access to this project.");

        var cleanContent =
            content?.Trim() ?? string.Empty;

        if (string.IsNullOrWhiteSpace(
                cleanContent))
        {
            throw new HubException(
                "Write a message before sending.");
        }

        if (cleanContent.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Messages cannot exceed "
                + $"{MaximumMessageLength} characters.");
        }

        ProjectDiscussionMessage? replyMessage =
            null;

        if (replyToMessageId.HasValue)
        {
            if (replyToMessageId.Value <= 0)
            {
                throw new HubException(
                    "Choose a valid message to reply to.");
            }

            replyMessage =
                await db.ProjectDiscussionMessages
                    .AsNoTracking()
                    .Include(message =>
                        message.User)
                    .FirstOrDefaultAsync(
                        message =>
                            message.Id ==
                                replyToMessageId.Value &&
                            message.ProjectId ==
                                projectId,
                        Context.ConnectionAborted);

            if (replyMessage == null)
            {
                throw new HubException(
                    "The message being replied to "
                    + "could not be found.");
            }

            if (replyMessage.IsDeleted)
            {
                throw new HubException(
                    "You cannot reply to a deleted message.");
            }
        }

        var user = await db.Users
            .AsNoTracking()
            .Where(item =>
                item.Id == userId)
            .Select(item => new
            {
                item.FullName
            })
            .FirstOrDefaultAsync(
                Context.ConnectionAborted);

        if (user == null)
        {
            throw new HubException(
                "The student account could not be found.");
        }

        var now =
            DateTime.UtcNow;

        var message =
            new ProjectDiscussionMessage
            {
                ProjectId =
                    projectId,

                UserId =
                    userId,

                Content =
                    cleanContent,

                ReplyToMessageId =
                    replyToMessageId,

                IsEdited =
                    false,

                IsDeleted =
                    false,

                CreatedAtUtc =
                    now
            };

        db.ProjectDiscussionMessages.Add(
            message);

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        await Groups.AddToGroupAsync(
            Context.ConnectionId,
            GroupName(projectId),
            Context.ConnectionAborted);

        var payload = new
        {
            id =
                message.Id,

            projectId,

            userId,

            fullName =
                SafeName(user.FullName),

            initials =
                Initials(user.FullName),

            content =
                message.Content,

            createdAtUtc =
                message.CreatedAtUtc,

            createdAtDisplay =
                message.CreatedAtUtc
                    .ToLocalTime()
                    .ToString(
                        "MMM d, yyyy h:mm tt"),

            isEdited =
                false,

            isDeleted =
                false,

            replyTo = replyMessage == null
                ? null
                : new
                {
                    id =
                        replyMessage.Id,

                    fullName =
                        SafeName(
                            replyMessage.User?.FullName),

                    content =
                        ShortText(
                            replyMessage.Content,
                            120)
                },

            attachments =
                Array.Empty<object>()
        };

        await SendToActiveProjectMembersAsync(
            projectId,
            "MessageCreated",
            payload);

        await NotifyOtherProjectMembersAsync(
            projectId,
            userId,
            SafeName(user.FullName));
    }

    public async Task EditMessage(
        int projectId,
        int messageId,
        string? content)
    {
        var (userId, _) =
            await EnsureProjectWriteAccessAsync(
                projectId,
                "You no longer have access to this project.");

        if (messageId <= 0)
        {
            throw new HubException(
                "Choose a valid message.");
        }

        var cleanContent =
            content?.Trim() ?? string.Empty;

        if (string.IsNullOrWhiteSpace(
                cleanContent))
        {
            throw new HubException(
                "The edited message cannot be empty.");
        }

        if (cleanContent.Length >
            MaximumMessageLength)
        {
            throw new HubException(
                $"Messages cannot exceed "
                + $"{MaximumMessageLength} characters.");
        }

        var message =
            await db.ProjectDiscussionMessages
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == messageId &&
                        item.ProjectId == projectId,
                    Context.ConnectionAborted);

        if (message == null)
        {
            throw new HubException(
                "The message could not be found.");
        }

        if (message.IsDeleted)
        {
            throw new HubException(
                "A deleted message cannot be edited.");
        }

        if (message.UserId != userId)
        {
            throw new HubException(
                "You can edit only your own messages.");
        }

        if (string.Equals(
                message.Content,
                cleanContent,
                StringComparison.Ordinal))
        {
            return;
        }

        var now =
            DateTime.UtcNow;

        message.Content =
            cleanContent;

        message.IsEdited =
            true;

        message.EditedAtUtc =
            now;

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        var payload = new
        {
            id =
                message.Id,

            projectId,

            content =
                message.Content,

            isEdited =
                true,

            editedAtUtc =
                now,

            editedAtDisplay =
                now.ToLocalTime()
                    .ToString(
                        "MMM d, yyyy h:mm tt")
        };

        await SendToActiveProjectMembersAsync(
            projectId,
            "MessageUpdated",
            payload);
    }

    public async Task DeleteMessage(
        int projectId,
        int messageId)
    {
        var (userId, access) =
            await EnsureProjectWriteAccessAsync(
                projectId,
                "You no longer have access to this project.");

        if (messageId <= 0)
        {
            throw new HubException(
                "Choose a valid message.");
        }

        var message =
            await db.ProjectDiscussionMessages
                .FirstOrDefaultAsync(
                    item =>
                        item.Id == messageId &&
                        item.ProjectId == projectId,
                    Context.ConnectionAborted);

        if (message == null)
        {
            throw new HubException(
                "The message could not be found.");
        }

        if (message.IsDeleted)
        {
            return;
        }

        var mayDelete =
            message.UserId == userId ||
            access.IsOwner;

        if (!mayDelete)
        {
            throw new HubException(
                "You cannot delete this message.");
        }

        var now =
            DateTime.UtcNow;

        message.Content =
            string.Empty;

        message.IsDeleted =
            true;

        message.DeletedAtUtc =
            now;

        await db.SaveChangesAsync(
            Context.ConnectionAborted);

        var payload = new
        {
            id =
                message.Id,

            projectId,

            deletedAtUtc =
                now,

            deletedAtDisplay =
                now.ToLocalTime()
                    .ToString(
                        "MMM d, yyyy h:mm tt")
        };

        await SendToActiveProjectMembersAsync(
            projectId,
            "MessageDeleted",
            payload);
    }

    public async Task Typing(
        int projectId)
    {
        var (userId, _) =
            await EnsureProjectWriteAccessAsync(
                projectId,
                "You no longer have access to this project.");

        var fullName =
            Context.User?
                .FindFirst(
                    ClaimTypes.Name)
                ?.Value;

        await SendToActiveProjectMembersAsync(
            projectId,
            "UserTyping",
            new
            {
                projectId,
                userId,
                fullName =
                    SafeName(fullName)
            });
    }

    public async Task StopTyping(
        int projectId)
    {
        var (userId, _) =
            await EnsureProjectWriteAccessAsync(
                projectId,
                "You no longer have access to this project.");

        await SendToActiveProjectMembersAsync(
            projectId,
            "UserStoppedTyping",
            new
            {
                projectId,
                userId
            });
    }

    public override async Task OnDisconnectedAsync(
        Exception? exception)
    {
        if (exception != null)
        {
            logger.LogDebug(
                exception,
                "Project discussion SignalR connection "
                + "{ConnectionId} was disconnected.",
                Context.ConnectionId);
        }

        await base.OnDisconnectedAsync(
            exception);
    }

    private async Task<(int UserId, ProjectAccessResult Access)>
        EnsureProjectAccessAsync(
            int projectId,
            string accessErrorMessage)
    {
        if (projectId <= 0)
        {
            throw new HubException(
                "Choose a valid project.");
        }

        var userId =
            CurrentUserId();

        if (userId == null)
        {
            throw new HubException(
                "Authentication is required.");
        }

        var access =
            await projectAccessService.GetAccessAsync(
                projectId,
                userId.Value,
                "student",
                Context.ConnectionAborted);

        if (access?.CanView != true)
        {
            throw new HubException(
                accessErrorMessage);
        }

        return (
            userId.Value,
            access);
    }

    private async Task<(int UserId, ProjectAccessResult Access)>
        EnsureProjectWriteAccessAsync(
            int projectId,
            string accessErrorMessage)
    {
        var result =
            await EnsureProjectAccessAsync(
                projectId,
                accessErrorMessage);

        if (!result.Access.CanEdit)
        {
            throw new HubException(
                "Restore this project before making changes.");
        }

        return result;
    }

    private async Task NotifyOtherProjectMembersAsync(
        int projectId,
        int senderUserId,
        string senderName)
    {
        var project =
            await db.Projects
                .AsNoTracking()
                .Where(item =>
                    item.Id == projectId)
                .Select(item => new
                {
                    item.StudentId,
                    item.Title
                })
                .FirstOrDefaultAsync(
                    Context.ConnectionAborted);

        if (project == null)
        {
            return;
        }

        var recipientList =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId == projectId &&
                    member.Status == "active")
                .Select(member =>
                    member.UserId)
                .Distinct()
                .ToListAsync(
                    Context.ConnectionAborted);

        var recipientIds =
            recipientList.ToHashSet();

        recipientIds.Add(
            project.StudentId);

        recipientIds.Remove(
            senderUserId);

        if (recipientIds.Count == 0)
        {
            return;
        }

        var projectTitle =
            string.IsNullOrWhiteSpace(
                project.Title)
                ? "Untitled Project"
                : project.Title.Trim();

        foreach (var recipientUserId
                 in recipientIds)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        recipientUserId,

                    title:
                        $"New message in \"{projectTitle}\"",

                    message:
                        $"{senderName} sent a message "
                        + "in the Project Workspace discussion.",

                    type:
                        "project_workspace_message",

                    url:
                        $"/Student/ProjectWorkspace"
                        + $"?projectId={projectId}",

                    sendEmail:
                        false,

                    projectId:
                        projectId,

                    actorUserId:
                        senderUserId,

                    cancellationToken:
                        Context.ConnectionAborted);
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Project workspace message was saved, "
                    + "but notification failed for user "
                    + "{RecipientUserId} in project "
                    + "{ProjectId}.",
                    recipientUserId,
                    projectId);
            }
        }
    }

    private async Task SendToActiveProjectMembersAsync(
        int projectId,
        string methodName,
        object payload)
    {
        /*
         * Broadcast to current active members rather
         * than trusting a stale SignalR group.
         *
         * A removed collaborator is therefore excluded
         * immediately from future project events.
         */
        var recipientUserIds =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId == projectId &&
                    member.Status == "active")
                .Select(member =>
                    member.UserId.ToString())
                .Distinct()
                .ToListAsync(
                    Context.ConnectionAborted);

        if (recipientUserIds.Count == 0)
        {
            return;
        }

        await Clients
            .Users(recipientUserIds)
            .SendAsync(
                methodName,
                payload,
                Context.ConnectionAborted);
    }

    private int? CurrentUserId()
    {
        var value = Context.User?
            .FindFirst(
                ClaimTypes.NameIdentifier)
            ?.Value;

        return int.TryParse(
            value,
            out var userId)
                ? userId
                : null;
    }

    private static string GroupName(
        int projectId)
    {
        return $"project-discussion-{projectId}";
    }

    private static string SafeName(
        string? value)
    {
        return string.IsNullOrWhiteSpace(
            value)
                ? "Student"
                : value.Trim();
    }

    private static string Initials(
        string? fullName)
    {
        if (string.IsNullOrWhiteSpace(
                fullName))
        {
            return "ST";
        }

        var parts = fullName
            .Split(
                ' ',
                StringSplitOptions
                    .RemoveEmptyEntries)
            .Take(2)
            .ToList();

        return parts.Count == 0
            ? "ST"
            : string.Join(
                    "",
                    parts.Select(
                        part => part[0]))
                .ToUpperInvariant();
    }

    private static string ShortText(
        string? content,
        int maximumLength)
    {
        var clean =
            content?.Trim() ?? string.Empty;

        if (clean.Length <= maximumLength)
        {
            return clean;
        }

        return clean[..maximumLength] + "…";
    }
}