using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("meetings")]
public class Meeting
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("supervisor_id")]
    public int SupervisorId { get; set; }

    [Column("student_id")]
    public int? StudentId { get; set; }

    [Column("title")]
    public string Title { get; set; } = "";

    [Column("agenda")]
    public string Agenda { get; set; } = "";

    [Column("notes_to_prepare")]
    public string NotesToPrepare { get; set; } = "";

    [Column("meeting_mode")]
    public string MeetingMode { get; set; } = "online";

    [Column("location_or_link")]
    public string LocationOrLink { get; set; } = "";

    [Column("scheduled_at")]
    public DateTime ScheduledAt { get; set; }

    [Column("duration_minutes")]
    public int DurationMinutes { get; set; } = 60;

    [Column("status")]
    public string Status { get; set; } = "scheduled";

    // Google Calendar event ID.
    // This is needed later to update or delete the same event.
    [Column("google_calendar_event_id")]
    public string? GoogleCalendarEventId { get; set; }

    // Link that opens the event inside Google Calendar.
    [Column("google_calendar_event_link")]
    public string? GoogleCalendarEventLink { get; set; }

    // The actual Google Meet video link returned by Google.
    [Column("google_meet_link")]
    public string? GoogleMeetLink { get; set; }

    // Values can include:
    // synced, failed, not_connected, cancelled.
    [Column("google_sync_status")]
    public string GoogleSyncStatus { get; set; } = "not_connected";

    // Records the most recent synchronization attempt.
    [Column("last_google_sync_at")]
    public DateTime? LastGoogleSyncAt { get; set; }

    // Used by the meeting reminder background service.
    [Column("reminder_12_hours_sent_at")]
    public DateTime? Reminder12HoursSentAt { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("updated_at")]
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [ForeignKey(nameof(SupervisorId))]
    public User? Supervisor { get; set; }

    [ForeignKey(nameof(StudentId))]
    public User? Student { get; set; }
}