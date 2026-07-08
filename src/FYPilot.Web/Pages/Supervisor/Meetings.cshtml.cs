using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class MeetingsModel(ApplicationDbContext db) : PageModel
{
    public List<Meeting> UpcomingMeetings { get; private set; } = [];
    public List<Meeting> RecentMeetings { get; private set; } = [];
    public List<StudentOption> Students { get; private set; } = [];
    public Meeting? SelectedMeeting { get; private set; }

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

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
    }

    public async Task OnGetAsync(int? meetingId)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        await LoadPageAsync(meetingId);
    }

    public async Task<IActionResult> OnPostScheduleAsync()
    {
        var supervisorId = SupervisorId();

        if (string.IsNullOrWhiteSpace(Input.Title))
        {
            TempData["Error"] = "Please write a meeting title.";
            return RedirectToPage();
        }

        if (Input.StudentId.HasValue)
        {
            var studentExists = await db.Users.AnyAsync(u => u.Id == Input.StudentId.Value && u.Role == "student");

            if (!studentExists)
            {
                TempData["Error"] = "The selected student was not found.";
                return RedirectToPage();
            }
        }

        var scheduledAtUtc = DateTime.SpecifyKind(Input.ScheduledAt, DateTimeKind.Local).ToUniversalTime();

        var meeting = new Meeting
        {
            SupervisorId = supervisorId,
            StudentId = Input.StudentId,
            Title = Input.Title.Trim(),
            Agenda = Input.Agenda?.Trim() ?? "",
            NotesToPrepare = Input.NotesToPrepare?.Trim() ?? "",
            MeetingMode = NormalizeMode(Input.MeetingMode),
            LocationOrLink = Input.LocationOrLink?.Trim() ?? "",
            ScheduledAt = scheduledAtUtc,
            DurationMinutes = Math.Clamp(Input.DurationMinutes, 15, 240),
            Status = "scheduled",
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };

        db.Meetings.Add(meeting);
        await db.SaveChangesAsync();

        TempData["Success"] = "Meeting scheduled successfully.";
        return RedirectToPage(new { meetingId = meeting.Id });
    }

    public async Task<IActionResult> OnPostCompleteAsync(int meetingId)
    {
        var meeting = await FindOwnMeetingAsync(meetingId);

        if (meeting == null)
        {
            TempData["Error"] = "Meeting was not found.";
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
        var meeting = await FindOwnMeetingAsync(meetingId);

        if (meeting == null)
        {
            TempData["Error"] = "Meeting was not found.";
            return RedirectToPage();
        }

        meeting.Status = "cancelled";
        meeting.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync();

        TempData["Success"] = "Meeting cancelled.";
        return RedirectToPage(new { meetingId });
    }

    private async Task LoadPageAsync(int? meetingId)
    {
        var supervisorId = SupervisorId();
        var now = DateTime.UtcNow;

        await LoadStudentsAsync();

        UpcomingMeetings = await db.Meetings
            .AsNoTracking()
            .Include(m => m.Student)
            .Where(m => m.SupervisorId == supervisorId &&
                        m.Status == "scheduled" &&
                        m.ScheduledAt.AddMinutes(m.DurationMinutes) >= now)
            .OrderBy(m => m.ScheduledAt)
            .Take(8)
            .ToListAsync();

        RecentMeetings = await db.Meetings
            .AsNoTracking()
            .Include(m => m.Student)
            .Where(m => m.SupervisorId == supervisorId &&
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
                .FirstOrDefaultAsync(m => m.Id == selectedId.Value && m.SupervisorId == supervisorId);
        }
    }

    private async Task LoadStudentsAsync()
    {
        Students = await db.Users
            .AsNoTracking()
            .Where(u => u.Role == "student")
            .OrderBy(u => u.FullName)
            .Select(u => new StudentOption { Id = u.Id, FullName = u.FullName, Email = u.Email })
            .ToListAsync();
    }

    private async Task<Meeting?> FindOwnMeetingAsync(int meetingId)
    {
        var supervisorId = SupervisorId();
        return await db.Meetings.FirstOrDefaultAsync(m => m.Id == meetingId && m.SupervisorId == supervisorId);
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
}
