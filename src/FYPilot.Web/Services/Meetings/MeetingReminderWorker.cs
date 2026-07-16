using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.Meetings;

public class MeetingReminderWorker(
    IServiceScopeFactory scopeFactory,
    ILogger<MeetingReminderWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await SendUpcomingMeetingRemindersAsync(stoppingToken);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Meeting reminder worker failed.");
            }

            await Task.Delay(TimeSpan.FromMinutes(15), stoppingToken);
        }
    }

    private async Task SendUpcomingMeetingRemindersAsync(CancellationToken stoppingToken)
    {
        using var scope = scopeFactory.CreateScope();

        var db = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var notificationService = scope.ServiceProvider.GetRequiredService<INotificationService>();

        var now = DateTime.UtcNow;
        var reminderWindowEnd = now.AddHours(12);

        var meetings = await db.Meetings
            .Include(m => m.Student)
            .Include(m => m.Supervisor)
            .Where(m =>
                m.Status == "scheduled" &&
                m.StudentId != null &&
                m.ScheduledAt > now &&
                m.ScheduledAt <= reminderWindowEnd &&
                m.Reminder12HoursSentAt == null)
            .ToListAsync(stoppingToken);

        foreach (var meeting in meetings)
        {
            if (meeting.Student == null || meeting.Supervisor == null)
            {
                continue;
            }

            var studentName = meeting.Student.FullName;
            var supervisorName = meeting.Supervisor.FullName;
            var meetingTime = meeting.ScheduledAt.ToLocalTime();

            var title = "Meeting Reminder";
            var message =
                $"Reminder: you have a meeting with {supervisorName} on {meetingTime:MMM d, yyyy} at {meetingTime:HH:mm}.";

            var meetingLink = !string.IsNullOrWhiteSpace(meeting.GoogleMeetLink)
     ? meeting.GoogleMeetLink
     : meeting.GoogleCalendarEventLink ?? "";

            var emailBody = BuildReminderEmail(
                studentName,
                supervisorName,
                meeting.Title,
                meetingTime,
                meeting.MeetingMode,
                meetingLink,
                meeting.Agenda);

            await notificationService.NotifyUserAsync(
     meeting.StudentId.Value,
     title,
     message,
     "meeting_reminder",
     "/Student/Dashboard",
     sendEmail: true,
     emailSubject: "Reminder: You have an upcoming FYP meeting",
     emailHtmlBody: emailBody);

            meeting.Reminder12HoursSentAt = DateTime.UtcNow;
            meeting.UpdatedAt = DateTime.UtcNow;
        }

        await db.SaveChangesAsync(stoppingToken);
    }

    private static string BuildReminderEmail(
        string studentName,
        string supervisorName,
        string meetingTitle,
        DateTime meetingTime,
        string meetingMode,
        string locationOrLink,
        string agenda)
    {
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        supervisorName = System.Net.WebUtility.HtmlEncode(supervisorName);
        meetingTitle = System.Net.WebUtility.HtmlEncode(meetingTitle);
        meetingMode = System.Net.WebUtility.HtmlEncode(meetingMode);
        locationOrLink = System.Net.WebUtility.HtmlEncode(locationOrLink);
        agenda = System.Net.WebUtility.HtmlEncode(agenda);

        var joinHtml = string.IsNullOrWhiteSpace(locationOrLink)
            ? ""
            : $"""
              <p style="margin-top:18px;">
                  <a href="{locationOrLink}"
                     style="display:inline-block;background:#28385E;color:white;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:700;">
                      Open Meeting
                  </a>
              </p>
              """;

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:640px;margin:auto;background:white;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
                <h2 style="color:#28385E;margin-top:0;">Meeting Reminder</h2>

                <p style="color:#475569;line-height:1.7;">
                    Hello {studentName},
                </p>

                <p style="color:#475569;line-height:1.7;">
                    This is a reminder that you have an upcoming FYP meeting with
                    <strong>{supervisorName}</strong>.
                </p>

                <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:14px;margin-top:14px;">
                    <p style="margin:0 0 8px;color:#28385E;"><strong>{meetingTitle}</strong></p>
                    <p style="margin:0;color:#475569;">Date: {meetingTime:MMM d, yyyy}</p>
                    <p style="margin:0;color:#475569;">Time: {meetingTime:HH:mm}</p>
                    <p style="margin:0;color:#475569;">Mode: {meetingMode}</p>
                    <p style="margin:0;color:#475569;">Agenda: {agenda}</p>
                </div>

                {joinHtml}

                <p style="color:#94A3B8;font-size:12px;margin-top:24px;">
                    FYPilot automated notification
                </p>
            </div>
        </div>
        """;
    }
}