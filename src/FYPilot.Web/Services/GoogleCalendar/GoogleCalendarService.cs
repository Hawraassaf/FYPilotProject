using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Configuration;
using Google.Apis.Auth.OAuth2;
using Google.Apis.Auth.OAuth2.Flows;
using Google.Apis.Auth.OAuth2.Requests;
using Google.Apis.Auth.OAuth2.Responses;
using Google.Apis.Calendar.v3;
using Google.Apis.Calendar.v3.Data;
using Google.Apis.Services;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace FYPilot.Web.Services.GoogleCalendar;

public class GoogleCalendarService(
    ApplicationDbContext db,
    IOptions<GoogleCalendarSettings> settings)
    : IGoogleCalendarService
{
    private readonly GoogleCalendarSettings _settings = settings.Value;

    private static readonly string[] Scopes =
    [
        CalendarService.Scope.Calendar
    ];

    public string CreateAuthorizationUrl(string state)
    {
        var flow = CreateFlow();

        AuthorizationCodeRequestUrl request =
            flow.CreateAuthorizationCodeRequest(_settings.RedirectUri);

        request.State = state;

        var authorizationUrl = request.Build().AbsoluteUri;
        var separator = authorizationUrl.Contains('?') ? "&" : "?";

        // Helps Google return a refresh token during testing.
        return authorizationUrl
               + separator
               + "prompt=consent";
    }

    public async Task ConnectAsync(
        int supervisorId,
        string authorizationCode,
        CancellationToken cancellationToken = default)
    {
        var flow = CreateFlow();

        var token = await flow.ExchangeCodeForTokenAsync(
            $"supervisor-{supervisorId}",
            authorizationCode,
            _settings.RedirectUri,
            cancellationToken);

        var existing = await db.GoogleCalendarTokens
            .FirstOrDefaultAsync(
                x => x.SupervisorId == supervisorId,
                cancellationToken);

        var expiration = CalculateExpiration(token);

        if (existing == null)
        {
            existing = new GoogleCalendarToken
            {
                SupervisorId = supervisorId,
                AccessToken = token.AccessToken ?? "",
                RefreshToken = token.RefreshToken ?? "",
                Expiration = expiration,
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            db.GoogleCalendarTokens.Add(existing);
        }
        else
        {
            existing.AccessToken =
                token.AccessToken ?? existing.AccessToken;

            if (!string.IsNullOrWhiteSpace(token.RefreshToken))
            {
                existing.RefreshToken = token.RefreshToken;
            }

            existing.Expiration = expiration;
            existing.UpdatedAt = DateTime.UtcNow;
        }

        await db.SaveChangesAsync(cancellationToken);
    }

    public Task<bool> IsConnectedAsync(
        int supervisorId,
        CancellationToken cancellationToken = default)
    {
        return db.GoogleCalendarTokens.AnyAsync(
            x => x.SupervisorId == supervisorId &&
                 x.RefreshToken != "",
            cancellationToken);
    }

    public async Task DisconnectAsync(
        int supervisorId,
        CancellationToken cancellationToken = default)
    {
        var token = await db.GoogleCalendarTokens
            .FirstOrDefaultAsync(
                x => x.SupervisorId == supervisorId,
                cancellationToken);

        if (token == null)
        {
            return;
        }

        db.GoogleCalendarTokens.Remove(token);

        await db.SaveChangesAsync(cancellationToken);
    }

    public async Task<bool> HasConflictAsync(
        int supervisorId,
        DateTime startUtc,
        DateTime endUtc,
        string? excludedGoogleEventId = null,
        CancellationToken cancellationToken = default)
    {
        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var request = calendar.Events.List("primary");

        request.TimeMinDateTimeOffset =
            new DateTimeOffset(
                DateTime.SpecifyKind(
                    startUtc,
                    DateTimeKind.Utc));

        request.TimeMaxDateTimeOffset =
            new DateTimeOffset(
                DateTime.SpecifyKind(
                    endUtc,
                    DateTimeKind.Utc));

        request.SingleEvents = true;
        request.ShowDeleted = false;
        request.MaxResults = 50;

        var response =
            await request.ExecuteAsync(cancellationToken);

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);

        return response.Items?.Any(calendarEvent =>
            calendarEvent.Status != "cancelled" &&
            calendarEvent.Id != excludedGoogleEventId) == true;
    }

    public async Task<GoogleCalendarSyncResult> CreateEventAsync(
        int supervisorId,
        Meeting meeting,
        string studentEmail,
        CancellationToken cancellationToken = default)
    {
        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var needsGoogleMeet = RequiresGoogleMeet(meeting);

        var googleEvent = BuildGoogleEvent(
            meeting,
            studentEmail,
            requestMeetLink: needsGoogleMeet);

        var request = calendar.Events.Insert(
            googleEvent,
            "primary");

        request.SendUpdates =
            EventsResource.InsertRequest.SendUpdatesEnum.All;

        if (needsGoogleMeet)
        {
            // Required. Without version 1, Google ignores
            // ConferenceData.CreateRequest.
            request.ConferenceDataVersion = 1;
        }

        var created =
            await request.ExecuteAsync(cancellationToken);

        if (needsGoogleMeet)
        {
            // Meet creation can initially be pending.
            // Fetch the event again until Google returns the link.
            created = await WaitForMeetLinkAsync(
                calendar,
                "primary",
                created.Id,
                cancellationToken);
        }

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);

        return new GoogleCalendarSyncResult(
            created.Id,
            created.HtmlLink,
            GetMeetLink(created));
    }

    public async Task<GoogleCalendarSyncResult> UpdateEventAsync(
        int supervisorId,
        Meeting meeting,
        string studentEmail,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(
                meeting.GoogleCalendarEventId))
        {
            return await CreateEventAsync(
                supervisorId,
                meeting,
                studentEmail,
                cancellationToken);
        }

        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var existing = await calendar.Events
            .Get(
                "primary",
                meeting.GoogleCalendarEventId)
            .ExecuteAsync(cancellationToken);

        var needsGoogleMeet = RequiresGoogleMeet(meeting);

        // A previous Meet request may still be pending.
        // Wait for it before creating another request.
        if (needsGoogleMeet &&
            string.IsNullOrWhiteSpace(
                GetMeetLink(existing)) &&
            IsMeetCreationPending(existing))
        {
            existing = await WaitForMeetLinkAsync(
                calendar,
                "primary",
                meeting.GoogleCalendarEventId,
                cancellationToken);
        }

        var existingMeetLink = GetMeetLink(existing);

        // Request a new Meet only when this is an online/hybrid
        // meeting and the Google event does not already have one.
        var shouldCreateMeet =
            needsGoogleMeet &&
            string.IsNullOrWhiteSpace(existingMeetLink);

        var updatedEvent = BuildGoogleEvent(
            meeting,
            studentEmail,
            requestMeetLink: shouldCreateMeet);

        // Preserve the existing Meet when updating the meeting.
        if (needsGoogleMeet &&
            !shouldCreateMeet &&
            existing.ConferenceData != null)
        {
            updatedEvent.ConferenceData =
                existing.ConferenceData;
        }

        var request = calendar.Events.Update(
            updatedEvent,
            "primary",
            meeting.GoogleCalendarEventId);

        request.SendUpdates =
            EventsResource.UpdateRequest.SendUpdatesEnum.All;

        // Required for preserving an existing Meet and for
        // generating a new Meet.
        request.ConferenceDataVersion = 1;

        var updated =
            await request.ExecuteAsync(cancellationToken);

        if (needsGoogleMeet &&
            string.IsNullOrWhiteSpace(
                GetMeetLink(updated)))
        {
            updated = await WaitForMeetLinkAsync(
                calendar,
                "primary",
                updated.Id,
                cancellationToken);
        }

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);

        return new GoogleCalendarSyncResult(
            updated.Id,
            updated.HtmlLink,
            GetMeetLink(updated));
    }

    public async Task DeleteEventAsync(
        int supervisorId,
        string googleEventId,
        CancellationToken cancellationToken = default)
    {
        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var request = calendar.Events.Delete(
            "primary",
            googleEventId);

        request.SendUpdates =
            EventsResource.DeleteRequest.SendUpdatesEnum.All;

        await request.ExecuteAsync(cancellationToken);

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);
    }

    private GoogleAuthorizationCodeFlow CreateFlow()
    {
        return new GoogleAuthorizationCodeFlow(
            new GoogleAuthorizationCodeFlow.Initializer
            {
                ClientSecrets = new ClientSecrets
                {
                    ClientId = _settings.ClientId,
                    ClientSecret = _settings.ClientSecret
                },
                Scopes = Scopes
            });
    }

    private async Task<(
        CalendarService Calendar,
        UserCredential Credential,
        GoogleCalendarToken TokenRow)>
        CreateCalendarClientAsync(
            int supervisorId,
            CancellationToken cancellationToken)
    {
        var tokenRow = await db.GoogleCalendarTokens
            .FirstOrDefaultAsync(
                x => x.SupervisorId == supervisorId,
                cancellationToken);

        if (tokenRow == null ||
            string.IsNullOrWhiteSpace(
                tokenRow.RefreshToken))
        {
            throw new InvalidOperationException(
                "Google Calendar is not connected.");
        }

        var tokenResponse = new TokenResponse
        {
            AccessToken = tokenRow.AccessToken,
            RefreshToken = tokenRow.RefreshToken,
            IssuedUtc = DateTime.UtcNow,

            ExpiresInSeconds = Math.Max(
                0,
                (long)(
                    tokenRow.Expiration -
                    DateTime.UtcNow
                ).TotalSeconds)
        };

        var credential = new UserCredential(
            CreateFlow(),
            $"supervisor-{supervisorId}",
            tokenResponse);

        if (credential.Token.IsExpired(
                Google.Apis.Util.SystemClock.Default))
        {
            await credential.RefreshTokenAsync(
                cancellationToken);
        }

        var calendar = new CalendarService(
            new BaseClientService.Initializer
            {
                HttpClientInitializer = credential,
                ApplicationName = "FYPilot"
            });

        return (calendar, credential, tokenRow);
    }

    private async Task SaveRefreshedTokenAsync(
        GoogleCalendarToken row,
        TokenResponse token,
        CancellationToken cancellationToken)
    {
        row.AccessToken =
            token.AccessToken ?? row.AccessToken;

        if (!string.IsNullOrWhiteSpace(
                token.RefreshToken))
        {
            row.RefreshToken = token.RefreshToken;
        }

        row.Expiration = CalculateExpiration(token);
        row.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync(cancellationToken);
    }

    private static DateTime CalculateExpiration(
        TokenResponse token)
    {
        var issued = token.IssuedUtc == default
            ? DateTime.UtcNow
            : token.IssuedUtc;

        return issued.AddSeconds(
            token.ExpiresInSeconds ?? 3600);
    }

    private static Event BuildGoogleEvent(
        Meeting meeting,
        string studentEmail,
        bool requestMeetLink)
    {
        var startUtc = DateTime.SpecifyKind(
            meeting.ScheduledAt,
            DateTimeKind.Utc);

        var endUtc = startUtc.AddMinutes(
            meeting.DurationMinutes);

        var googleEvent = new Event
        {
            Summary = meeting.Title,
            Description = BuildDescription(meeting),

            Location = string.Equals(
                meeting.MeetingMode,
                "in_person",
                StringComparison.OrdinalIgnoreCase)
                    ? meeting.LocationOrLink
                    : null,

            Start = new EventDateTime
            {
                DateTimeDateTimeOffset =
                    new DateTimeOffset(startUtc),

                TimeZone = "UTC"
            },

            End = new EventDateTime
            {
                DateTimeDateTimeOffset =
                    new DateTimeOffset(endUtc),

                TimeZone = "UTC"
            },

            Attendees =
            [
                new EventAttendee
                {
                    Email = studentEmail
                }
            ],

            Reminders = new Event.RemindersData
            {
                UseDefault = false,

                Overrides =
                [
                    new EventReminder
                    {
                        Method = "email",
                        Minutes = 24 * 60
                    },

                    new EventReminder
                    {
                        Method = "popup",
                        Minutes = 30
                    }
                ]
            }
        };

        if (requestMeetLink)
        {
            googleEvent.ConferenceData =
                new ConferenceData
                {
                    CreateRequest =
                        new CreateConferenceRequest
                        {
                            // Must be unique for every
                            // conference-generation request.
                            RequestId =
                                $"fypilot-{meeting.Id}-" +
                                $"{Guid.NewGuid():N}",

                            ConferenceSolutionKey =
                                new ConferenceSolutionKey
                                {
                                    Type = "hangoutsMeet"
                                }
                        }
                };
        }

        return googleEvent;
    }

    private static bool RequiresGoogleMeet(
        Meeting meeting)
    {
        return string.Equals(
                   meeting.MeetingMode,
                   "online",
                   StringComparison.OrdinalIgnoreCase) ||
               string.Equals(
                   meeting.MeetingMode,
                   "hybrid",
                   StringComparison.OrdinalIgnoreCase);
    }

    private static string? GetMeetLink(
        Event googleEvent)
    {
        // Google normally places the Meet URL here.
        if (!string.IsNullOrWhiteSpace(
                googleEvent.HangoutLink))
        {
            return googleEvent.HangoutLink;
        }

        // Fallback: retrieve the video conference entry point.
        return googleEvent.ConferenceData?
            .EntryPoints?
            .FirstOrDefault(entryPoint =>
                string.Equals(
                    entryPoint.EntryPointType,
                    "video",
                    StringComparison.OrdinalIgnoreCase))
            ?.Uri;
    }

    private static bool IsMeetCreationPending(
        Event googleEvent)
    {
        return string.Equals(
            googleEvent.ConferenceData?
                .CreateRequest?
                .Status?
                .StatusCode,
            "pending",
            StringComparison.OrdinalIgnoreCase);
    }

    private static async Task<Event>
        WaitForMeetLinkAsync(
            CalendarService calendarService,
            string calendarId,
            string googleEventId,
            CancellationToken cancellationToken)
    {
        const int maximumAttempts = 15;

        var delay = TimeSpan.FromSeconds(1);

        Event? currentEvent = null;

        for (var attempt = 1;
             attempt <= maximumAttempts;
             attempt++)
        {
            currentEvent = await calendarService.Events
                .Get(calendarId, googleEventId)
                .ExecuteAsync(cancellationToken);

            var meetLink = GetMeetLink(currentEvent);

            if (!string.IsNullOrWhiteSpace(meetLink))
            {
                return currentEvent;
            }

            var status = currentEvent.ConferenceData?
                .CreateRequest?
                .Status?
                .StatusCode;

            if (string.Equals(
                    status,
                    "failure",
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Google Calendar created the event, " +
                    "but Google Meet generation failed.");
            }

            await Task.Delay(
                delay,
                cancellationToken);
        }

        throw new InvalidOperationException(
            "Google Calendar created or updated the " +
            "event, but did not return the Google Meet " +
            "link yet. Google event ID: " +
            googleEventId);
    }

    private static string BuildDescription(
        Meeting meeting)
    {
        return $"""
        FYPilot Final Year Project Meeting

        Agenda:
        {meeting.Agenda}

        Notes to prepare:
        {meeting.NotesToPrepare}

        Mode:
        {meeting.MeetingMode}
        """;
    }
}