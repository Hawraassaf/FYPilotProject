using FYPilot.Domain.Entities;

namespace FYPilot.Application.Interfaces;

public enum FinalizeClaim
{
    Claimed,
    AlreadyCompleted,
    AlreadyFinalizing,
    NotReady
}

public sealed record StartJobResult(AiAgentJob Job, bool CreatedNew);

/// <summary>
/// Owns every read/write of AiAgentJob. Every method that mutates or reads
/// on behalf of a browser request takes the caller's userId and re-validates
/// ownership internally -- this is the sole enforcement point for "a job can
/// only be read/mutated by the user who owns it." The "coordinator-only"
/// methods below act with system authority (no userId) and must only ever
/// be called from AiAgentJobCoordinator, never from a browser-facing
/// endpoint.
/// </summary>
public interface IAiAgentJobService
{
    // Browser-facing (userId-authorized)
    Task<StartJobResult> StartJobAsync(int userId, int? projectId, string agentName, string requestHash, string requestJson, CancellationToken ct = default);
    Task<AiAgentJob?> GetAuthorizedJobAsync(Guid jobId, int userId, CancellationToken ct = default);

    /// <summary>The §3(b) relevance-scoped lookup: active-with-hash, then completed-with-hash, never a different hash's job. includeRecentlyCompleted=false restricts to the active branch only.</summary>
    Task<AiAgentJob?> FindJobByHashAsync(int userId, int? projectId, string agentName, string requestHash, bool includeRecentlyCompleted, CancellationToken ct = default);

    /// <summary>Hash-agnostic active-only fallback -- never returns a completed job (see FindJobByHashAsync for the completed-job path, which requires an exact hash match).</summary>
    Task<AiAgentJob?> FindActiveJobAsync(int userId, int? projectId, string agentName, CancellationToken ct = default);

    Task MarkPythonAcceptedAsync(Guid jobId, CancellationToken ct = default);
    Task RequestCancelAsync(Guid jobId, int userId, CancellationToken ct = default);

    // Coordinator-only (system authority)
    Task<AiAgentJob?> GetByIdAsync(Guid jobId, CancellationToken ct = default);
    Task<IReadOnlyList<AiAgentJob>> GetActiveJobsAsync(CancellationToken ct = default);
    Task<bool> TryClaimOrRenewLeaseAsync(Guid jobId, Guid coordinatorOwnerId, TimeSpan leaseDuration, CancellationToken ct = default);

    /// <summary>
    /// Atomically increments and returns the job's LastEventSequence while
    /// mirroring Python's authoritative stage-state map verbatim. Python's
    /// snapshot already scopes currentAttemptChunkCount/currentAttemptTokenCount
    /// to the CURRENT provider attempt (it resets them itself on
    /// fallback_started), so this just writes the incoming values directly
    /// -- callers (AiAgentJobCoordinator) are responsible for computing
    /// providerAttemptsJson's audit-trail append themselves, by comparing
    /// the previous job row's provider/attempt-number against the new
    /// snapshot, before calling this method.
    /// </summary>
    Task<long> MirrorLiveStateAsync(Guid jobId, string stageStatesJson, string stageKey, string message, string? provider, string? model,
        int currentAttemptChunkCount, int? currentAttemptTokenCount, int providerAttemptNumber, string providerAttemptsJson, CancellationToken ct = default);

    Task MarkAwaitingFinalizeAsync(Guid jobId, string resultJson, CancellationToken ct = default);
    Task<FinalizeClaim> TryClaimFinalizationAsync(Guid jobId, CancellationToken ct = default);
    Task CompleteJobAsync(Guid jobId, CancellationToken ct = default);
    Task FailJobAsync(Guid jobId, string errorCode, string errorMessage, CancellationToken ct = default);
    Task MarkCancelledAsync(Guid jobId, CancellationToken ct = default);
}
