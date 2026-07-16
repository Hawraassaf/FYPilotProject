using FYPilot.Domain.Entities;

namespace FYPilot.Web.Services.GoogleCalendar;

public interface IGoogleCalendarService
{
    string CreateAuthorizationUrl(string state);

    Task ConnectAsync(
        int supervisorId,
        string authorizationCode,
        CancellationToken cancellationToken = default);

    Task<bool> IsConnectedAsync(
        int supervisorId,
        CancellationToken cancellationToken = default);

    Task DisconnectAsync(
        int supervisorId,
        CancellationToken cancellationToken = default);

    Task<bool> HasConflictAsync(
        int supervisorId,
        DateTime startUtc,
        DateTime endUtc,
        string? excludedGoogleEventId = null,
        CancellationToken cancellationToken = default);

    Task<GoogleCalendarSyncResult> CreateEventAsync(
        int supervisorId,
        Meeting meeting,
        string studentEmail,
        CancellationToken cancellationToken = default);

    Task<GoogleCalendarSyncResult> UpdateEventAsync(
        int supervisorId,
        Meeting meeting,
        string studentEmail,
        CancellationToken cancellationToken = default);

    Task DeleteEventAsync(
        int supervisorId,
        string googleEventId,
        CancellationToken cancellationToken = default);
}

public sealed record GoogleCalendarSyncResult(
    string EventId,
    string? EventLink,
    string? GoogleMeetLink);