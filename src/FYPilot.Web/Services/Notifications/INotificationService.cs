namespace FYPilot.Web.Services.Notifications;

public interface INotificationService
{
    Task NotifyUserAsync(
        int recipientUserId,
        string title,
        string message,
        string type,
        string url = "",
        bool sendEmail = false,
        string? emailSubject = null,
        string? emailHtmlBody = null);
}