namespace FYPilot.Application.Interfaces;

/// <summary>
/// Server-to-server transport between AiAgentJobCoordinator and the Python
/// AI service's centralized job endpoints (app/routers/ai_jobs.py). Never
/// called by a browser-facing handler directly -- only the coordinator (and
/// its recovery pass) talks to Python's job lifecycle; the browser only
/// ever talks to .NET's own /api/ai-agent-jobs/* endpoints.
/// </summary>
public interface IAiJobsPythonClient
{
    /// <summary>
    /// POST /ai-jobs/{agent-name}. Python's start endpoint is idempotent
    /// per jobId -- safe to call again if a prior call's response was lost
    /// (see AiAgentJobCoordinator's queued/PythonAcceptedAtUtc==null
    /// recovery case). Deadline/timing policy per agent (e.g. Idea
    /// Comparison's 45s budget) is owned entirely by Python's existing
    /// AGENT_REGISTRY/router config -- not duplicated on the .NET side, so
    /// there is no deadline parameter here.
    /// </summary>
    Task<PythonJobStartResult> StartJobAsync(string agentName, Guid jobId, string requestJson, CancellationToken ct = default);

    /// <summary>GET /ai-jobs/{jobId}. Found=false means Python has no record of this job (e.g. it restarted) -- callers should treat that as ai_service_lost_job_state, not as "still queued".</summary>
    Task<PythonJobSnapshot> GetSnapshotAsync(Guid jobId, CancellationToken ct = default);

    /// <summary>GET /ai-jobs/{jobId}/result. Only meaningful once the snapshot reports the worker is done.</summary>
    Task<string?> GetResultJsonAsync(Guid jobId, CancellationToken ct = default);

    /// <summary>POST /ai-jobs/{jobId}/cancel. Sets Python's cancellation flag; does not abort an in-flight provider call (see AiAgentJobCoordinator §13/ honest cancellation).</summary>
    Task CancelAsync(Guid jobId, CancellationToken ct = default);
}

public sealed record PythonJobStartResult(bool Accepted, string? ErrorMessage);

/// <summary>
/// One agent's live execution snapshot as Python's AgentJobManager
/// currently sees it. WorkerStatus is Python's own internal status
/// ("queued"|"running"|"done"|"failed"|"cancelled" -- distinct from
/// AiAgentJob.Status, which additionally has awaiting_finalize/finalizing/
/// completed states Python has no concept of).
/// </summary>
public sealed record PythonJobSnapshot(
    bool Found,
    string WorkerStatus,
    string StageStatesJson,
    string StageKey,
    string Message,
    string? Provider,
    string? Model,
    int CurrentAttemptChunkCount,
    int? CurrentAttemptTokenCount,
    int ProviderAttemptNumber,
    string? ErrorMessage);
