using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.Notifications;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.Meetings;

public class MeetingReminderWorker(
    IServiceScopeFactory scopeFactory,
    ILogger<MeetingReminderWorker> logger)
    : BackgroundService
{
    private static readonly TimeSpan CheckInterval =
        TimeSpan.FromMinutes(15);

    private static readonly TimeSpan ReminderWindow =
        TimeSpan.FromHours(12);

    protected override async Task ExecuteAsync(
        CancellationToken stoppingToken)
    {
        /*
         * Run immediately when the application starts,
         * then repeat every 15 minutes.
         */
        await RunReminderCheckSafelyAsync(
            stoppingToken);

        using var timer =
            new PeriodicTimer(
                CheckInterval);

        try
        {
            while (await timer.WaitForNextTickAsync(
                       stoppingToken))
            {
                await RunReminderCheckSafelyAsync(
                    stoppingToken);
            }
        }
        catch (OperationCanceledException)
            when (stoppingToken.IsCancellationRequested)
        {
            /*
             * Normal application shutdown.
             */
        }
    }

    private async Task RunReminderCheckSafelyAsync(
        CancellationToken stoppingToken)
    {
        try
        {
            await SendUpcomingMeetingRemindersAsync(
                stoppingToken);
        }
        catch (OperationCanceledException)
            when (stoppingToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Meeting reminder worker failed.");
        }
    }

    private async Task SendUpcomingMeetingRemindersAsync(
        CancellationToken stoppingToken)
    {
        using var scope =
            scopeFactory.CreateScope();

        var db =
            scope.ServiceProvider
                .GetRequiredService<ApplicationDbContext>();

        var notificationService =
            scope.ServiceProvider
                .GetRequiredService<INotificationService>();

        var now =
            DateTime.UtcNow;

        var reminderWindowEnd =
            now.Add(
                ReminderWindow);

        /*
         * ProjectId is the source of truth.
         * StudentId is only a legacy owner reference and
         * must not limit reminders to one student.
         */
        var meetings =
            await db.Meetings
                .AsNoTracking()
                .Where(meeting =>
                    meeting.Status == "scheduled" &&
                    meeting.ProjectId.HasValue &&
                    meeting.Project != null &&
                    meeting.ScheduledAt > now &&
                    meeting.ScheduledAt <=
                        reminderWindowEnd &&
                    meeting.Reminder12HoursSentAt ==
                        null)
                .Select(meeting =>
                    new MeetingReminderCandidate(
                        meeting.Id,
                        meeting.ProjectId!.Value,
                        meeting.Project!.StudentId,
                        meeting.SupervisorId,
                        meeting.Project.Title,
                        meeting.Title,
                        meeting.ScheduledAt,
                        meeting.DurationMinutes,
                        meeting.MeetingMode,
                        meeting.GoogleMeetLink,
                        meeting.GoogleCalendarEventLink,
                        meeting.Agenda,
                        meeting.NotesToPrepare,
                        meeting.Supervisor != null
                            ? meeting.Supervisor.FullName
                            : "Supervisor"))
                .OrderBy(meeting =>
                    meeting.ScheduledAtUtc)
                .ToListAsync(
                    stoppingToken);

        foreach (var meeting in meetings)
        {
            stoppingToken.ThrowIfCancellationRequested();

            await SendReminderForMeetingAsync(
                db,
                notificationService,
                meeting,
                stoppingToken);
        }
    }

    private async Task SendReminderForMeetingAsync(
        ApplicationDbContext db,
        INotificationService notificationService,
        MeetingReminderCandidate meeting,
        CancellationToken stoppingToken)
    {
        var memberIds =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        meeting.ProjectId &&
                    member.Status == "active")
                .Select(member =>
                    member.UserId)
                .Distinct()
                .ToListAsync(
                    stoppingToken);

        /*
         * Preserve compatibility with older projects that
         * store the owner only in Project.StudentId.
         */
        if (!memberIds.Contains(
                meeting.OwnerUserId))
        {
            memberIds.Add(
                meeting.OwnerUserId);
        }

        var recipients =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    memberIds.Contains(
                        user.Id) &&
                    user.Role.ToLower() ==
                        "student")
                .Select(user =>
                    new ReminderRecipient(
                        user.Id,
                        user.FullName,
                        user.Email))
                .ToListAsync(
                    stoppingToken);

        if (recipients.Count == 0)
        {
            logger.LogWarning(
                "Meeting {MeetingId} for project "
                + "{ProjectId} has no active student "
                + "recipients for its reminder.",
                meeting.MeetingId,
                meeting.ProjectId);

            /*
             * Mark it processed so a malformed meeting
             * does not trigger the same warning every
             * 15 minutes.
             */
            await MarkReminderProcessedAsync(
                db,
                meeting.MeetingId,
                stoppingToken);

            return;
        }

        var projectTitle =
            SafeText(
                meeting.ProjectTitle,
                "Untitled Project");

        var supervisorName =
            SafeText(
                meeting.SupervisorName,
                "Supervisor");

        var meetingTitle =
            SafeText(
                meeting.MeetingTitle,
                "Project meeting");

        var meetingTime =
            meeting.ScheduledAtUtc
                .ToLocalTime();

        var meetingLink =
            !string.IsNullOrWhiteSpace(
                meeting.GoogleMeetLink)
                ? meeting.GoogleMeetLink
                : meeting.GoogleCalendarEventLink
                  ?? "";

        foreach (var recipient in recipients)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        recipient.UserId,

                    title:
                        $"Meeting reminder for "
                        + $"\"{projectTitle}\"",

                    message:
                        $"{supervisorName} has a project "
                        + $"meeting scheduled on "
                        + $"{meetingTime:MMM d, yyyy} at "
                        + $"{meetingTime:HH:mm}.",

                    type:
                        "meeting_reminder",

                    url:
                        $"/Student/Dashboard"
                        + $"?projectId={meeting.ProjectId}",

                    sendEmail:
                        true,

                    emailSubject:
                        $"Reminder: upcoming meeting for "
                        + projectTitle,

                    emailHtmlBody:
                        BuildReminderEmail(
                            recipient.FullName,
                            supervisorName,
                            projectTitle,
                            meetingTitle,
                            meetingTime,
                            meeting.DurationMinutes,
                            meeting.MeetingMode,
                            meetingLink,
                            meeting.Agenda,
                            meeting.NotesToPrepare),

                    projectId:
                        meeting.ProjectId,

                    actorUserId:
                        meeting.SupervisorUserId,

                    cancellationToken:
                        stoppingToken);
            }
            catch (Exception exception)
            {
                /*
                 * Continue notifying the remaining members.
                 * One recipient must not block the entire
                 * project reminder.
                 */
                logger.LogWarning(
                    exception,
                    "Meeting reminder notification failed "
                    + "for user {UserId}, project "
                    + "{ProjectId}, meeting {MeetingId}.",
                    recipient.UserId,
                    meeting.ProjectId,
                    meeting.MeetingId);
            }
        }

        /*
         * Process the reminder once per meeting.
         * NotificationService persists each successful
         * in-app notification before attempting email.
         */
        await MarkReminderProcessedAsync(
            db,
            meeting.MeetingId,
            stoppingToken);
    }

    private static async Task MarkReminderProcessedAsync(
        ApplicationDbContext db,
        int meetingId,
        CancellationToken cancellationToken)
    {
        var processedAt =
            DateTime.UtcNow;

        await db.Meetings
            .Where(meeting =>
                meeting.Id == meetingId &&
                meeting.Reminder12HoursSentAt ==
                    null)
            .ExecuteUpdateAsync(
                setters =>
                    setters
                        .SetProperty(
                            meeting =>
                                meeting
                                    .Reminder12HoursSentAt,
                            processedAt)
                        .SetProperty(
                            meeting =>
                                meeting.UpdatedAt,
                            processedAt),
                cancellationToken);
    }

    private static string BuildReminderEmail(
        string studentName,
        string supervisorName,
        string projectTitle,
        string meetingTitle,
        DateTime meetingTime,
        int durationMinutes,
        string meetingMode,
        string locationOrLink,
        string agenda,
        string notesToPrepare)
    {
        var safeStudentName =
            Encode(
                SafeText(
                    studentName,
                    "Student"));

        var safeSupervisorName =
            Encode(
                SafeText(
                    supervisorName,
                    "Supervisor"));

        var safeProjectTitle =
            Encode(
                SafeText(
                    projectTitle,
                    "Untitled Project"));

        var safeMeetingTitle =
            Encode(
                SafeText(
                    meetingTitle,
                    "Project meeting"));

        var safeMeetingMode =
            Encode(
                SafeText(
                    meetingMode,
                    "Online"));

        var safeLocationOrLink =
            Encode(
                locationOrLink);

        var safeAgenda =
            Encode(
                SafeText(
                    agenda,
                    "No agenda added."));

        var safeNotes =
            Encode(
                SafeText(
                    notesToPrepare,
                    "No preparation notes added."));

        var joinHtml =
            string.IsNullOrWhiteSpace(
                locationOrLink)
                ? ""
                : $"""
                  <p style="margin-top:18px;">
                      <a href="{safeLocationOrLink}"
                         style="display:inline-block;
                                background:#28385E;
                                color:white;
                                text-decoration:none;
                                padding:12px 18px;
                                border-radius:12px;
                                font-weight:700;">
                          Open Meeting
                      </a>
                  </p>
                  """;

        return $"""
        <div style="font-family:Arial,sans-serif;
                    background:#F7F8FA;
                    padding:28px;">
            <div style="max-width:640px;
                        margin:auto;
                        background:white;
                        border:1px solid #E8ECF2;
                        border-radius:18px;
                        padding:26px;">
                <h2 style="color:#28385E;
                           margin-top:0;">
                    Meeting Reminder
                </h2>

                <p style="color:#475569;
                          line-height:1.7;">
                    Hello {safeStudentName},
                </p>

                <p style="color:#475569;
                          line-height:1.7;">
                    This is a reminder that your project
                    <strong>{safeProjectTitle}</strong>
                    has an upcoming meeting with
                    <strong>{safeSupervisorName}</strong>.
                </p>

                <div style="background:#F7F8FA;
                            border:1px solid #E8ECF2;
                            border-radius:14px;
                            padding:14px;
                            margin-top:14px;
                            line-height:1.7;">
                    <p style="margin:0 0 8px;
                              color:#28385E;">
                        <strong>{safeMeetingTitle}</strong>
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Date:</strong>
                        {meetingTime:MMM d, yyyy}
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Time:</strong>
                        {meetingTime:HH:mm}
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Duration:</strong>
                        {durationMinutes} minutes
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Mode:</strong>
                        {safeMeetingMode}
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Agenda:</strong>
                        {safeAgenda}
                    </p>

                    <p style="margin:0;
                              color:#475569;">
                        <strong>Notes to prepare:</strong>
                        {safeNotes}
                    </p>
                </div>

                {joinHtml}

                <p style="color:#94A3B8;
                          font-size:12px;
                          margin-top:24px;">
                    FYPilot automated notification
                </p>
            </div>
        </div>
        """;
    }

    private static string SafeText(
        string? value,
        string fallback)
    {
        return string.IsNullOrWhiteSpace(
                value)
            ? fallback
            : value.Trim();
    }

    private static string Encode(
        string? value)
    {
        return System.Net.WebUtility.HtmlEncode(
            value ?? "");
    }

    private sealed record MeetingReminderCandidate(
        int MeetingId,
        int ProjectId,
        int OwnerUserId,
        int SupervisorUserId,
        string ProjectTitle,
        string MeetingTitle,
        DateTime ScheduledAtUtc,
        int DurationMinutes,
        string MeetingMode,
        string? GoogleMeetLink,
        string? GoogleCalendarEventLink,
        string Agenda,
        string NotesToPrepare,
        string SupervisorName);

    private sealed record ReminderRecipient(
        int UserId,
        string FullName,
        string Email);
}