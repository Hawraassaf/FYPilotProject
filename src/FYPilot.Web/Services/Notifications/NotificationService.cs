using System.Net;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Hubs;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.Notifications;

public class NotificationService(
    ApplicationDbContext db,
    IHubContext<NotificationHub> hubContext,
    FYPilot.Infrastructure.Services.IEmailSender emailSender,
    ILogger<NotificationService> logger)
    : INotificationService
{
    public async Task NotifyUserAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url = "",
        bool sendEmail = false,
        string? emailSubject = null,
        string? emailHtmlBody = null,
        int? projectId = null,
        int? actorUserId = null,
        CancellationToken cancellationToken = default)
    {
        if (recipientUserId <= 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(recipientUserId));
        }

        if (actorUserId.HasValue &&
            actorUserId.Value == recipientUserId)
        {
            return;
        }

        var recipient = await db.Users
            .AsNoTracking()
            .Where(user => user.Id == recipientUserId)
            .Select(user => new
            {
                user.Id,
                user.Email
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (recipient == null)
        {
            logger.LogWarning(
                "Notification recipient {UserId} no longer exists.",
                recipientUserId);

            return;
        }

        var cleanTitle = CleanRequired(title, 200, "Notification");
        var cleanMessage = CleanRequired(message, 1200, "You have a new update.");
        var cleanType = CleanRequired(type, 80, "general")
            .ToLowerInvariant();
        var cleanUrl = CleanOptional(url, 500);

        var notification = new Notification
        {
            RecipientUserId = recipientUserId,
            ProjectId = projectId,
            ActorUserId = actorUserId,
            Title = cleanTitle,
            Message = cleanMessage,
            Type = cleanType,
            Url = cleanUrl,
            IsRead = false,
            CreatedAt = DateTime.UtcNow
        };

        db.Notifications.Add(notification);
        await db.SaveChangesAsync(cancellationToken);

        try
        {
            await hubContext.Clients
                .Group($"user-{recipientUserId}")
                .SendAsync(
                    "ReceiveNotification",
                    new
                    {
                        id = notification.Id,
                        projectId = notification.ProjectId,
                        actorUserId = notification.ActorUserId,
                        title = notification.Title,
                        message = notification.Message,
                        type = notification.Type,
                        url = notification.Url,
                        createdAt = notification.CreatedAt
                    },
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Notification {NotificationId} was saved, but its live SignalR push failed.",
                notification.Id);
        }

        if (!sendEmail ||
            string.IsNullOrWhiteSpace(recipient.Email))
        {
            return;
        }

        try
        {
            var subject = string.IsNullOrWhiteSpace(emailSubject)
                ? notification.Title
                : emailSubject.Trim();

            var body = string.IsNullOrWhiteSpace(emailHtmlBody)
                ? BuildDefaultEmail(
                    notification.Title,
                    notification.Message,
                    notification.Url)
                : emailHtmlBody;

            await emailSender.SendAsync(
                recipient.Email,
                subject,
                body);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Failed to send notification email to user {UserId}.",
                recipientUserId);
        }
    }

    private static string CleanRequired(
        string? value,
        int maximumLength,
        string fallback)
    {
        var clean = string.IsNullOrWhiteSpace(value)
            ? fallback
            : value.Trim();

        return clean.Length <= maximumLength
            ? clean
            : clean[..maximumLength];
    }

    private static string CleanOptional(
        string? value,
        int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return "";
        }

        var clean = value.Trim();

        return clean.Length <= maximumLength
            ? clean
            : clean[..maximumLength];
    }

    private static string BuildDefaultEmail(
        string title,
        string message,
        string url)
    {
        var safeTitle = WebUtility.HtmlEncode(title);
        var safeMessage = WebUtility.HtmlEncode(message);
        var safeUrl = WebUtility.HtmlEncode(url);

        var button = string.IsNullOrWhiteSpace(url)
            ? ""
            : $"""
               <p style="margin-top:22px;">
                   <a href="{safeUrl}"
                      style="display:inline-block;background:#28385E;color:white;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:700;">
                       Open FYPilot
                   </a>
               </p>
               """;

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:620px;margin:auto;background:white;border-radius:18px;padding:26px;border:1px solid #E8ECF2;">
                <h2 style="color:#28385E;margin-top:0;">{safeTitle}</h2>
                <p style="color:#475569;line-height:1.7;font-size:15px;">{safeMessage}</p>
                {button}
                <p style="color:#94A3B8;font-size:12px;margin-top:26px;">
                    This is an automated message from FYPilot.
                </p>
            </div>
        </div>
        """;
    }
}
