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
    ILogger<NotificationService> logger) : INotificationService
{
    public async Task NotifyUserAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url = "",
        bool sendEmail = false,
        string? emailSubject = null,
        string? emailHtmlBody = null)
    {
        var notification = new Notification
        {
            RecipientUserId = recipientUserId,
            Title = title,
            Message = message,
            Type = type,
            Url = url,
            IsRead = false,
            CreatedAt = DateTime.UtcNow
        };

        db.Notifications.Add(notification);
        await db.SaveChangesAsync();

        await hubContext.Clients
            .Group($"user-{recipientUserId}")
            .SendAsync("ReceiveNotification", new
            {
                id = notification.Id,
                title = notification.Title,
                message = notification.Message,
                type = notification.Type,
                url = notification.Url,
                createdAt = notification.CreatedAt
            });

        if (!sendEmail)
        {
            return;
        }

        var user = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == recipientUserId);

        if (user == null || string.IsNullOrWhiteSpace(user.Email))
        {
            return;
        }

        try
        {
            var subject = string.IsNullOrWhiteSpace(emailSubject)
                ? title
                : emailSubject;

            var body = string.IsNullOrWhiteSpace(emailHtmlBody)
                ? BuildDefaultEmail(title, message, url)
                : emailHtmlBody;

            await emailSender.SendAsync(user.Email, subject, body);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to send notification email to user {UserId}", recipientUserId);
        }
    }

    private static string BuildDefaultEmail(string title, string message, string url)
    {
        var safeTitle = System.Net.WebUtility.HtmlEncode(title);
        var safeMessage = System.Net.WebUtility.HtmlEncode(message);
        var safeUrl = System.Net.WebUtility.HtmlEncode(url);

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