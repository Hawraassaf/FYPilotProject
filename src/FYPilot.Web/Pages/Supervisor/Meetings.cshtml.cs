using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.GoogleCalendar;
using FYPilot.Web.Services.Notifications;
using FYPilot.Web.Services.Supervisors;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using System.Security.Claims;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class MeetingsModel(
    ApplicationDbContext db,
    INotificationService notificationService,
    SupervisorAccessService supervisorAccess,
    IGoogleCalendarService googleCalendar,
    ILogger<MeetingsModel> logger) : PageModel
{
    public List<Meeting> UpcomingMeetings { get; private set; } = [];
    public List<Meeting> RecentMeetings { get; private set; } = [];
    public List<StudentOption> Students { get; private set; } = [];
    public Meeting? SelectedMeeting { get; private set; }

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }
    public bool GoogleCalendarConnected { get; private set; }

    [BindProperty]
    public MeetingInput Input { get; set; } = new();

    public sealed class StudentOption
    {
        public int Id { get; set; }
        public string FullName { get; set; } = "";
        public string Email { get; set; } = "";
    }

    public sealed class MeetingInput
    {
        public int? StudentId { get; set; }
        public string Title { get; set; } = "";
        public string Agenda { get; set; } = "";
        public string NotesToPrepare { get; set; } = "";
        public string MeetingMode { get; set; } = "online";
        public string LocationOrLink { get; set; } = "";
        public DateTime ScheduledAt { get; set; } = DateTime.Now.AddDays(1).Date.AddHours(10);
        public int DurationMinutes { get; set; } = 60;
        public int MeetingId { get; set; }
    }

    public async Task OnGetAsync(int? meetingId)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        GoogleCalendarConnected =
      await googleCalendar.IsConnectedAsync(SupervisorId());

        await LoadPageAsync(meetingId);
    }

    public async Task<IActionResult> OnPostSaveAsync()
    {
        var supervisorId = SupervisorId();

        if (!Input.StudentId.HasValue)
        {
            TempData["Error"] = "Please select the assigned student.";
            return RedirectToPage();
        }

        if (string.IsNullOrWhiteSpace(Input.Title))
        {
            TempData["Error"] = "Please write a meeting title.";
            return RedirectToPage();
        }

        if (Input.ScheduledAt == default)
        {
            TempData["Error"] = "Please choose a valid meeting date and time.";
            return RedirectToPage();
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            Input.StudentId.Value);

        if (!canAccessStudent)
        {
            TempData["Error"] =
                "You can only schedule meetings with students assigned to you.";

            return RedirectToPage();
        }

        var student = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(u => u.Id == Input.StudentId.Value);

        if (student == null || string.IsNullOrWhiteSpace(student.Email))
        {
            TempData["Error"] =
                "The selected student does not have a valid email.";

            return RedirectToPage();
        }

        var scheduledAtUtc = DateTime.SpecifyKind(
                Input.ScheduledAt,
                DateTimeKind.Local)
            .ToUniversalTime();

        var duration = Math.Clamp(Input.DurationMinutes, 15, 240);
        var endUtc = scheduledAtUtc.AddMinutes(duration);

        Meeting? meeting = null;

        if (Input.MeetingId > 0)
        {
            meeting = await FindOwnMeetingAsync(Input.MeetingId);

            if (meeting == null)
            {
                TempData["Error"] = "Meeting was not found.";
                return RedirectToPage();
            }
        }

        bool connected;

        try
        {
            connected = await googleCalendar.IsConnectedAsync(supervisorId);
        }
        catch
        {
            connected = false;
        }

        if (connected)
        {
            try
            {
                var conflict = await googleCalendar.HasConflictAsync(
                    supervisorId,
                    scheduledAtUtc,
                    endUtc,
                    meeting?.GoogleCalendarEventId);

                if (conflict)
                {
                    TempData["Error"] =
                        "This time conflicts with another event in your Google Calendar. Choose another time.";

                    return RedirectToPage(
                        meeting == null
                            ? null
                            : new { meetingId = meeting.Id });
                }
            }
            catch
            {
                TempData["Error"] =
                    "FYPilot could not check Google Calendar for conflicts. Reconnect Google Calendar and try again.";

                return RedirectToPage(
                    meeting == null
                        ? null
                        : new { meetingId = meeting.Id });
            }
        }

        var isNew = meeting == null;

        if (isNew)
        {
            meeting = new Meeting
            {
                SupervisorId = supervisorId,
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow,
                Status = "scheduled",

                // Must have a value before the first SaveChangesAsync().
                GoogleSyncStatus = connected
                    ? "pending"
                    : "not_connected",

                LastGoogleSyncAt = null,
                Reminder12HoursSentAt = null
            };

            db.Meetings.Add(meeting);
        }
        meeting!.StudentId = Input.StudentId.Value;
        meeting.Title = Input.Title.Trim();
        meeting.Agenda = Input.Agenda?.Trim() ?? "";
        meeting.NotesToPrepare = Input.NotesToPrepare?.Trim() ?? "";  
        meeting.MeetingMode = "online";
        meeting.LocationOrLink = "";
        meeting.ScheduledAt = scheduledAtUtc;
        meeting.DurationMinutes = duration;
        meeting.Status = "scheduled";
        meeting.UpdatedAt = DateTime.UtcNow;

        meeting.GoogleSyncStatus ??= connected
    ? "pending"
    : "not_connected";
        await db.SaveChangesAsync();

        var googleSyncSucceeded = false;

        if (connected)
        {
            try
            {
                GoogleCalendarSyncResult syncResult;

                if (isNew)
                {
                    syncResult = await googleCalendar.CreateEventAsync(
                        supervisorId,
                        meeting,
                        student.Email);
                }
                else
                {
                    syncResult = await googleCalendar.UpdateEventAsync(
                        supervisorId,
                        meeting,
                        student.Email);
                }

                meeting.GoogleCalendarEventId = syncResult.EventId;
                meeting.GoogleCalendarEventLink = syncResult.EventLink;
                meeting.GoogleMeetLink = syncResult.GoogleMeetLink;
                meeting.GoogleSyncStatus = "synced";
                meeting.LastGoogleSyncAt = DateTime.UtcNow;

                if (!string.IsNullOrWhiteSpace(syncResult.GoogleMeetLink))
                {
                    meeting.LocationOrLink = syncResult.GoogleMeetLink;
                }

                await db.SaveChangesAsync();
                googleSyncSucceeded = true;
            }
            catch (Exception ex)
            {
                meeting.GoogleSyncStatus = "failed";
                meeting.LastGoogleSyncAt = DateTime.UtcNow;

                await db.SaveChangesAsync();

                logger.LogError(
                    ex,
                    "Google Calendar synchronization failed for meeting {MeetingId} " +
                    "and supervisor {SupervisorId}.",
                    meeting.Id,
                    supervisorId);

                TempData["Error"] =
                    "The meeting was saved in FYPilot, but Google Calendar " +
                    "synchronization failed. Reconnect Google Calendar and try again.";
            }
        }
        else
        {
            meeting.GoogleSyncStatus = "not_connected";
            meeting.LastGoogleSyncAt = null;
            await db.SaveChangesAsync();
        }

        if (isNew)
        {
            await SendMeetingCreatedCommunicationAsync(meeting.Id);

            TempData["Success"] = googleSyncSucceeded
                ? "Meeting created and synchronized with Google Calendar."
                : connected
                    ? "Meeting created in FYPilot."
                    : "Meeting created. Connect Google Calendar to synchronize it.";
        }
        else
        {
            TempData["Success"] = googleSyncSucceeded
                ? "Meeting updated in FYPilot and Google Calendar."
                : "Meeting updated in FYPilot.";
        }

        return RedirectToPage(new { meetingId = meeting.Id });
    }

    public async Task<IActionResult> OnPostCompleteAsync(int meetingId)
    {
        var meeting = await FindOwnMeetingAsync(meetingId);

        if (meeting == null)
        {
            TempData["Error"] = "Meeting was not found or it does not belong to one of your assigned students.";
            return RedirectToPage();
        }

        meeting.Status = "completed";
        meeting.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync();

        TempData["Success"] = "Meeting marked as completed.";
        return RedirectToPage(new { meetingId });
    }

    public async Task<IActionResult> OnPostCancelAsync(int meetingId)
    {
        var supervisorId = SupervisorId();
        var meeting = await FindOwnMeetingAsync(meetingId);

        if (meeting == null)
        {
            TempData["Error"] =
                "Meeting was not found or it does not belong to one of your assigned students.";

            return RedirectToPage();
        }

        if (!string.IsNullOrWhiteSpace(meeting.GoogleCalendarEventId))
        {
            try
            {
                await googleCalendar.DeleteEventAsync(
                    supervisorId,
                    meeting.GoogleCalendarEventId);

                meeting.GoogleCalendarEventId = null;
                meeting.GoogleCalendarEventLink = null;
                meeting.GoogleMeetLink = null;
                meeting.GoogleSyncStatus = "cancelled";
                meeting.LastGoogleSyncAt = DateTime.UtcNow;
            }
            catch
            {
                TempData["Error"] =
                    "Google Calendar could not cancel the event. The meeting was not cancelled.";

                return RedirectToPage(new { meetingId });
            }
        }

        meeting.Status = "cancelled";
        meeting.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        TempData["Success"] =
            "Meeting cancelled. Google Calendar attendees were notified when the meeting was synced.";

        return RedirectToPage(new { meetingId });
    }

    private async Task LoadPageAsync(int? meetingId)
    {
        var supervisorId = SupervisorId();
        var now = DateTime.UtcNow;

        var assignedStudentIds = await supervisorAccess.GetAssignedStudentIdsAsync(supervisorId);

        await LoadStudentsAsync();

        UpcomingMeetings = await db.Meetings
            .AsNoTracking()
            .Include(m => m.Student)
            .Where(m =>
                m.SupervisorId == supervisorId &&
                m.StudentId.HasValue &&
                assignedStudentIds.Contains(m.StudentId.Value) &&
                m.Status == "scheduled" &&
                m.ScheduledAt.AddMinutes(m.DurationMinutes) >= now)
            .OrderBy(m => m.ScheduledAt)
            .Take(8)
            .ToListAsync();

        RecentMeetings = await db.Meetings
            .AsNoTracking()
            .Include(m => m.Student)
            .Where(m =>
                m.SupervisorId == supervisorId &&
                m.StudentId.HasValue &&
                assignedStudentIds.Contains(m.StudentId.Value) &&
                (m.Status != "scheduled" ||
                 m.ScheduledAt.AddMinutes(m.DurationMinutes) < now))
            .OrderByDescending(m => m.ScheduledAt)
            .Take(8)
            .ToListAsync();

        var selectedId = meetingId ?? UpcomingMeetings.FirstOrDefault()?.Id ?? RecentMeetings.FirstOrDefault()?.Id;

        if (selectedId.HasValue)
        {
            SelectedMeeting = await db.Meetings
                .AsNoTracking()
                .Include(m => m.Student)
                .Include(m => m.Supervisor)
                .FirstOrDefaultAsync(m =>
                    m.Id == selectedId.Value &&
                    m.SupervisorId == supervisorId &&
                    m.StudentId.HasValue &&
                    assignedStudentIds.Contains(m.StudentId.Value));
        }
    }

    private async Task LoadStudentsAsync()
    {
        var supervisorId = SupervisorId();

        Students = await db.SupervisorAssignments
            .AsNoTracking()
            .Include(a => a.Student)
            .Where(a =>
                a.SupervisorId == supervisorId &&
                a.Status == "active" &&
                a.Student != null)
            .OrderBy(a => a.Student!.FullName)
            .Select(a => new StudentOption
            {
                Id = a.StudentId,
                FullName = a.Student!.FullName,
                Email = a.Student.Email
            })
            .ToListAsync();
    }

    private async Task<Meeting?> FindOwnMeetingAsync(int meetingId)
    {
        var supervisorId = SupervisorId();

        var assignedStudentIds = await supervisorAccess.GetAssignedStudentIdsAsync(supervisorId);

        return await db.Meetings
            .FirstOrDefaultAsync(m =>
                m.Id == meetingId &&
                m.SupervisorId == supervisorId &&
                m.StudentId.HasValue &&
                assignedStudentIds.Contains(m.StudentId.Value));
    }

    private async Task SendMeetingCreatedCommunicationAsync(int meetingId)
    {
        var supervisorId = SupervisorId();

        var meeting = await db.Meetings
            .AsNoTracking()
            .Include(m => m.Student)
            .Include(m => m.Supervisor)
            .FirstOrDefaultAsync(m => m.Id == meetingId && m.SupervisorId == supervisorId);

        if (meeting == null || meeting.StudentId == null || meeting.Student == null || meeting.Supervisor == null)
        {
            return;
        }

        var canAccessStudent = await supervisorAccess.CanAccessStudentAsync(
            supervisorId,
            meeting.StudentId.Value);

        if (!canAccessStudent)
        {
            return;
        }

        var meetingTime = meeting.ScheduledAt.ToLocalTime();

        var title = "New FYP Meeting Scheduled";

        var message =
            $"Dr. {meeting.Supervisor.FullName} scheduled a meeting with you on " +
            $"{meetingTime:MMM d, yyyy} at {meetingTime:HH:mm}.";

        var meetingLink = !string.IsNullOrWhiteSpace(meeting.GoogleMeetLink)
     ? meeting.GoogleMeetLink
     : meeting.GoogleCalendarEventLink ?? "";

        var emailBody = BuildMeetingCreatedEmail(
            meeting.Student.FullName,
            meeting.Supervisor.FullName,
            meeting.Title,
            meetingTime,
            meeting.DurationMinutes,
            meeting.MeetingMode,
            meetingLink,
            meeting.Agenda,
            meeting.NotesToPrepare);
        await notificationService.NotifyUserAsync(
    meeting.StudentId.Value,
    title,
    message,
    "meeting_created",
    "/Student/Dashboard",
    sendEmail: false,
    emailSubject: "New FYP meeting scheduled",
    emailHtmlBody: emailBody);
    }

    private static string BuildMeetingCreatedEmail(
        string studentName,
        string supervisorName,
        string meetingTitle,
        DateTime meetingTime,
        int durationMinutes,
        string meetingMode,
        string locationOrLink,
        string agenda,
        string notesToPrepare)
    {
        studentName = System.Net.WebUtility.HtmlEncode(studentName);
        supervisorName = System.Net.WebUtility.HtmlEncode(supervisorName);
        meetingTitle = System.Net.WebUtility.HtmlEncode(meetingTitle);
        meetingMode = System.Net.WebUtility.HtmlEncode(meetingMode);
        locationOrLink = System.Net.WebUtility.HtmlEncode(locationOrLink);
        agenda = System.Net.WebUtility.HtmlEncode(agenda);
        notesToPrepare = System.Net.WebUtility.HtmlEncode(notesToPrepare);

        var locationHtml = string.IsNullOrWhiteSpace(locationOrLink)
            ? ""
            : $"""
              <p style="margin:0;color:#475569;">
                  <strong>Link / Location:</strong> {locationOrLink}
              </p>
              """;

        var buttonHtml = string.IsNullOrWhiteSpace(locationOrLink)
            ? ""
            : $"""
              <p style="margin-top:20px;">
                  <a href="{locationOrLink}"
                     style="display:inline-block;background:#28385E;color:white;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:700;">
                      Open Meeting
                  </a>
              </p>
              """;

        var agendaHtml = string.IsNullOrWhiteSpace(agenda)
            ? ""
            : $"""
              <p style="margin:0;color:#475569;">

                  <strong>Agenda:</strong> {agenda}
              </p>
              """;

        var notesHtml = string.IsNullOrWhiteSpace(notesToPrepare)
            ? ""
            : $"""
              <p style="margin:0;color:#475569;">
                  <strong>Notes to prepare:</strong> {notesToPrepare}
              </p>
              """;

        return $"""
        <div style="font-family:Arial,sans-serif;background:#F7F8FA;padding:28px;">
            <div style="max-width:660px;margin:auto;background:white;border:1px solid #E8ECF2;border-radius:18px;padding:26px;">
                <h2 style="color:#28385E;margin-top:0;">New FYP Meeting Scheduled</h2>

                <p style="color:#475569;line-height:1.7;">
                    Hello {studentName},
                </p>

                <p style="color:#475569;line-height:1.7;">
                    Dr. <strong>{supervisorName}</strong> scheduled a new FYP meeting with you.
                </p>

                <div style="background:#F7F8FA;border:1px solid #E8ECF2;border-radius:14px;padding:16px;margin-top:14px;">
                    <p style="margin:0 0 10px;color:#28385E;font-size:16px;">
                        <strong>{meetingTitle}</strong>
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Date:</strong> {meetingTime:MMM d, yyyy}
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Time:</strong> {meetingTime:HH:mm}
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Duration:</strong> {durationMinutes} minutes
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Mode:</strong> {meetingMode}
                    </p>

                    {locationHtml}
                    {agendaHtml}
                    {notesHtml}
                </div>

                {buttonHtml}

                <p style="color:#94A3B8;font-size:12px;margin-top:24px;">
                    This is an automated message from FYPilot.
                </p>
            </div>
        </div>
        """;
    }

    private static string NormalizeMode(string? mode)
    {
        return mode?.Trim().ToLowerInvariant() switch
        {
            "in_person" => "in_person",
            "hybrid" => "hybrid",
            _ => "online"
        };
    }

    private int SupervisorId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
    public IActionResult OnGetConnectGoogleCalendar()
    {
        var state = Guid.NewGuid().ToString("N");

        HttpContext.Session.SetString(
            "GoogleCalendarOAuthState",
            state);

        var authorizationUrl =
            googleCalendar.CreateAuthorizationUrl(state);

        return Redirect(authorizationUrl);
    }

    public async Task<IActionResult> OnPostDisconnectGoogleAsync()
    {
        await googleCalendar.DisconnectAsync(SupervisorId());

        TempData["Success"] =
            "Google Calendar disconnected.";

        return RedirectToPage();
    }
    public async Task<IActionResult> OnPostDeleteAsync(int meetingId)
    {
        var supervisorId = SupervisorId();
        var meeting = await FindOwnMeetingAsync(meetingId);

        if (meeting == null)
        {
            TempData["Error"] = "Meeting was not found.";
            return RedirectToPage();
        }

        var hadGoogleEvent =
            !string.IsNullOrWhiteSpace(meeting.GoogleCalendarEventId);

        if (hadGoogleEvent)
        {
            try
            {
                await googleCalendar.DeleteEventAsync(
                    supervisorId,
                    meeting.GoogleCalendarEventId!);
            }
            catch
            {
                TempData["Error"] =
                    "Google Calendar could not cancel the event. The meeting was not deleted.";

                return RedirectToPage(new { meetingId });
            }
        }

        db.Meetings.Remove(meeting);
        await db.SaveChangesAsync();

        TempData["Success"] = hadGoogleEvent
            ? "Meeting deleted from FYPilot and Google Calendar. Attendees received a cancellation update."
            : "Meeting deleted from FYPilot.";

        return RedirectToPage();
    }

}