using System.Text.Json;
using FYPilot.Application.Common;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using Npgsql;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// EF Core-backed implementation of IAiAgentJobService. See that interface
/// for the userId-authorization contract every browser-facing method must
/// honor, and AiAgentJobCoordinator for the only caller of the
/// coordinator-only methods.
/// </summary>
public class AiAgentJobService(ApplicationDbContext db) : IAiAgentJobService
{
    private static readonly TimeSpan RecentlyCompletedWindow = TimeSpan.FromHours(24);

    public async Task<StartJobResult> StartJobAsync(int userId, int? projectId, string agentName, string requestHash, string requestJson, CancellationToken ct = default)
    {
        var existing = await FindReusableJobAsync(userId, projectId, agentName, requestHash, ct);
        if (existing is not null)
        {
            return new StartJobResult(existing, CreatedNew: false);
        }

        var job = new AiAgentJob
        {
            JobId = Guid.NewGuid(),
            UserId = userId,
            ProjectId = projectId,
            AgentName = agentName,
            RequestHash = requestHash,
            RequestJson = requestJson,
            Status = AiAgentJobStatus.Queued,
            StageKey = "queued",
            StageStatesJson = "{}",
            Message = "Queued",
            CreatedAtUtc = DateTime.UtcNow,
            UpdatedAtUtc = DateTime.UtcNow,
        };

        db.AiAgentJobs.Add(job);

        try
        {
            await db.SaveChangesAsync(ct);
            return new StartJobResult(job, CreatedNew: true);
        }
        catch (DbUpdateException ex) when (ex.InnerException is PostgresException { SqlState: "23505" })
        {
            // The partial unique index fired -- another concurrent request
            // just created the matching active job. Detach our failed
            // insert and return the winner instead of throwing.
            db.Entry(job).State = EntityState.Detached;

            var winner = await FindReusableJobAsync(userId, projectId, agentName, requestHash, ct);
            if (winner is null)
            {
                throw; // genuinely unexpected -- rethrow rather than silently swallow
            }

            return new StartJobResult(winner, CreatedNew: false);
        }
    }

    private Task<AiAgentJob?> FindReusableJobAsync(int userId, int? projectId, string agentName, string requestHash, CancellationToken ct) =>
        db.AiAgentJobs
            .Where(j => j.UserId == userId && j.ProjectId == projectId && j.AgentName == agentName && j.RequestHash == requestHash)
            .Where(j => AiAgentJobStatus.ReusableStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAtUtc)
            .FirstOrDefaultAsync(ct);

    public Task<AiAgentJob?> GetAuthorizedJobAsync(Guid jobId, int userId, CancellationToken ct = default) =>
        db.AiAgentJobs.FirstOrDefaultAsync(j => j.JobId == jobId && j.UserId == userId, ct);

    public async Task<AiAgentJob?> FindJobByHashAsync(int userId, int? projectId, string agentName, string requestHash, bool includeRecentlyCompleted, CancellationToken ct = default)
    {
        var active = await db.AiAgentJobs
            .Where(j => j.UserId == userId && j.ProjectId == projectId && j.AgentName == agentName && j.RequestHash == requestHash)
            .Where(j => AiAgentJobStatus.ActiveStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAtUtc)
            .FirstOrDefaultAsync(ct);

        if (active is not null || !includeRecentlyCompleted)
        {
            return active;
        }

        var cutoff = DateTime.UtcNow - RecentlyCompletedWindow;
        return await db.AiAgentJobs
            .Where(j => j.UserId == userId && j.ProjectId == projectId && j.AgentName == agentName && j.RequestHash == requestHash)
            .Where(j => j.Status == AiAgentJobStatus.Completed && j.CompletedAtUtc >= cutoff)
            .OrderByDescending(j => j.CompletedAtUtc)
            .FirstOrDefaultAsync(ct);
    }

    public Task<AiAgentJob?> FindActiveJobAsync(int userId, int? projectId, string agentName, CancellationToken ct = default) =>
        db.AiAgentJobs
            .Where(j => j.UserId == userId && j.ProjectId == projectId && j.AgentName == agentName)
            .Where(j => AiAgentJobStatus.ActiveStatuses.Contains(j.Status))
            .OrderByDescending(j => j.CreatedAtUtc)
            .FirstOrDefaultAsync(ct);

    public async Task MarkPythonAcceptedAsync(Guid jobId, CancellationToken ct = default)
    {
        await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.PythonAcceptedAtUtc, DateTime.UtcNow)
                .SetProperty(j => j.UpdatedAtUtc, DateTime.UtcNow), ct);
    }

    public async Task RequestCancelAsync(Guid jobId, int userId, CancellationToken ct = default)
    {
        await db.AiAgentJobs
            .Where(j => j.JobId == jobId && j.UserId == userId)
            .Where(j => j.Status == AiAgentJobStatus.Queued || j.Status == AiAgentJobStatus.Running)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.Status, AiAgentJobStatus.CancelRequested)
                .SetProperty(j => j.Message, "Cancellation requested. Finishing the active provider request safely.")
                .SetProperty(j => j.UpdatedAtUtc, DateTime.UtcNow), ct);
    }

    public Task<AiAgentJob?> GetByIdAsync(Guid jobId, CancellationToken ct = default) =>
        db.AiAgentJobs.FirstOrDefaultAsync(j => j.JobId == jobId, ct);

    public async Task<IReadOnlyList<AiAgentJob>> GetActiveJobsAsync(CancellationToken ct = default) =>
        await db.AiAgentJobs
            .Where(j => AiAgentJobStatus.ActiveStatuses.Contains(j.Status))
            .ToListAsync(ct);

    public async Task<bool> TryClaimOrRenewLeaseAsync(Guid jobId, Guid coordinatorOwnerId, TimeSpan leaseDuration, CancellationToken ct = default)
    {
        var now = DateTime.UtcNow;
        var claimed = await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .Where(j => j.CoordinatorOwnerId == null || j.CoordinatorOwnerId == coordinatorOwnerId || j.CoordinatorLeaseExpiresAtUtc < now)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.CoordinatorOwnerId, coordinatorOwnerId)
                .SetProperty(j => j.CoordinatorLeaseExpiresAtUtc, now.Add(leaseDuration)), ct);

        return claimed == 1;
    }

    public async Task<long> MirrorLiveStateAsync(Guid jobId, string stageStatesJson, string stageKey, string message, string? provider, string? model,
        int currentAttemptChunkCount, int? currentAttemptTokenCount, int providerAttemptNumber, string providerAttemptsJson, CancellationToken ct = default)
    {
        // Single atomic UPDATE ... RETURNING so the sequence increment can
        // never be lost or duplicated between two concurrent mirror calls
        // for the same job (defense in depth -- the lease already ensures
        // only one coordinator watches a job, but this makes the increment
        // itself correct regardless).
        const string sql = """
            UPDATE ai_agent_jobs
            SET last_event_sequence = last_event_sequence + 1,
                stage_states_json = @stageStatesJson,
                stage_key = @stageKey,
                message = @message,
                provider = @provider,
                model = @model,
                status = CASE WHEN status = @queued THEN @running ELSE status END,
                started_at_utc = CASE WHEN started_at_utc IS NULL THEN now() AT TIME ZONE 'UTC' ELSE started_at_utc END,
                provider_attempt_number = @providerAttemptNumber,
                current_attempt_chunk_count = @currentAttemptChunkCount,
                current_attempt_token_count = @currentAttemptTokenCount,
                provider_attempts_json = @providerAttemptsJson,
                updated_at_utc = now() AT TIME ZONE 'UTC'
            WHERE job_id = @jobId
            RETURNING last_event_sequence;
            """;

        var parameters = new[]
        {
            new NpgsqlParameter("jobId", jobId),
            new NpgsqlParameter("stageStatesJson", stageStatesJson),
            new NpgsqlParameter("stageKey", stageKey),
            new NpgsqlParameter("message", message),
            new NpgsqlParameter("provider", (object?)provider ?? DBNull.Value),
            new NpgsqlParameter("model", (object?)model ?? DBNull.Value),
            new NpgsqlParameter("queued", AiAgentJobStatus.Queued),
            new NpgsqlParameter("running", AiAgentJobStatus.Running),
            new NpgsqlParameter("providerAttemptNumber", providerAttemptNumber),
            new NpgsqlParameter("currentAttemptChunkCount", currentAttemptChunkCount),
            new NpgsqlParameter("currentAttemptTokenCount", (object?)currentAttemptTokenCount ?? DBNull.Value),
            new NpgsqlParameter("providerAttemptsJson", providerAttemptsJson),
        };

        // NOTE: .SingleAsync()/.FirstAsync() over SqlQueryRaw<T> make EF Core
        // try to COMPOSE the raw SQL as a subquery (wrapping it to apply a
        // row limit) -- which throws InvalidOperationException
        // ("non-composable SQL") for anything that isn't a plain SELECT,
        // including this UPDATE ... RETURNING statement. .ToListAsync()
        // executes the raw command as-is with no further composition,
        // which is what a non-SELECT statement requires. Caught via live
        // browser verification against real Postgres -- an in-memory/fake
        // EF provider would never have exercised this path.
        var results = await db.Database.SqlQueryRaw<long>(sql, parameters).ToListAsync(ct);
        return results[0];
    }

    public async Task MarkAwaitingFinalizeAsync(Guid jobId, string resultJson, CancellationToken ct = default)
    {
        // "save" is .NET's own stage -- Python's worker process has already
        // returned by the time this runs, so nothing on the Python side
        // will ever report it. Every agent's StagePlan ends with a "save"
        // stage by convention (see app/jobs/plans.py); patch just that one
        // key onto whatever StageStatesJson Python last reported, rather
        // than needing this service to know each agent's full stage list.
        //
        // Deliberately AsNoTracking()+ExecuteUpdateAsync rather than
        // load-tracked-entity+SaveChangesAsync: the coordinator's own flow
        // (lease renewal, MirrorLiveStateAsync, TryClaimFinalizationAsync)
        // touches this same row via bulk ExecuteUpdateAsync calls that
        // bypass the change tracker but still bump Postgres's xmin --
        // mixing that with a tracked SaveChangesAsync on the same DbContext
        // produced a real DbUpdateConcurrencyException in testing. Every
        // write in this class now goes through ExecuteUpdateAsync
        // consistently to avoid that class of bug entirely.
        var current = await db.AiAgentJobs
            .AsNoTracking()
            .Where(j => j.JobId == jobId)
            .Select(j => new { j.Status, j.StageStatesJson })
            .FirstOrDefaultAsync(ct);

        if (current is null || current.Status is AiAgentJobStatus.Cancelled or AiAgentJobStatus.CancelRequested)
        {
            return;
        }

        var patchedStageStates = WithStageState(current.StageStatesJson, "save", "current");

        await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.ResultJson, resultJson)
                .SetProperty(j => j.Status, AiAgentJobStatus.AwaitingFinalize)
                .SetProperty(j => j.StageStatesJson, patchedStageStates)
                .SetProperty(j => j.UpdatedAtUtc, DateTime.UtcNow), ct);
    }

    private static string WithStageState(string stageStatesJson, string stageKey, string state)
    {
        Dictionary<string, string> stageStates;
        try
        {
            stageStates = JsonSerializer.Deserialize<Dictionary<string, string>>(stageStatesJson) ?? [];
        }
        catch (JsonException)
        {
            stageStates = [];
        }

        if (stageStates.ContainsKey(stageKey))
        {
            stageStates[stageKey] = state;
        }

        return JsonSerializer.Serialize(stageStates);
    }

    public async Task<FinalizeClaim> TryClaimFinalizationAsync(Guid jobId, CancellationToken ct = default)
    {
        var claimed = await db.AiAgentJobs
            .Where(j => j.JobId == jobId && j.Status == AiAgentJobStatus.AwaitingFinalize)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.Status, AiAgentJobStatus.Finalizing)
                .SetProperty(j => j.UpdatedAtUtc, DateTime.UtcNow), ct);

        if (claimed == 1)
        {
            return FinalizeClaim.Claimed;
        }

        var current = await db.AiAgentJobs.AsNoTracking().FirstOrDefaultAsync(j => j.JobId == jobId, ct);
        return current?.Status switch
        {
            AiAgentJobStatus.Completed => FinalizeClaim.AlreadyCompleted,
            AiAgentJobStatus.Finalizing => FinalizeClaim.AlreadyFinalizing,
            _ => FinalizeClaim.NotReady,
        };
    }

    public async Task CompleteJobAsync(Guid jobId, CancellationToken ct = default)
    {
        // See MarkAwaitingFinalizeAsync's comment -- AsNoTracking read +
        // ExecuteUpdateAsync write only, never a tracked entity, to avoid
        // the xmin/DbUpdateConcurrencyException class of bug entirely.
        var currentStageStates = await db.AiAgentJobs
            .AsNoTracking()
            .Where(j => j.JobId == jobId)
            .Select(j => j.StageStatesJson)
            .FirstOrDefaultAsync(ct);

        if (currentStageStates is null)
        {
            return;
        }

        var now = DateTime.UtcNow;
        var patchedStageStates = WithStageState(currentStageStates, "save", "completed");

        await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.Status, AiAgentJobStatus.Completed)
                .SetProperty(j => j.StageKey, "save")
                .SetProperty(j => j.StageStatesJson, patchedStageStates)
                .SetProperty(j => j.CompletedAtUtc, now)
                .SetProperty(j => j.UpdatedAtUtc, now)
                // Terminal transitions bump the sequence too (not just
                // MirrorLiveStateAsync) so the coordinator can publish a
                // correctly-numbered final SSE event.
                .SetProperty(j => j.LastEventSequence, j => j.LastEventSequence + 1), ct);
    }

    public async Task FailJobAsync(Guid jobId, string errorCode, string errorMessage, CancellationToken ct = default)
    {
        var now = DateTime.UtcNow;
        await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .Where(j => j.Status != AiAgentJobStatus.Completed) // never overwrite a completed job
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.Status, AiAgentJobStatus.Failed)
                .SetProperty(j => j.ErrorCode, errorCode)
                .SetProperty(j => j.ErrorMessage, errorMessage)
                .SetProperty(j => j.CompletedAtUtc, now)
                .SetProperty(j => j.UpdatedAtUtc, now)
                .SetProperty(j => j.LastEventSequence, j => j.LastEventSequence + 1), ct);
    }

    public async Task MarkCancelledAsync(Guid jobId, CancellationToken ct = default)
    {
        var now = DateTime.UtcNow;
        await db.AiAgentJobs
            .Where(j => j.JobId == jobId)
            .Where(j => j.Status != AiAgentJobStatus.Completed)
            .ExecuteUpdateAsync(s => s
                .SetProperty(j => j.Status, AiAgentJobStatus.Cancelled)
                .SetProperty(j => j.ErrorCode, "cancelled_by_user")
                .SetProperty(j => j.CompletedAtUtc, now)
                .SetProperty(j => j.UpdatedAtUtc, now)
                .SetProperty(j => j.LastEventSequence, j => j.LastEventSequence + 1), ct);
    }
}
