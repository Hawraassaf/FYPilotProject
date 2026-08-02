using System.Text.Json;
using FYPilot.Application.Common;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using Microsoft.Extensions.DependencyInjection;

namespace FYPilot.Web.Services.AiAgentJobs;

/// <summary>
/// Owns every AiAgentJob from "Python's worker finished" through "result
/// mirrored, persisted, and marked complete" -- independently of any
/// connected browser, tab lifetime, or client-side storage. This is the
/// core guarantee of the centralized AI Agent Loading System: closing the
/// browser mid-run must never leave a job stuck, and a completed job must
/// never be duplicated if this process crashes and restarts mid-flight.
///
/// Runs a single tick loop (not one long-lived Task per job) -- every
/// PollInterval, it re-reads every active job from Postgres and processes
/// each one based on its current status. This keeps job lifecycle logic in
/// one place and avoids per-job Task bookkeeping/leak bugs, at the cost of
/// polling Python for live state (rather than holding a persistent
/// server-to-server SSE subscription) -- a deliberate simplification noted
/// in the final report; every durability/correctness guarantee here is
/// identical either way, only update latency differs.
/// </summary>
public class AiAgentJobCoordinator(
    IServiceScopeFactory scopeFactory,
    IAiAgentJobEventBus eventBus,
    ILogger<AiAgentJobCoordinator> logger) : BackgroundService
{
    private readonly Guid _coordinatorOwnerId = Guid.NewGuid();

    private static readonly TimeSpan TickInterval = TimeSpan.FromMilliseconds(750);
    private static readonly TimeSpan LeaseDuration = TimeSpan.FromSeconds(15);

    /// <summary>How long a Queued job may sit with PythonAcceptedAtUtc==null before the coordinator assumes the original Start handler's call to Python was lost and retries it itself.</summary>
    private static readonly TimeSpan PythonAcceptGracePeriod = TimeSpan.FromSeconds(10);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        logger.LogInformation("AiAgentJobCoordinator starting (owner {OwnerId}).", _coordinatorOwnerId);

        // Startup recovery pass: reuses the exact same per-job processing
        // as steady-state ticking, just triggered once immediately so any
        // job left mid-flight by a previous crashed process is picked back
        // up right away rather than waiting for the first tick.
        await RunPassAsync(stoppingToken);

        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await Task.Delay(TickInterval, stoppingToken);
                await RunPassAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "AiAgentJobCoordinator pass failed unexpectedly; recovering next tick.");
            }
        }

        logger.LogInformation("AiAgentJobCoordinator stopping.");
    }

    private async Task RunPassAsync(CancellationToken ct)
    {
        using var scope = scopeFactory.CreateScope();
        var jobService = scope.ServiceProvider.GetRequiredService<IAiAgentJobService>();

        IReadOnlyList<AiAgentJob> activeJobs;
        try
        {
            activeJobs = await jobService.GetActiveJobsAsync(ct);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to load active jobs; skipping this pass.");
            return;
        }

        foreach (var job in activeJobs)
        {
            try
            {
                await ProcessJobAsync(job, ct);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "Processing job {JobId} ({AgentName}) failed; will retry next pass.", job.JobId, job.AgentName);
            }
        }
    }

    private async Task ProcessJobAsync(AiAgentJob job, CancellationToken ct)
    {
        using var scope = scopeFactory.CreateScope();
        var services = scope.ServiceProvider;
        var jobService = services.GetRequiredService<IAiAgentJobService>();

        if (!await jobService.TryClaimOrRenewLeaseAsync(job.JobId, _coordinatorOwnerId, LeaseDuration, ct))
        {
            // Another coordinator instance currently holds a live lease on
            // this job (only relevant once/if scaled to multiple FYPilot.Web
            // instances -- see the single-instance deployment note in the
            // final report). Skip it this tick.
            return;
        }

        if (job.Status == AiAgentJobStatus.Queued && job.PythonAcceptedAtUtc is null)
        {
            if (DateTime.UtcNow - job.CreatedAtUtc >= PythonAcceptGracePeriod)
            {
                await TryStartOnPythonAsync(job, services, ct);
            }

            return;
        }

        if (job.Status is AiAgentJobStatus.Queued or AiAgentJobStatus.Running or AiAgentJobStatus.CancelRequested)
        {
            await PollAndMirrorAsync(job, services, ct);
            return;
        }

        if (job.Status == AiAgentJobStatus.AwaitingFinalize)
        {
            await ClaimAndFinalizeAsync(job.JobId, services, ct);
            return;
        }

        if (job.Status == AiAgentJobStatus.Finalizing)
        {
            await ResumeFinalizingAsync(job, services, ct);
        }
    }

    /// <summary>
    /// Retries a start call that a page's own Start handler already
    /// attempted but whose outcome was never recorded (e.g. this process
    /// crashed between inserting the AiAgentJob row and hearing back from
    /// Python). Safe to call repeatedly -- Python's start endpoint is
    /// idempotent per jobId (see app/routers/ai_jobs.py).
    /// </summary>
    private async Task TryStartOnPythonAsync(AiAgentJob job, IServiceProvider services, CancellationToken ct)
    {
        var jobService = services.GetRequiredService<IAiAgentJobService>();
        var pythonClient = services.GetRequiredService<IAiJobsPythonClient>();

        var result = await pythonClient.StartJobAsync(job.AgentName, job.JobId, job.RequestJson, ct);

        if (result.Accepted)
        {
            await jobService.MarkPythonAcceptedAsync(job.JobId, ct);
            logger.LogInformation("Python accepted job {JobId} ({AgentName}) via coordinator recovery retry.", job.JobId, job.AgentName);
        }
        else
        {
            // Deliberately does not fail the job after one attempt -- this
            // path only runs for jobs whose original Start handler call was
            // lost, where Python being briefly unreachable should be
            // retried indefinitely (every tick), not given up on.
            logger.LogWarning("Python did not accept job {JobId} ({AgentName}) on retry: {Error}. Will retry next pass.",
                job.JobId, job.AgentName, result.ErrorMessage);
        }
    }

    private async Task PollAndMirrorAsync(AiAgentJob job, IServiceProvider services, CancellationToken ct)
    {
        if (job.PythonAcceptedAtUtc is null)
        {
            return; // nothing to poll yet
        }

        var jobService = services.GetRequiredService<IAiAgentJobService>();
        var pythonClient = services.GetRequiredService<IAiJobsPythonClient>();

        var snapshot = await pythonClient.GetSnapshotAsync(job.JobId, ct);
        var cancelling = job.Status == AiAgentJobStatus.CancelRequested;

        if (!snapshot.Found)
        {
            await jobService.FailJobAsync(job.JobId, "ai_service_lost_job_state",
                "The AI service restarted before this request finished. Please try again.", ct);
            await PublishTerminalAsync(job.JobId, "job_failed", jobService, ct);
            return;
        }

        // Mirror this snapshot's stage_states BEFORE handling a terminal
        // worker status -- Python's final "done"/"failed" snapshot already
        // reflects every stage it actually completed (e.g. review,
        // rewrite), and skipping this step would freeze the displayed
        // circles at whatever the second-to-last poll happened to see,
        // showing a stage as still "current"/"upcoming" even though it
        // honestly did finish. Harmless to do unconditionally: it's the
        // same mirror the "queued"/"running" branch already does.
        await MirrorAndPublishAsync(job, snapshot, jobService, ct);

        switch (snapshot.WorkerStatus)
        {
            case "done":
                if (cancelling)
                {
                    // Cancellation always wins over a late success -- a
                    // result that happened to finish right as cancellation
                    // landed is discarded, never finalized.
                    await jobService.MarkCancelledAsync(job.JobId, ct);
                    await PublishTerminalAsync(job.JobId, "job_cancelled", jobService, ct);
                }
                else
                {
                    var resultJson = await pythonClient.GetResultJsonAsync(job.JobId, ct) ?? "{}";
                    await jobService.MarkAwaitingFinalizeAsync(job.JobId, resultJson, ct);
                    await ClaimAndFinalizeAsync(job.JobId, services, ct); // don't wait for the next tick
                }
                break;

            case "failed":
                if (cancelling)
                {
                    await jobService.MarkCancelledAsync(job.JobId, ct);
                    await PublishTerminalAsync(job.JobId, "job_cancelled", jobService, ct);
                }
                else
                {
                    var errorMessage = string.IsNullOrWhiteSpace(snapshot.ErrorMessage)
                        ? "The AI provider could not generate a result."
                        : snapshot.ErrorMessage;

                    await jobService.FailJobAsync(job.JobId, "provider_failure", errorMessage, ct);
                    await PublishTerminalAsync(job.JobId, "job_failed", jobService, ct);
                }
                break;

            case "cancelled":
                await jobService.MarkCancelledAsync(job.JobId, ct);
                await PublishTerminalAsync(job.JobId, "job_cancelled", jobService, ct);
                break;

            default:
                // "queued" or "running" -- still in flight; already
                // mirrored above, nothing further to do this tick.
                break;
        }
    }

    private async Task MirrorAndPublishAsync(AiAgentJob job, PythonJobSnapshot snapshot, IAiAgentJobService jobService, CancellationToken ct)
    {
        var fallbackOccurred = snapshot.ProviderAttemptNumber > job.ProviderAttemptNumber;

        var attemptsJson = fallbackOccurred
            ? AppendAttemptHistory(job)
            : job.ProviderAttemptsJson;

        var sequence = await jobService.MirrorLiveStateAsync(
            job.JobId,
            snapshot.StageStatesJson,
            snapshot.StageKey,
            snapshot.Message,
            snapshot.Provider,
            snapshot.Model,
            snapshot.CurrentAttemptChunkCount,
            snapshot.CurrentAttemptTokenCount,
            snapshot.ProviderAttemptNumber,
            attemptsJson,
            ct);

        eventBus.Publish(job.JobId, new AiAgentJobEventDto(
            Sequence: sequence,
            Type: "progress",
            Status: job.Status == AiAgentJobStatus.Queued ? AiAgentJobStatus.Running : job.Status,
            StageKey: snapshot.StageKey,
            StageStates: ParseStageStates(snapshot.StageStatesJson),
            Message: snapshot.Message,
            Provider: snapshot.Provider,
            Model: snapshot.Model,
            CurrentAttemptChunkCount: snapshot.CurrentAttemptChunkCount,
            CurrentAttemptTokenCount: snapshot.CurrentAttemptTokenCount,
            ErrorCode: null,
            ErrorMessage: null));
    }

    /// <summary>Records the attempt that just ended (the job row's state BEFORE this newer snapshot overwrote it) into the audit trail.</summary>
    private static string AppendAttemptHistory(AiAgentJob job)
    {
        List<Dictionary<string, object?>> history;

        try
        {
            history = JsonSerializer.Deserialize<List<Dictionary<string, object?>>>(job.ProviderAttemptsJson) ?? [];
        }
        catch (JsonException)
        {
            history = [];
        }

        history.Add(new Dictionary<string, object?>
        {
            ["attemptNumber"] = job.ProviderAttemptNumber,
            ["provider"] = job.Provider,
            ["model"] = job.Model,
            ["chunkCount"] = job.CurrentAttemptChunkCount,
            ["tokenCount"] = job.CurrentAttemptTokenCount,
            ["endedAtUtc"] = DateTime.UtcNow,
        });

        return JsonSerializer.Serialize(history);
    }

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

    /// <summary>Claims the awaiting_finalize -> finalizing transition (atomic; a no-op if already claimed/completed elsewhere) and, only on success, runs the finalizer.</summary>
    private async Task ClaimAndFinalizeAsync(Guid jobId, IServiceProvider services, CancellationToken ct)
    {
        var jobService = services.GetRequiredService<IAiAgentJobService>();

        var claim = await jobService.TryClaimFinalizationAsync(jobId, ct);
        if (claim != FinalizeClaim.Claimed)
        {
            return;
        }

        var job = await jobService.GetByIdAsync(jobId, ct);
        if (job is null)
        {
            return;
        }

        await RunFinalizerAndCompleteAsync(job, services, ct);
    }

    /// <summary>
    /// Resumes a job a previous coordinator instance already claimed
    /// (Status=="finalizing") before crashing. If ResultPersistedAtUtc is
    /// already set, the output was already committed -- finish the state
    /// transition without re-running the finalizer. Otherwise re-run it
    /// (safe: every finalizer's parent-level idempotency check makes this
    /// a no-op if it turns out output WAS written but the marker wasn't).
    /// </summary>
    private async Task ResumeFinalizingAsync(AiAgentJob job, IServiceProvider services, CancellationToken ct)
    {
        var jobService = services.GetRequiredService<IAiAgentJobService>();

        if (job.ResultPersistedAtUtc is not null)
        {
            await jobService.CompleteJobAsync(job.JobId, ct);
            await PublishTerminalAsync(job.JobId, "job_completed", jobService, ct);
            return;
        }

        await RunFinalizerAndCompleteAsync(job, services, ct);
    }

    private async Task RunFinalizerAndCompleteAsync(AiAgentJob job, IServiceProvider services, CancellationToken ct)
    {
        var jobService = services.GetRequiredService<IAiAgentJobService>();

        try
        {
            var finalizer = services.GetRequiredKeyedService<IAiAgentJobFinalizer>(job.AgentName);
            using var resultDocument = JsonDocument.Parse(string.IsNullOrWhiteSpace(job.ResultJson) ? "{}" : job.ResultJson);

            await finalizer.FinalizeAsync(job, resultDocument.RootElement, ct);
            await jobService.CompleteJobAsync(job.JobId, ct);

            await PublishTerminalAsync(job.JobId, "job_completed", jobService, ct);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Finalizer for job {JobId} ({AgentName}) failed.", job.JobId, job.AgentName);
            await jobService.FailJobAsync(job.JobId, "finalization_failed", "Saving the result failed. Please try again.", ct);
            await PublishTerminalAsync(job.JobId, "job_failed", jobService, ct);
        }
    }

    /// <summary>
    /// Re-reads the job after a terminal transition to get its true,
    /// atomically-bumped LastEventSequence (CompleteJobAsync/FailJobAsync/
    /// MarkCancelledAsync all increment it) before publishing -- never
    /// guesses the next sequence value from a possibly-stale in-memory job.
    /// </summary>
    private async Task PublishTerminalAsync(Guid jobId, string type, IAiAgentJobService jobService, CancellationToken ct)
    {
        var job = await jobService.GetByIdAsync(jobId, ct);
        if (job is null)
        {
            return;
        }

        eventBus.Publish(jobId, new AiAgentJobEventDto(
            Sequence: job.LastEventSequence,
            Type: type,
            Status: job.Status,
            StageKey: job.StageKey,
            StageStates: ParseStageStates(job.StageStatesJson),
            Message: job.ErrorMessage ?? job.Message,
            Provider: job.Provider,
            Model: job.Model,
            CurrentAttemptChunkCount: job.CurrentAttemptChunkCount,
            CurrentAttemptTokenCount: job.CurrentAttemptTokenCount,
            ErrorCode: job.ErrorCode,
            ErrorMessage: job.ErrorMessage));
    }
}
