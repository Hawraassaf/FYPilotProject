using System.Net.Mail;
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
            flow.CreateAuthorizationCodeRequest(
                _settings.RedirectUri);

        request.State = state;

        var authorizationUrl =
            request.Build().AbsoluteUri;

        var separator =
            authorizationUrl.Contains('?')
                ? "&"
                : "?";

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

        var token =
            await flow.ExchangeCodeForTokenAsync(
                $"supervisor-{supervisorId}",
                authorizationCode,
                _settings.RedirectUri,
                cancellationToken);

        var existing =
            await db.GoogleCalendarTokens
                .FirstOrDefaultAsync(
                    item =>
                        item.SupervisorId ==
                            supervisorId,
                    cancellationToken);

        var expiration =
            CalculateExpiration(token);

        if (existing == null)
        {
            existing =
                new GoogleCalendarToken
                {
                    SupervisorId =
                        supervisorId,

                    AccessToken =
                        token.AccessToken ?? "",

                    RefreshToken =
                        token.RefreshToken ?? "",

                    Expiration =
                        expiration,

                    CreatedAt =
                        DateTime.UtcNow,

                    UpdatedAt =
                        DateTime.UtcNow
                };

            db.GoogleCalendarTokens.Add(
                existing);
        }
        else
        {
            existing.AccessToken =
                token.AccessToken
                ?? existing.AccessToken;

            if (!string.IsNullOrWhiteSpace(
                    token.RefreshToken))
            {
                existing.RefreshToken =
                    token.RefreshToken;
            }

            existing.Expiration =
                expiration;

            existing.UpdatedAt =
                DateTime.UtcNow;
        }

        await db.SaveChangesAsync(
            cancellationToken);
    }

    public Task<bool> IsConnectedAsync(
        int supervisorId,
        CancellationToken cancellationToken = default)
    {
        return db.GoogleCalendarTokens
            .AnyAsync(
                item =>
                    item.SupervisorId ==
                        supervisorId &&
                    item.RefreshToken != "",
                cancellationToken);
    }

    public async Task DisconnectAsync(
        int supervisorId,
        CancellationToken cancellationToken = default)
    {
        var token =
            await db.GoogleCalendarTokens
                .FirstOrDefaultAsync(
                    item =>
                        item.SupervisorId ==
                            supervisorId,
                    cancellationToken);

        if (token == null)
        {
            return;
        }

        db.GoogleCalendarTokens.Remove(
            token);

        await db.SaveChangesAsync(
            cancellationToken);
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

        var request =
            calendar.Events.List("primary");

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

        request.SingleEvents =
            true;

        request.ShowDeleted =
            false;

        request.MaxResults =
            50;

        var response =
            await request.ExecuteAsync(
                cancellationToken);

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);

        return response.Items?.Any(
            calendarEvent =>
                calendarEvent.Status !=
                    "cancelled" &&
                calendarEvent.Id !=
                    excludedGoogleEventId) == true;
    }

    public async Task<GoogleCalendarSyncResult>
        CreateEventAsync(
            int supervisorId,
            Meeting meeting,
            IReadOnlyCollection<string> attendeeEmails,
            CancellationToken cancellationToken = default)
    {
        var normalizedEmails =
            NormalizeAttendeeEmails(
                attendeeEmails);

        if (normalizedEmails.Count == 0)
        {
            throw new InvalidOperationException(
                "The project has no valid attendee "
                + "email addresses.");
        }

        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var needsGoogleMeet =
            RequiresGoogleMeet(meeting);

        var googleEvent =
            BuildGoogleEvent(
                meeting,
                normalizedEmails,
                requestMeetLink:
                    needsGoogleMeet);

        /*
         * First create the event silently.
         *
         * Invitations are sent only after the Google
         * Meet link is ready. Therefore, a Meet
         * generation failure cannot cause both a Google
         * invitation and a FYPilot fallback email.
         */
        var insertRequest =
            calendar.Events.Insert(
                googleEvent,
                "primary");

        insertRequest.SendUpdates =
            EventsResource.InsertRequest
                .SendUpdatesEnum.None;

        if (needsGoogleMeet)
        {
            insertRequest.ConferenceDataVersion =
                1;
        }

        var created =
            await insertRequest.ExecuteAsync(
                cancellationToken);

        try
        {
            if (needsGoogleMeet)
            {
                created =
                    await WaitForMeetLinkAsync(
                        calendar,
                        "primary",
                        created.Id,
                        cancellationToken);
            }
        }
        catch
        {
            /*
             * The event has not notified attendees yet.
             * Remove the silent draft before allowing
             * FYPilot to send its fallback email.
             */
            try
            {
                var cleanup =
                    calendar.Events.Delete(
                        "primary",
                        created.Id);

                cleanup.SendUpdates =
                    EventsResource.DeleteRequest
                        .SendUpdatesEnum.None;

                await cleanup.ExecuteAsync(
                    cancellationToken);
            }
            catch
            {
                // Preserve the original synchronization
                // exception.
            }

            throw;
        }

        /*
         * This final update is the one official Google
         * invitation sent to every active member.
         */
        var invitationRequest =
            calendar.Events.Update(
                created,
                "primary",
                created.Id);

        invitationRequest.SendUpdates =
            EventsResource.UpdateRequest
                .SendUpdatesEnum.All;

        invitationRequest.ConferenceDataVersion =
            1;

        created =
            await invitationRequest.ExecuteAsync(
                cancellationToken);

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);

        return new GoogleCalendarSyncResult(
            created.Id,
            created.HtmlLink,
            GetMeetLink(created));
    }

    public async Task<GoogleCalendarSyncResult>
        UpdateEventAsync(
            int supervisorId,
            Meeting meeting,
            IReadOnlyCollection<string> attendeeEmails,
            CancellationToken cancellationToken = default)
    {
        var normalizedEmails =
            NormalizeAttendeeEmails(
                attendeeEmails);

        if (normalizedEmails.Count == 0)
        {
            throw new InvalidOperationException(
                "The project has no valid attendee "
                + "email addresses.");
        }

        if (string.IsNullOrWhiteSpace(
                meeting.GoogleCalendarEventId))
        {
            return await CreateEventAsync(
                supervisorId,
                meeting,
                normalizedEmails,
                cancellationToken);
        }

        var (calendar, credential, tokenRow) =
            await CreateCalendarClientAsync(
                supervisorId,
                cancellationToken);

        var existing =
            await calendar.Events
                .Get(
                    "primary",
                    meeting.GoogleCalendarEventId)
                .ExecuteAsync(
                    cancellationToken);

        var needsGoogleMeet =
            RequiresGoogleMeet(meeting);

        if (needsGoogleMeet &&
            string.IsNullOrWhiteSpace(
                GetMeetLink(existing)) &&
            IsMeetCreationPending(existing))
        {
            existing =
                await WaitForMeetLinkAsync(
                    calendar,
                    "primary",
                    meeting.GoogleCalendarEventId,
                    cancellationToken);
        }

        var existingMeetLink =
            GetMeetLink(existing);

        var needsConferencePreparation =
            needsGoogleMeet &&
            string.IsNullOrWhiteSpace(
                existingMeetLink);

        if (needsConferencePreparation)
        {
            /*
             * Prepare the new Meet silently first.
             * Attendee update emails are delayed until
             * the Meet link exists.
             */
            var preparationEvent =
                BuildGoogleEvent(
                    meeting,
                    normalizedEmails,
                    requestMeetLink:
                        true);

            var preparationRequest =
                calendar.Events.Update(
                    preparationEvent,
                    "primary",
                    meeting.GoogleCalendarEventId);

            preparationRequest.SendUpdates =
                EventsResource.UpdateRequest
                    .SendUpdatesEnum.None;

            preparationRequest.ConferenceDataVersion =
                1;

            var prepared =
                await preparationRequest.ExecuteAsync(
                    cancellationToken);

            existing =
                await WaitForMeetLinkAsync(
                    calendar,
                    "primary",
                    prepared.Id,
                    cancellationToken);
        }

        var finalEvent =
            BuildGoogleEvent(
                meeting,
                normalizedEmails,
                requestMeetLink:
                    false);

        if (needsGoogleMeet &&
            existing.ConferenceData != null)
        {
            finalEvent.ConferenceData =
                existing.ConferenceData;
        }

        /*
         * Google sends exactly one official update to
         * current attendees. Attendees removed from the
         * project are removed from the event by this
         * attendee replacement.
         */
        var updateRequest =
            calendar.Events.Update(
                finalEvent,
                "primary",
                meeting.GoogleCalendarEventId);

        updateRequest.SendUpdates =
            EventsResource.UpdateRequest
                .SendUpdatesEnum.All;

        updateRequest.ConferenceDataVersion =
            1;

        var updated =
            await updateRequest.ExecuteAsync(
                cancellationToken);

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

        var request =
            calendar.Events.Delete(
                "primary",
                googleEventId);

        /*
         * Google sends one official cancellation to
         * every attendee.
         */
        request.SendUpdates =
            EventsResource.DeleteRequest
                .SendUpdatesEnum.All;

        await request.ExecuteAsync(
            cancellationToken);

        await SaveRefreshedTokenAsync(
            tokenRow,
            credential.Token,
            cancellationToken);
    }

    private GoogleAuthorizationCodeFlow CreateFlow()
    {
        return new GoogleAuthorizationCodeFlow(
            new GoogleAuthorizationCodeFlow
                .Initializer
            {
                ClientSecrets =
                    new ClientSecrets
                    {
                        ClientId =
                            _settings.ClientId,

                        ClientSecret =
                            _settings.ClientSecret
                    },

                Scopes =
                    Scopes
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
        var tokenRow =
            await db.GoogleCalendarTokens
                .FirstOrDefaultAsync(
                    item =>
                        item.SupervisorId ==
                            supervisorId,
                    cancellationToken);

        if (tokenRow == null ||
            string.IsNullOrWhiteSpace(
                tokenRow.RefreshToken))
        {
            throw new InvalidOperationException(
                "Google Calendar is not connected.");
        }

        var tokenResponse =
            new TokenResponse
            {
                AccessToken =
                    tokenRow.AccessToken,

                RefreshToken =
                    tokenRow.RefreshToken,

                IssuedUtc =
                    DateTime.UtcNow,

                ExpiresInSeconds =
                    Math.Max(
                        0,
                        (long)(
                            tokenRow.Expiration -
                            DateTime.UtcNow
                        ).TotalSeconds)
            };

        var credential =
            new UserCredential(
                CreateFlow(),
                $"supervisor-{supervisorId}",
                tokenResponse);

        if (credential.Token.IsExpired(
                Google.Apis.Util.SystemClock
                    .Default))
        {
            await credential.RefreshTokenAsync(
                cancellationToken);
        }

        var calendar =
            new CalendarService(
                new BaseClientService
                    .Initializer
                {
                    HttpClientInitializer =
                        credential,

                    ApplicationName =
                        "FYPilot"
                });

        return (
            calendar,
            credential,
            tokenRow);
    }

    private async Task SaveRefreshedTokenAsync(
        GoogleCalendarToken row,
        TokenResponse token,
        CancellationToken cancellationToken)
    {
        row.AccessToken =
            token.AccessToken
            ?? row.AccessToken;

        if (!string.IsNullOrWhiteSpace(
                token.RefreshToken))
        {
            row.RefreshToken =
                token.RefreshToken;
        }

        row.Expiration =
            CalculateExpiration(token);

        row.UpdatedAt =
            DateTime.UtcNow;

        await db.SaveChangesAsync(
            cancellationToken);
    }

    private static DateTime CalculateExpiration(
        TokenResponse token)
    {
        var issued =
            token.IssuedUtc == default
                ? DateTime.UtcNow
                : token.IssuedUtc;

        return issued.AddSeconds(
            token.ExpiresInSeconds
            ?? 3600);
    }

    private static Event BuildGoogleEvent(
        Meeting meeting,
        IReadOnlyCollection<string> attendeeEmails,
        bool requestMeetLink)
    {
        var startUtc =
            DateTime.SpecifyKind(
                meeting.ScheduledAt,
                DateTimeKind.Utc);

        var endUtc =
            startUtc.AddMinutes(
                meeting.DurationMinutes);

        var googleEvent =
            new Event
            {
                Summary =
                    meeting.Title,

                Description =
                    BuildDescription(meeting),

                Location =
                    string.Equals(
                        meeting.MeetingMode,
                        "in_person",
                        StringComparison
                            .OrdinalIgnoreCase)
                        ? meeting.LocationOrLink
                        : null,

                Start =
                    new EventDateTime
                    {
                        DateTimeDateTimeOffset =
                            new DateTimeOffset(
                                startUtc),

                        TimeZone =
                            "UTC"
                    },

                End =
                    new EventDateTime
                    {
                        DateTimeDateTimeOffset =
                            new DateTimeOffset(
                                endUtc),

                        TimeZone =
                            "UTC"
                    },

                Attendees =
                    attendeeEmails
                        .Select(email =>
                            new EventAttendee
                            {
                                Email =
                                    email
                            })
                        .ToList(),

                Reminders =
                    new Event.RemindersData
                    {
                        UseDefault =
                            false,

                        Overrides =
                        [
                            new EventReminder
                            {
                                Method =
                                    "email",

                                Minutes =
                                    24 * 60
                            },

                            new EventReminder
                            {
                                Method =
                                    "popup",

                                Minutes =
                                    30
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
                            RequestId =
                                $"fypilot-{meeting.Id}-"
                                + $"{Guid.NewGuid():N}",

                            ConferenceSolutionKey =
                                new ConferenceSolutionKey
                                {
                                    Type =
                                        "hangoutsMeet"
                                }
                        }
                };
        }

        return googleEvent;
    }

    private static List<string>
        NormalizeAttendeeEmails(
            IReadOnlyCollection<string>? attendeeEmails)
    {
        if (attendeeEmails == null ||
            attendeeEmails.Count == 0)
        {
            return [];
        }

        return attendeeEmails
            .Where(email =>
                !string.IsNullOrWhiteSpace(
                    email))
            .Select(email =>
                email.Trim())
            .Where(IsValidEmail)
            .Distinct(
                StringComparer.OrdinalIgnoreCase)
            .OrderBy(email =>
                email,
                StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private static bool IsValidEmail(
        string email)
    {
        return MailAddress.TryCreate(
            email,
            out _);
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
        if (!string.IsNullOrWhiteSpace(
                googleEvent.HangoutLink))
        {
            return googleEvent.HangoutLink;
        }

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
        const int maximumAttempts =
            15;

        var delay =
            TimeSpan.FromSeconds(1);

        Event? currentEvent =
            null;

        for (var attempt = 1;
             attempt <= maximumAttempts;
             attempt++)
        {
            currentEvent =
                await calendarService.Events
                    .Get(
                        calendarId,
                        googleEventId)
                    .ExecuteAsync(
                        cancellationToken);

            var meetLink =
                GetMeetLink(
                    currentEvent);

            if (!string.IsNullOrWhiteSpace(
                    meetLink))
            {
                return currentEvent;
            }

            var status =
                currentEvent.ConferenceData?
                    .CreateRequest?
                    .Status?
                    .StatusCode;

            if (string.Equals(
                    status,
                    "failure",
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(
                    "Google Calendar created the event, "
                    + "but Google Meet generation failed.");
            }

            await Task.Delay(
                delay,
                cancellationToken);
        }

        throw new InvalidOperationException(
            "Google Calendar created or updated the "
            + "event, but did not return the Google "
            + "Meet link yet. Google event ID: "
            + googleEventId);
    }

    private static string BuildDescription(
        Meeting meeting)
    {
        return $"""
        FYPilot Final Year Project Meeting

        Project ID:
        {meeting.ProjectId?.ToString() ?? "Not available"}

        Agenda:
        {meeting.Agenda}

        Notes to prepare:
        {meeting.NotesToPrepare}

        Mode:
        {meeting.MeetingMode}
        """;
    }
}