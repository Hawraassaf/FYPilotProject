using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.Common;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Web.Services.AiAgentJobs;
using Microsoft.AspNetCore.Http.Features;

namespace FYPilot.Web.Endpoints;

/// <summary>
/// The shared, agent-agnostic browser-facing surface of the centralized AI
/// Agent Loading System. Every one of these five endpoints is reused by
/// all wired agents -- only the page-specific Start handler
/// (OnPostStart&lt;Agent&gt;JobAsync on each page) is not centralized here,
/// since it needs each page's own authorization/validation logic.
///
/// The browser is a pure observer: nothing here ever finalizes a job --
/// that is AiAgentJobCoordinator's job alone, running independently of
/// whether anyone is watching (see that class's doc comment).
/// </summary>
public static class AiAgentJobEndpoints
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public static void MapAiAgentJobEndpoints(this IEndpointRouteBuilder app)
    {
        var group = app.MapGroup("/api/ai-agent-jobs").RequireAuthorization();

        group.MapGet("/current", GetCurrentAsync);
        group.MapGet("/{jobId:guid}", GetSnapshotAsync);
        group.MapGet("/{jobId:guid}/events", StreamEventsAsync);
        group.MapGet("/{jobId:guid}/result", GetResultAsync);
        group.MapPost("/{jobId:guid}/cancel", CancelAsync);
    }

    private static int GetUserId(ClaimsPrincipal user) =>
        int.Parse(user.FindFirst(ClaimTypes.NameIdentifier)!.Value);

    /// <summary>
    /// GET /api/ai-agent-jobs/current?agentName=&amp;projectId=&amp;requestHash=
    /// The server-side reconnect lookup -- deliberately NOT a fallback to
    /// sessionStorage. requestHash omitted -&gt; active-only (never returns a
    /// completed job without an exact hash match, so a stale unrelated
    /// result is never surfaced -- see AiAgentJobService.FindJobByHashAsync).
    /// </summary>
    private static async Task<IResult> GetCurrentAsync(
        string agentName,
        int? projectId,
        string? requestHash,
        ClaimsPrincipal user,
        IAiAgentJobService jobService,
        CancellationToken ct)
    {
        var userId = GetUserId(user);

        var job = string.IsNullOrWhiteSpace(requestHash)
            ? await jobService.FindActiveJobAsync(userId, projectId, agentName, ct)
            : await jobService.FindJobByHashAsync(userId, projectId, agentName, requestHash, includeRecentlyCompleted: true, ct);

        return job is null ? Results.NoContent() : Results.Ok(ToSnapshotDto(job));
    }

    private static async Task<IResult> GetSnapshotAsync(
        Guid jobId, ClaimsPrincipal user, IAiAgentJobService jobService, CancellationToken ct)
    {
        var job = await jobService.GetAuthorizedJobAsync(jobId, GetUserId(user), ct);
        return job is null ? Results.NotFound() : Results.Ok(ToSnapshotDto(job));
    }

    /// <summary>
    /// GET /api/ai-agent-jobs/{jobId}/result -- the ONLY endpoint that
    /// exposes an agent's actual output to the browser (used by the two
    /// inline-result pages, Mentor Chat and Defense Simulator; reload-style
    /// pages never call this -- their OnGetAsync reconstructs from
    /// ResultJson server-side). Delegates to the same
    /// IAiAgentJobFinalizer.BuildSafeResultViewAsync that persisted the
    /// output, so this can never leak more than what was actually saved.
    /// </summary>
    private static async Task<IResult> GetResultAsync(
        Guid jobId, ClaimsPrincipal user, IAiAgentJobService jobService, IServiceProvider services, CancellationToken ct)
    {
        var job = await jobService.GetAuthorizedJobAsync(jobId, GetUserId(user), ct);
        if (job is null)
        {
            return Results.NotFound();
        }

        if (job.Status != AiAgentJobStatus.Completed)
        {
            return Results.Conflict(new { message = "This job has not completed yet." });
        }

        var finalizer = services.GetKeyedService<IAiAgentJobFinalizer>(job.AgentName);
        if (finalizer is null)
        {
            return Results.NotFound();
        }

        var view = await finalizer.BuildSafeResultViewAsync(job, ct);
        return Results.Ok(view);
    }

    private static async Task<IResult> CancelAsync(
        Guid jobId, ClaimsPrincipal user, IAiAgentJobService jobService, IAiJobsPythonClient pythonClient, CancellationToken ct)
    {
        var userId = GetUserId(user);
        var job = await jobService.GetAuthorizedJobAsync(jobId, userId, ct);
        if (job is null)
        {
            return Results.NotFound();
        }

        // The browser-authorized half of cancellation -- transitions the
        // job to cancel_requested. The eventual cancel_requested ->
        // cancelled transition happens later, observed via this same job's
        // events/snapshot once AiAgentJobCoordinator sees Python's worker
        // stop naturally (see that class's honest-cancellation handling).
        await jobService.RequestCancelAsync(jobId, userId, ct);
        await pythonClient.CancelAsync(jobId, ct);

        return Results.Accepted();
    }

    /// <summary>
    /// GET /api/ai-agent-jobs/{jobId}/events -- SSE. Ownership is checked
    /// BEFORE anything streams. Subscribes to AiAgentJobCoordinator's
    /// in-process event bus purely as an observer -- closing this
    /// connection (or never opening it at all) has zero effect on the job
    /// itself, which the coordinator drives independently.
    /// </summary>
    private static async Task StreamEventsAsync(
        Guid jobId,
        HttpContext httpContext,
        ClaimsPrincipal user,
        IAiAgentJobService jobService,
        IAiAgentJobEventBus eventBus,
        CancellationToken ct)
    {
        var job = await jobService.GetAuthorizedJobAsync(jobId, GetUserId(user), ct);
        if (job is null)
        {
            httpContext.Response.StatusCode = StatusCodes.Status404NotFound;
            return;
        }

        httpContext.Response.Headers.ContentType = "text/event-stream";
        httpContext.Response.Headers.CacheControl = "no-cache";
        httpContext.Response.Headers["X-Accel-Buffering"] = "no"; // hint for reverse proxies that would otherwise buffer the stream
        httpContext.Features.Get<IHttpResponseBodyFeature>()?.DisableBuffering();

        // Immediate snapshot so a fresh/reconnecting subscriber is caught
        // up without waiting for the next real event -- carries the
        // authoritative StageStates map straight from the DB row, so a
        // reconnect after refresh reconstructs every circle (including any
        // already-skipped ones) correctly from this single event.
        await WriteEventAsync(httpContext, ToSseDto(job, "snapshot"), ct);
        await httpContext.Response.Body.FlushAsync(ct);

        if (AiAgentJobStatus.IsTerminal(job.Status))
        {
            return; // already done -- nothing further will ever be published for this job
        }

        var subscriberId = eventBus.Subscribe(jobId, out var reader);
        try
        {
            while (await reader.WaitToReadAsync(ct))
            {
                while (reader.TryRead(out var evt))
                {
                    await WriteEventAsync(httpContext, evt, ct);
                    await httpContext.Response.Body.FlushAsync(ct);

                    if (evt.Type is "job_completed" or "job_failed" or "job_cancelled")
                    {
                        return;
                    }
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Client disconnected -- not an error.
        }
        finally
        {
            eventBus.Unsubscribe(jobId, subscriberId);
        }
    }

    private static async Task WriteEventAsync(HttpContext httpContext, AiAgentJobEventDto evt, CancellationToken ct)
    {
        var json = JsonSerializer.Serialize(evt, JsonOptions);
        await httpContext.Response.WriteAsync($"id: {evt.Sequence}\ndata: {json}\n\n", ct);
    }

    private static object ToSnapshotDto(AiAgentJob job) => new
    {
        jobId = job.JobId,
        status = job.Status,
        stageKey = job.StageKey,
        stageStates = ParseStageStates(job.StageStatesJson),
        message = job.Message,
        provider = job.Provider,
        model = job.Model,
        providerAttemptNumber = job.ProviderAttemptNumber,
        currentAttemptChunkCount = job.CurrentAttemptChunkCount,
        currentAttemptTokenCount = job.CurrentAttemptTokenCount,
        lastEventSequence = job.LastEventSequence,
        errorCode = job.ErrorCode,
        errorMessage = job.ErrorMessage,
        createdAtUtc = job.CreatedAtUtc,
        startedAtUtc = job.StartedAtUtc,
    };

    private static AiAgentJobEventDto ToSseDto(AiAgentJob job, string type) => new(
        Sequence: job.LastEventSequence,
        Type: type,
        Status: job.Status,
        StageKey: job.StageKey,
        StageStates: ParseStageStates(job.StageStatesJson),
        Message: job.Message,
        Provider: job.Provider,
        Model: job.Model,
        CurrentAttemptChunkCount: job.CurrentAttemptChunkCount,
        CurrentAttemptTokenCount: job.CurrentAttemptTokenCount,
        ErrorCode: job.ErrorCode,
        ErrorMessage: job.ErrorMessage);

    private static Dictionary<string, string> ParseStageStates(string stageStatesJson)
    {
        try
        {
            return JsonSerializer.Deserialize<Dictionary<string, string>>(stageStatesJson) ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }
}
