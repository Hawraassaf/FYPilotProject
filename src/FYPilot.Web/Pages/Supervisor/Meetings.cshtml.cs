using System.Net;
using System.Net.Mail;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.GoogleCalendar;
using FYPilot.Web.Services.Notifications;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class MeetingsModel(
    ApplicationDbContext db,
    INotificationService notificationService,
    IGoogleCalendarService googleCalendar,
    ILogger<MeetingsModel> logger)
    : PageModel
{
    public List<Meeting>
        UpcomingMeetings
    { get; private set; } = [];

    public List<Meeting>
        RecentMeetings
    { get; private set; } = [];

    public List<ProjectOption>
        Projects
    { get; private set; } = [];

    public Meeting? SelectedMeeting { get; private set; }

    public string? SuccessMessage { get; private set; }

    public string? ErrorMessage { get; private set; }

    public bool GoogleCalendarConnected { get; private set; }

    [BindProperty]
    public MeetingInput Input { get; set; } = new();

    public sealed class ProjectOption
    {
        public int Id { get; set; }

        public string Title { get; set; } = "";

        public string OfficialIdeaTitle { get; set; } = "";

        public string MemberNames { get; set; } = "";

        public int MemberCount { get; set; }
    }

    public sealed class ProjectMeetingInfo
    {
        public int ProjectId { get; set; }

        public string ProjectTitle { get; set; } = "";

        public string OfficialIdeaTitle { get; set; } = "";

        public string MemberNames { get; set; } = "";

        public int MemberCount { get; set; }
    }

    public sealed class MeetingInput
    {
        public int? ProjectId { get; set; }

        public string Title { get; set; } = "";

        public string Agenda { get; set; } = "";

        public string NotesToPrepare { get; set; } = "";

        public string MeetingMode { get; set; } = "online";

        public string LocationOrLink { get; set; } = "";

        public DateTime ScheduledAt { get; set; } =
            DateTime.Now
                .AddDays(1)
                .Date
                .AddHours(10);

        public int DurationMinutes { get; set; } = 60;

        public int MeetingId { get; set; }
    }

    public async Task OnGetAsync(
        int? meetingId,
        CancellationToken cancellationToken)
    {
        GoogleCalendarConnected =
            await IsGoogleConnectedSafeAsync(
                SupervisorId(),
                cancellationToken);

        await LoadPageAsync(
            meetingId,
            cancellationToken);
    }

    public async Task<IActionResult>
        OnPostSaveAsync(
            CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        if (!Input.ProjectId.HasValue ||
            Input.ProjectId.Value <= 0)
        {
            TempData["Error"] =
                "Select one of your assigned projects.";

            return RedirectToPage();
        }

        if (string.IsNullOrWhiteSpace(
                Input.Title))
        {
            TempData["Error"] =
                "Write a meeting title.";

            return RedirectToPage();
        }

        if (Input.Title.Trim().Length > 200)
        {
            TempData["Error"] =
                "The meeting title cannot exceed "
                + "200 characters.";

            return RedirectToPage();
        }

        if (Input.ScheduledAt == default)
        {
            TempData["Error"] =
                "Choose a valid meeting date and time.";

            return RedirectToPage();
        }

        var project =
            await LoadAssignedProjectAsync(
                supervisorId,
                Input.ProjectId.Value,
                tracked: false,
                cancellationToken);

        if (project == null)
        {
            TempData["Error"] =
                "You can schedule meetings only for "
                + "projects officially assigned to you.";

            return RedirectToPage();
        }

        var recipients =
            await LoadProjectRecipientsAsync(
                project.Id,
                project.StudentId,
                cancellationToken);

        if (recipients.Count == 0)
        {
            TempData["Error"] =
                "This project has no active members.";

            return RedirectToPage();
        }

        var invalidRecipients =
            recipients
                .Where(recipient =>
                    !IsValidEmail(
                        recipient.Email))
                .Select(recipient =>
                    recipient.FullName)
                .Distinct(
                    StringComparer.OrdinalIgnoreCase)
                .ToList();

        if (invalidRecipients.Count > 0)
        {
            TempData["Error"] =
                "Every active project member must have "
                + "a valid email before scheduling. "
                + "Check: "
                + string.Join(
                    ", ",
                    invalidRecipients);

            return RedirectToPage();
        }

        var attendeeEmails =
            recipients
                .Select(recipient =>
                    recipient.Email.Trim())
                .Distinct(
                    StringComparer.OrdinalIgnoreCase)
                .ToList();

        var scheduledAtUtc =
            DateTime.SpecifyKind(
                    Input.ScheduledAt,
                    DateTimeKind.Local)
                .ToUniversalTime();

        var duration =
            Math.Clamp(
                Input.DurationMinutes,
                15,
                240);

        var endUtc =
            scheduledAtUtc.AddMinutes(
                duration);

        Meeting? meeting =
            null;

        if (Input.MeetingId > 0)
        {
            meeting =
                await FindOwnMeetingAsync(
                    Input.MeetingId,
                    cancellationToken);

            if (meeting == null)
            {
                TempData["Error"] =
                    "The meeting was not found or the "
                    + "project is no longer assigned to you.";

                return RedirectToPage();
            }
        }

        var connected =
            await IsGoogleConnectedSafeAsync(
                supervisorId,
                cancellationToken);

        if (connected)
        {
            try
            {
                var conflict =
                    await googleCalendar.HasConflictAsync(
                        supervisorId,
                        scheduledAtUtc,
                        endUtc,
                        meeting?.GoogleCalendarEventId,
                        cancellationToken);

                if (conflict)
                {
                    TempData["Error"] =
                        "This time conflicts with another "
                        + "event in your Google Calendar. "
                        + "Choose another time.";

                    return RedirectToPage(
                        meeting == null
                            ? null
                            : new
                            {
                                meetingId =
                                    meeting.Id
                            });
                }
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Google Calendar conflict checking "
                    + "failed for supervisor "
                    + "{SupervisorId}.",
                    supervisorId);

                TempData["Error"] =
                    "FYPilot could not check Google "
                    + "Calendar for conflicts. Reconnect "
                    + "Google Calendar and try again.";

                return RedirectToPage(
                    meeting == null
                        ? null
                        : new
                        {
                            meetingId =
                                meeting.Id
                        });
            }
        }

        var isNew =
            meeting == null;

        var now =
            DateTime.UtcNow;

        if (isNew)
        {
            meeting =
                new Meeting
                {
                    SupervisorId =
                        supervisorId,

                    CreatedAt =
                        now,

                    Status =
                        "scheduled",

                    GoogleSyncStatus =
                        connected
                            ? "pending"
                            : "not_connected"
                };

            db.Meetings.Add(
                meeting);
        }

        meeting!.ProjectId =
            project.Id;

        /*
         * StudentId remains only as a temporary legacy
         * reference to the project owner. ProjectId is
         * the source of truth for meeting access.
         */
        meeting.StudentId =
            project.StudentId;

        meeting.SupervisorId =
            supervisorId;

        meeting.Title =
            Input.Title.Trim();

        meeting.Agenda =
            Input.Agenda?.Trim() ?? "";

        meeting.NotesToPrepare =
            Input.NotesToPrepare?.Trim() ?? "";

        meeting.MeetingMode =
            "online";

        meeting.LocationOrLink =
            "";

        meeting.ScheduledAt =
            scheduledAtUtc;

        meeting.DurationMinutes =
            duration;

        meeting.Status =
            "scheduled";

        meeting.UpdatedAt =
            now;

        /*
         * An edited date/time needs a new reminder.
         */
        meeting.Reminder12HoursSentAt =
            null;

        meeting.GoogleSyncStatus ??=
            connected
                ? "pending"
                : "not_connected";

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    project.Id,

                UserId =
                    supervisorId,

                ActionType =
                    isNew
                        ? "meeting_created"
                        : "meeting_updated",

                Description =
                    isNew
                        ? $"Meeting \"{meeting.Title}\" "
                          + "was scheduled."
                        : $"Meeting \"{meeting.Title}\" "
                          + "was updated.",

                CreatedAtUtc =
                    now
            });

        await db.SaveChangesAsync(
            cancellationToken);

        var googleSyncSucceeded =
            false;

        if (connected)
        {
            try
            {
                GoogleCalendarSyncResult syncResult;

                if (isNew)
                {
                    syncResult =
                        await googleCalendar
                            .CreateEventAsync(
                                supervisorId,
                                meeting,
                                attendeeEmails,
                                cancellationToken);
                }
                else
                {
                    syncResult =
                        await googleCalendar
                            .UpdateEventAsync(
                                supervisorId,
                                meeting,
                                attendeeEmails,
                                cancellationToken);
                }

                meeting.GoogleCalendarEventId =
                    syncResult.EventId;

                meeting.GoogleCalendarEventLink =
                    syncResult.EventLink;

                meeting.GoogleMeetLink =
                    syncResult.GoogleMeetLink;

                meeting.GoogleSyncStatus =
                    "synced";

                meeting.LastGoogleSyncAt =
                    DateTime.UtcNow;

                if (!string.IsNullOrWhiteSpace(
                        syncResult.GoogleMeetLink))
                {
                    meeting.LocationOrLink =
                        syncResult.GoogleMeetLink;
                }

                await db.SaveChangesAsync(
                    cancellationToken);

                googleSyncSucceeded =
                    true;
            }
            catch (Exception exception)
            {
                meeting.GoogleSyncStatus =
                    "failed";

                meeting.LastGoogleSyncAt =
                    DateTime.UtcNow;

                await db.SaveChangesAsync(
                    cancellationToken);

                logger.LogError(
                    exception,
                    "Google Calendar synchronization "
                    + "failed for meeting {MeetingId}, "
                    + "project {ProjectId}, and "
                    + "supervisor {SupervisorId}.",
                    meeting.Id,
                    project.Id,
                    supervisorId);

                TempData["Error"] =
                    "The meeting was saved in FYPilot, "
                    + "but Google Calendar synchronization "
                    + "failed. FYPilot sent a fallback "
                    + "email to the active project members.";
            }
        }
        else
        {
            meeting.GoogleSyncStatus =
                "not_connected";

            meeting.LastGoogleSyncAt =
                null;

            await db.SaveChangesAsync(
                cancellationToken);
        }

        /*
         * Google sends the official invitation/update
         * when synchronization succeeds. FYPilot then
         * creates only in-app notifications.
         *
         * When Google is unavailable, FYPilot sends the
         * fallback email itself.
         */
        await SendMeetingCommunicationAsync(
            meeting,
            project,
            recipients,
            isNew
                ? MeetingCommunicationKind.Created
                : MeetingCommunicationKind.Updated,
            sendFallbackEmail:
                !googleSyncSucceeded,
            cancellationToken);

        TempData["Success"] =
            googleSyncSucceeded
                ? isNew
                    ? "Project meeting created and "
                      + "Google Calendar invitations "
                      + "were sent to all active members."
                    : "Project meeting updated and "
                      + "Google Calendar updates were "
                      + "sent to all active members."
                : connected
                    ? isNew
                        ? "Project meeting created in "
                          + "FYPilot."
                        : "Project meeting updated in "
                          + "FYPilot."
                    : isNew
                        ? "Project meeting created. "
                          + "FYPilot sent fallback emails "
                          + "because Google Calendar is "
                          + "not connected."
                        : "Project meeting updated. "
                          + "FYPilot sent fallback emails "
                          + "because Google Calendar is "
                          + "not connected.";

        return RedirectToPage(
            new
            {
                meetingId =
                    meeting.Id
            });
    }

    public async Task<IActionResult>
        OnPostCompleteAsync(
            int meetingId,
            CancellationToken cancellationToken)
    {
        var meeting =
            await FindOwnMeetingAsync(
                meetingId,
                cancellationToken);

        if (meeting?.Project == null)
        {
            TempData["Error"] =
                "The meeting was not found or the "
                + "project is no longer assigned to you.";

            return RedirectToPage();
        }

        if (meeting.Status ==
            "completed")
        {
            TempData["Success"] =
                "The meeting is already marked completed.";

            return RedirectToPage(
                new
                {
                    meetingId
                });
        }

        meeting.Status =
            "completed";

        meeting.UpdatedAt =
            DateTime.UtcNow;

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    meeting.Project.Id,

                UserId =
                    SupervisorId(),

                ActionType =
                    "meeting_completed",

                Description =
                    $"Meeting \"{meeting.Title}\" "
                    + "was marked completed.",

                CreatedAtUtc =
                    DateTime.UtcNow
            });

        await db.SaveChangesAsync(
            cancellationToken);

        var recipients =
            await LoadProjectRecipientsAsync(
                meeting.Project.Id,
                meeting.Project.StudentId,
                cancellationToken);

        await SendMeetingCommunicationAsync(
            meeting,
            meeting.Project,
            recipients,
            MeetingCommunicationKind.Completed,
            sendFallbackEmail:
                false,
            cancellationToken);

        TempData["Success"] =
            "Project meeting marked as completed.";

        return RedirectToPage(
            new
            {
                meetingId
            });
    }

    public async Task<IActionResult>
        OnPostCancelAsync(
            int meetingId,
            CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var meeting =
            await FindOwnMeetingAsync(
                meetingId,
                cancellationToken);

        if (meeting?.Project == null)
        {
            TempData["Error"] =
                "The meeting was not found or the "
                + "project is no longer assigned to you.";

            return RedirectToPage();
        }

        var recipients =
            await LoadProjectRecipientsAsync(
                meeting.Project.Id,
                meeting.Project.StudentId,
                cancellationToken);

        var googleCancellationSucceeded =
            false;

        if (!string.IsNullOrWhiteSpace(
                meeting.GoogleCalendarEventId))
        {
            try
            {
                await googleCalendar.DeleteEventAsync(
                    supervisorId,
                    meeting.GoogleCalendarEventId,
                    cancellationToken);

                googleCancellationSucceeded =
                    true;

                meeting.GoogleCalendarEventId =
                    null;

                meeting.GoogleCalendarEventLink =
                    null;

                meeting.GoogleMeetLink =
                    null;

                meeting.GoogleSyncStatus =
                    "cancelled";

                meeting.LastGoogleSyncAt =
                    DateTime.UtcNow;
            }
            catch (Exception exception)
            {
                logger.LogError(
                    exception,
                    "Google Calendar cancellation failed "
                    + "for meeting {MeetingId}.",
                    meeting.Id);

                meeting.GoogleSyncStatus =
                    "failed";

                meeting.LastGoogleSyncAt =
                    DateTime.UtcNow;

                TempData["Error"] =
                    "The meeting was cancelled in "
                    + "FYPilot, but Google Calendar could "
                    + "not send the cancellation. "
                    + "FYPilot sent a fallback email.";
            }
        }

        meeting.Status =
            "cancelled";

        meeting.UpdatedAt =
            DateTime.UtcNow;

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    meeting.Project.Id,

                UserId =
                    supervisorId,

                ActionType =
                    "meeting_cancelled",

                Description =
                    $"Meeting \"{meeting.Title}\" "
                    + "was cancelled.",

                CreatedAtUtc =
                    DateTime.UtcNow
            });

        await db.SaveChangesAsync(
            cancellationToken);

        await SendMeetingCommunicationAsync(
            meeting,
            meeting.Project,
            recipients,
            MeetingCommunicationKind.Cancelled,
            sendFallbackEmail:
                !googleCancellationSucceeded,
            cancellationToken);

        TempData["Success"] =
            googleCancellationSucceeded
                ? "Project meeting cancelled. Google "
                  + "Calendar sent the official "
                  + "cancellation to all attendees."
                : "Project meeting cancelled in FYPilot.";

        return RedirectToPage(
            new
            {
                meetingId
            });
    }

    public IActionResult OnGetConnectGoogleCalendar()
    {
        var state =
            Guid.NewGuid()
                .ToString("N");

        HttpContext.Session.SetString(
            "GoogleCalendarOAuthState",
            state);

        var authorizationUrl =
            googleCalendar
                .CreateAuthorizationUrl(
                    state);

        return Redirect(
            authorizationUrl);
    }

    public async Task<IActionResult>
        OnPostDisconnectGoogleAsync(
            CancellationToken cancellationToken)
    {
        await googleCalendar.DisconnectAsync(
            SupervisorId(),
            cancellationToken);

        TempData["Success"] =
            "Google Calendar disconnected.";

        return RedirectToPage();
    }

    public async Task<IActionResult>
        OnPostDeleteAsync(
            int meetingId,
            CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var meeting =
            await FindOwnMeetingAsync(
                meetingId,
                cancellationToken);

        if (meeting?.Project == null)
        {
            TempData["Error"] =
                "The meeting was not found or the "
                + "project is no longer assigned to you.";

            return RedirectToPage();
        }

        var project =
            meeting.Project;

        var recipients =
            await LoadProjectRecipientsAsync(
                project.Id,
                project.StudentId,
                cancellationToken);

        var googleCancellationSucceeded =
            false;

        if (!string.IsNullOrWhiteSpace(
                meeting.GoogleCalendarEventId))
        {
            try
            {
                await googleCalendar.DeleteEventAsync(
                    supervisorId,
                    meeting.GoogleCalendarEventId,
                    cancellationToken);

                googleCancellationSucceeded =
                    true;
            }
            catch (Exception exception)
            {
                logger.LogError(
                    exception,
                    "Google Calendar deletion failed for "
                    + "meeting {MeetingId}.",
                    meeting.Id);

                TempData["Error"] =
                    "The meeting was deleted from "
                    + "FYPilot, but Google Calendar could "
                    + "not send the cancellation. "
                    + "FYPilot sent a fallback email.";
            }
        }

        db.ProjectActivities.Add(
            new ProjectActivity
            {
                ProjectId =
                    project.Id,

                UserId =
                    supervisorId,

                ActionType =
                    "meeting_deleted",

                Description =
                    $"Meeting \"{meeting.Title}\" "
                    + "was deleted.",

                CreatedAtUtc =
                    DateTime.UtcNow
            });

        db.Meetings.Remove(
            meeting);

        await db.SaveChangesAsync(
            cancellationToken);

        await SendMeetingCommunicationAsync(
            meeting,
            project,
            recipients,
            MeetingCommunicationKind.Deleted,
            sendFallbackEmail:
                !googleCancellationSucceeded,
            cancellationToken);

        TempData["Success"] =
            googleCancellationSucceeded
                ? "Meeting deleted from FYPilot and "
                  + "Google Calendar. Google sent the "
                  + "official cancellation."
                : "Meeting deleted from FYPilot.";

        return RedirectToPage();
    }

    public ProjectMeetingInfo GetProjectInfo(
        Meeting? meeting)
    {
        if (meeting?.Project == null)
        {
            return new ProjectMeetingInfo
            {
                ProjectId =
                    meeting?.ProjectId ?? 0,

                ProjectTitle =
                    "Project unavailable",

                OfficialIdeaTitle =
                    "No official idea selected",

                MemberNames =
                    meeting?.Student?.FullName
                    ?? "No active members",

                MemberCount =
                    meeting?.StudentId.HasValue == true
                        ? 1
                        : 0
            };
        }

        var project =
            meeting.Project;

        var members =
            project.Members
                .Where(member =>
                    member.Status ==
                        "active" &&
                    member.User != null)
                .Select(member =>
                    new
                    {
                        member.UserId,
                        Name =
                            member.User!.FullName
                    })
                .ToList();

        if (members.All(member =>
                member.UserId !=
                    project.StudentId))
        {
            members.Add(
                new
                {
                    UserId =
                        project.StudentId,

                    Name =
                        project.Student?.FullName
                        ?? meeting.Student?.FullName
                        ?? "Project owner"
                });
        }

        var distinctMembers =
            members
                .GroupBy(member =>
                    member.UserId)
                .Select(group =>
                    group.First())
                .OrderBy(member =>
                    member.Name)
                .ToList();

        return new ProjectMeetingInfo
        {
            ProjectId =
                project.Id,

            ProjectTitle =
                SafeProjectTitle(
                    project.Title),

            OfficialIdeaTitle =
                project.ProjectIdea?.Title
                ?? "No official idea selected",

            MemberNames =
                string.Join(
                    ", ",
                    distinctMembers.Select(
                        member =>
                            member.Name)),

            MemberCount =
                distinctMembers.Count
        };
    }

    private async Task LoadPageAsync(
        int? meetingId,
        CancellationToken cancellationToken)
    {
        var supervisorId =
            SupervisorId();

        var now =
            DateTime.UtcNow;

        await LoadProjectsAsync(
            supervisorId,
            cancellationToken);

        UpcomingMeetings =
            await BuildMeetingQuery(
                    supervisorId,
                    tracked: false)
                .Where(meeting =>
                    meeting.Status ==
                        "scheduled" &&
                    meeting.ScheduledAt
                        .AddMinutes(
                            meeting.DurationMinutes) >=
                        now)
                .OrderBy(meeting =>
                    meeting.ScheduledAt)
                .Take(8)
                .ToListAsync(
                    cancellationToken);

        RecentMeetings =
            await BuildMeetingQuery(
                    supervisorId,
                    tracked: false)
                .Where(meeting =>
                    meeting.Status !=
                        "scheduled" ||
                    meeting.ScheduledAt
                        .AddMinutes(
                            meeting.DurationMinutes) <
                        now)
                .OrderByDescending(meeting =>
                    meeting.ScheduledAt)
                .Take(8)
                .ToListAsync(
                    cancellationToken);

        var selectedId =
            meetingId
            ?? UpcomingMeetings
                .FirstOrDefault()
                ?.Id
            ?? RecentMeetings
                .FirstOrDefault()
                ?.Id;

        if (selectedId.HasValue)
        {
            SelectedMeeting =
                await BuildMeetingQuery(
                        supervisorId,
                        tracked: false)
                    .FirstOrDefaultAsync(
                        meeting =>
                            meeting.Id ==
                                selectedId.Value,
                        cancellationToken);
        }
    }

    private async Task LoadProjectsAsync(
        int supervisorId,
        CancellationToken cancellationToken)
    {
        var projects =
            await db.Projects
                .AsNoTracking()
                .Include(project =>
                    project.Student)
                .Include(project =>
                    project.ProjectIdea)
                .Include(project =>
                    project.Members
                        .Where(member =>
                            member.Status ==
                                "active"))
                .ThenInclude(member =>
                    member.User)
                .Where(project =>
                    project.SupervisorId ==
                        supervisorId &&
                    project.SupervisorAssignmentStatus ==
                        "active")
                .OrderBy(project =>
                    project.Title)
                .AsSplitQuery()
                .ToListAsync(
                    cancellationToken);

        Projects =
            projects
                .Select(project =>
                {
                    var members =
                        project.Members
                            .Where(member =>
                                member.Status ==
                                    "active" &&
                                member.User != null)
                            .Select(member =>
                                new
                                {
                                    member.UserId,
                                    member.User!.FullName
                                })
                            .ToList();

                    if (members.All(member =>
                            member.UserId !=
                                project.StudentId))
                    {
                        members.Add(
                            new
                            {
                                UserId =
                                    project.StudentId,

                                FullName =
                                    project.Student?.FullName
                                    ?? "Project owner"
                            });
                    }

                    var distinctMembers =
                        members
                            .GroupBy(member =>
                                member.UserId)
                            .Select(group =>
                                group.First())
                            .OrderBy(member =>
                                member.FullName)
                            .ToList();

                    return new ProjectOption
                    {
                        Id =
                            project.Id,

                        Title =
                            SafeProjectTitle(
                                project.Title),

                        OfficialIdeaTitle =
                            project.ProjectIdea?.Title
                            ?? "No official idea selected",

                        MemberNames =
                            string.Join(
                                ", ",
                                distinctMembers.Select(
                                    member =>
                                        member.FullName)),

                        MemberCount =
                            distinctMembers.Count
                    };
                })
                .ToList();
    }

    private IQueryable<Meeting> BuildMeetingQuery(
        int supervisorId,
        bool tracked)
    {
        IQueryable<Meeting> query =
            db.Meetings
                .Include(meeting =>
                    meeting.Student)
                .Include(meeting =>
                    meeting.Supervisor)
                .Include(meeting =>
                    meeting.Project)
                .ThenInclude(project =>
                    project!.Student)
                .Include(meeting =>
                    meeting.Project)
                .ThenInclude(project =>
                    project!.ProjectIdea)
                .Include(meeting =>
                    meeting.Project)
                .ThenInclude(project =>
                    project!.Members
                        .Where(member =>
                            member.Status ==
                                "active"))
                .ThenInclude(member =>
                    member.User)
                .Where(meeting =>
                    meeting.ProjectId.HasValue &&
                    meeting.Project != null &&
                    meeting.SupervisorId ==
                        supervisorId &&
                    meeting.Project.SupervisorId ==
                        supervisorId &&
                    meeting.Project
                        .SupervisorAssignmentStatus ==
                        "active")
                .AsSplitQuery();

        if (!tracked)
        {
            query =
                query.AsNoTracking();
        }

        return query;
    }

    private async Task<Project?>
        LoadAssignedProjectAsync(
            int supervisorId,
            int projectId,
            bool tracked,
            CancellationToken cancellationToken)
    {
        IQueryable<Project> query =
            db.Projects
                .Include(project =>
                    project.ProjectIdea);

        if (!tracked)
        {
            query =
                query.AsNoTracking();
        }

        return await query
            .FirstOrDefaultAsync(
                project =>
                    project.Id ==
                        projectId &&
                    project.SupervisorId ==
                        supervisorId &&
                    project.SupervisorAssignmentStatus ==
                        "active",
                cancellationToken);
    }

    private async Task<Meeting?>
        FindOwnMeetingAsync(
            int meetingId,
            CancellationToken cancellationToken)
    {
        return await BuildMeetingQuery(
                SupervisorId(),
                tracked: true)
            .FirstOrDefaultAsync(
                meeting =>
                    meeting.Id ==
                        meetingId,
                cancellationToken);
    }

    private async Task<List<ProjectRecipient>>
        LoadProjectRecipientsAsync(
            int projectId,
            int ownerId,
            CancellationToken cancellationToken)
    {
        var recipients =
            await db.ProjectMembers
                .AsNoTracking()
                .Where(member =>
                    member.ProjectId ==
                        projectId &&
                    member.Status ==
                        "active")
                .Select(member =>
                    new ProjectRecipient(
                        member.UserId,
                        member.User != null
                            ? member.User.FullName
                            : "Student",
                        member.User != null
                            ? member.User.Email
                            : ""))
                .ToListAsync(
                    cancellationToken);

        if (recipients.All(recipient =>
                recipient.UserId !=
                    ownerId))
        {
            var owner =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            ownerId)
                    .Select(user =>
                        new ProjectRecipient(
                            user.Id,
                            user.FullName,
                            user.Email))
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (owner != null)
            {
                recipients.Add(
                    owner);
            }
        }

        return recipients
            .GroupBy(recipient =>
                recipient.UserId)
            .Select(group =>
                group.First())
            .OrderBy(recipient =>
                recipient.FullName)
            .ToList();
    }

    private async Task SendMeetingCommunicationAsync(
        Meeting meeting,
        Project project,
        IReadOnlyCollection<ProjectRecipient> recipients,
        MeetingCommunicationKind kind,
        bool sendFallbackEmail,
        CancellationToken cancellationToken)
    {
        if (recipients.Count == 0)
        {
            return;
        }

        var supervisorName =
            meeting.Supervisor?.FullName;

        if (string.IsNullOrWhiteSpace(
                supervisorName))
        {
            supervisorName =
                await db.Users
                    .AsNoTracking()
                    .Where(user =>
                        user.Id ==
                            meeting.SupervisorId)
                    .Select(user =>
                        user.FullName)
                    .FirstOrDefaultAsync(
                        cancellationToken)
                ?? "Supervisor";
        }

        var projectTitle =
            SafeProjectTitle(
                project.Title);

        var memberNames =
            string.Join(
                ", ",
                recipients.Select(
                    recipient =>
                        recipient.FullName));

        var meetingTime =
            meeting.ScheduledAt
                .ToLocalTime();

        var meetingLink =
            !string.IsNullOrWhiteSpace(
                meeting.GoogleMeetLink)
                ? meeting.GoogleMeetLink
                : meeting.GoogleCalendarEventLink
                  ?? "";

        var metadata =
            CommunicationMetadata(
                kind,
                projectTitle,
                meeting.Title);

        foreach (var recipient in recipients)
        {
            try
            {
                await notificationService.NotifyUserAsync(
                    recipientUserId:
                        recipient.UserId,

                    title:
                        metadata.Title,

                    message:
                        BuildNotificationMessage(
                            kind,
                            supervisorName,
                            projectTitle,
                            meetingTime),

                    type:
                        metadata.Type,

                    url:
                        "/Student/Dashboard",

                    sendEmail:
                        sendFallbackEmail,

                    emailSubject:
                        metadata.EmailSubject,

                    emailHtmlBody:
                        BuildMeetingEmail(
                            kind,
                            recipient.FullName,
                            supervisorName,
                            projectTitle,
                            memberNames,
                            meeting.Title,
                            meetingTime,
                            meeting.DurationMinutes,
                            meetingLink,
                            meeting.Agenda,
                            meeting.NotesToPrepare));
            }
            catch (Exception exception)
            {
                logger.LogWarning(
                    exception,
                    "Meeting database action succeeded, "
                    + "but notification failed for user "
                    + "{UserId}, project {ProjectId}, "
                    + "and meeting {MeetingId}.",
                    recipient.UserId,
                    project.Id,
                    meeting.Id);
            }
        }
    }

    private static CommunicationInfo
        CommunicationMetadata(
            MeetingCommunicationKind kind,
            string projectTitle,
            string meetingTitle)
    {
        return kind switch
        {
            MeetingCommunicationKind.Created =>
                new CommunicationInfo(
                    "New Project Meeting Scheduled",
                    "project_meeting_created",
                    $"New meeting for {projectTitle}: "
                    + meetingTitle),

            MeetingCommunicationKind.Updated =>
                new CommunicationInfo(
                    "Project Meeting Updated",
                    "project_meeting_updated",
                    $"Meeting updated for {projectTitle}: "
                    + meetingTitle),

            MeetingCommunicationKind.Cancelled =>
                new CommunicationInfo(
                    "Project Meeting Cancelled",
                    "project_meeting_cancelled",
                    $"Meeting cancelled for "
                    + projectTitle),

            MeetingCommunicationKind.Deleted =>
                new CommunicationInfo(
                    "Project Meeting Deleted",
                    "project_meeting_deleted",
                    $"Meeting deleted for "
                    + projectTitle),

            MeetingCommunicationKind.Completed =>
                new CommunicationInfo(
                    "Project Meeting Completed",
                    "project_meeting_completed",
                    $"Meeting completed for "
                    + projectTitle),

            _ =>
                new CommunicationInfo(
                    "Project Meeting Updated",
                    "project_meeting_updated",
                    $"Meeting update for "
                    + projectTitle)
        };
    }

    private static string BuildNotificationMessage(
        MeetingCommunicationKind kind,
        string supervisorName,
        string projectTitle,
        DateTime meetingTime)
    {
        return kind switch
        {
            MeetingCommunicationKind.Created =>
                $"{supervisorName} scheduled a meeting "
                + $"for project \"{projectTitle}\" on "
                + $"{meetingTime:MMM d, yyyy} at "
                + $"{meetingTime:HH:mm}.",

            MeetingCommunicationKind.Updated =>
                $"{supervisorName} updated the meeting "
                + $"for project \"{projectTitle}\". "
                + $"The current time is "
                + $"{meetingTime:MMM d, yyyy} at "
                + $"{meetingTime:HH:mm}.",

            MeetingCommunicationKind.Cancelled =>
                $"The meeting for project "
                + $"\"{projectTitle}\" was cancelled.",

            MeetingCommunicationKind.Deleted =>
                $"The meeting for project "
                + $"\"{projectTitle}\" was deleted.",

            MeetingCommunicationKind.Completed =>
                $"The meeting for project "
                + $"\"{projectTitle}\" was marked "
                + "completed.",

            _ =>
                $"The meeting for project "
                + $"\"{projectTitle}\" was updated."
        };
    }

    private static string BuildMeetingEmail(
        MeetingCommunicationKind kind,
        string recipientName,
        string supervisorName,
        string projectTitle,
        string memberNames,
        string meetingTitle,
        DateTime meetingTime,
        int durationMinutes,
        string meetingLink,
        string agenda,
        string notesToPrepare)
    {
        var heading =
            kind switch
            {
                MeetingCommunicationKind.Created =>
                    "New Project Meeting Scheduled",

                MeetingCommunicationKind.Updated =>
                    "Project Meeting Updated",

                MeetingCommunicationKind.Cancelled =>
                    "Project Meeting Cancelled",

                MeetingCommunicationKind.Deleted =>
                    "Project Meeting Deleted",

                MeetingCommunicationKind.Completed =>
                    "Project Meeting Completed",

                _ =>
                    "Project Meeting Update"
            };

        var actionText =
            kind switch
            {
                MeetingCommunicationKind.Created =>
                    "scheduled a new meeting",

                MeetingCommunicationKind.Updated =>
                    "updated the meeting",

                MeetingCommunicationKind.Cancelled =>
                    "cancelled the meeting",

                MeetingCommunicationKind.Deleted =>
                    "deleted the meeting",

                MeetingCommunicationKind.Completed =>
                    "marked the meeting as completed",

                _ =>
                    "updated the meeting"
            };

        var showSchedule =
            kind is
                MeetingCommunicationKind.Created or
                MeetingCommunicationKind.Updated or
                MeetingCommunicationKind.Completed;

        var showJoinLink =
            (
                kind is
                    MeetingCommunicationKind.Created or
                    MeetingCommunicationKind.Updated
            ) &&
            !string.IsNullOrWhiteSpace(
                meetingLink);

        var scheduleHtml =
            showSchedule
                ? $"""
                  <p style="margin:0;color:#475569;">
                      <strong>Date:</strong>
                      {meetingTime:MMM d, yyyy}
                  </p>
                  <p style="margin:0;color:#475569;">
                      <strong>Time:</strong>
                      {meetingTime:HH:mm}
                  </p>
                  <p style="margin:0;color:#475569;">
                      <strong>Duration:</strong>
                      {durationMinutes} minutes
                  </p>
                  """
                : "";

        var linkHtml =
            showJoinLink
                ? $"""
                  <p style="margin-top:20px;">
                      <a href="{Encode(meetingLink)}"
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
                  """
                : "";

        return $"""
        <div style="font-family:Arial,sans-serif;
                    background:#F7F8FA;
                    padding:28px;">
            <div style="max-width:660px;
                        margin:auto;
                        background:white;
                        border:1px solid #E8ECF2;
                        border-radius:18px;
                        padding:26px;">
                <h2 style="color:#28385E;
                           margin-top:0;">
                    {Encode(heading)}
                </h2>

                <p style="color:#475569;
                          line-height:1.7;">
                    Hello {Encode(recipientName)},
                </p>

                <p style="color:#475569;
                          line-height:1.7;">
                    {Encode(supervisorName)}
                    {Encode(actionText)} for
                    <strong>{Encode(projectTitle)}</strong>.
                </p>

                <div style="background:#F7F8FA;
                            border:1px solid #E8ECF2;
                            border-radius:14px;
                            padding:16px;
                            margin-top:14px;
                            line-height:1.7;">
                    <p style="margin:0 0 10px;
                              color:#28385E;
                              font-size:16px;">
                        <strong>{Encode(meetingTitle)}</strong>
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Project members:</strong>
                        {Encode(memberNames)}
                    </p>

                    {scheduleHtml}

                    <p style="margin:0;color:#475569;">
                        <strong>Agenda:</strong>
                        {Encode(
                            string.IsNullOrWhiteSpace(agenda)
                                ? "No agenda added."
                                : agenda)}
                    </p>

                    <p style="margin:0;color:#475569;">
                        <strong>Notes to prepare:</strong>
                        {Encode(
                            string.IsNullOrWhiteSpace(
                                notesToPrepare)
                                ? "No preparation notes added."
                                : notesToPrepare)}
                    </p>
                </div>

                {linkHtml}

                <p style="color:#94A3B8;
                          font-size:12px;
                          margin-top:24px;">
                    FYPilot fallback notification
                </p>
            </div>
        </div>
        """;
    }

    private async Task<bool>
        IsGoogleConnectedSafeAsync(
            int supervisorId,
            CancellationToken cancellationToken)
    {
        try
        {
            return await googleCalendar
                .IsConnectedAsync(
                    supervisorId,
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Could not determine Google Calendar "
                + "connection status for supervisor "
                + "{SupervisorId}.",
                supervisorId);

            return false;
        }
    }

    private static bool IsValidEmail(
        string? email)
    {
        return !string.IsNullOrWhiteSpace(
                   email) &&
               MailAddress.TryCreate(
                   email.Trim(),
                   out _);
    }

    private static string SafeProjectTitle(
        string? title)
    {
        return string.IsNullOrWhiteSpace(
            title)
            ? "Untitled Project"
            : title.Trim();
    }

    private static string Encode(
        string? value)
    {
        return WebUtility.HtmlEncode(
            value ?? "");
    }

    private int SupervisorId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        if (int.TryParse(
                value,
                out var supervisorId))
        {
            return supervisorId;
        }

        throw new InvalidOperationException(
            "Unable to identify the logged-in "
            + "supervisor.");
    }

    private sealed record ProjectRecipient(
        int UserId,
        string FullName,
        string Email);

    private sealed record CommunicationInfo(
        string Title,
        string Type,
        string EmailSubject);

    private enum MeetingCommunicationKind
    {
        Created,
        Updated,
        Cancelled,
        Deleted,
        Completed
    }
}