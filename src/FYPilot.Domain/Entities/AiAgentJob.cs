using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

/// <summary>
/// Durable, browser-facing record of one AI agent background job (the
/// centralized AI Agent Loading System). Postgres is the sole source of
/// truth for job state; Python's in-process AgentJobManager only tracks
/// live execution and never owns completion. See
/// AiAgentJobCoordinator (FYPilot.Web) for the background service that
/// drives a job from Python's "worker_done" through persisted completion
/// independently of any connected browser.
///
/// Allowed <see cref="Status"/> values: queued, running, cancel_requested,
/// awaiting_finalize, finalizing, completed, failed, cancelled. There is no
/// numeric progress-percent field by design -- progress is represented only
/// by <see cref="StageStatesJson"/>, an authoritative per-stage state map
/// mirrored verbatim from Python (never inferred from stage order).
/// </summary>
[Table("ai_agent_jobs")]
public class AiAgentJob
{
    [Key][Column("id")] public int Id { get; set; }

    [Column("job_id")] public Guid JobId { get; set; }

    [Column("user_id")] public int UserId { get; set; }

    [Column("project_id")] public int? ProjectId { get; set; }

    [MaxLength(80)]
    [Column("agent_name")]
    public string AgentName { get; set; } = "";

    /// <summary>SHA-256 hex over the normalized, authorized request inputs -- the duplicate-job / relevance key.</summary>
    [MaxLength(64)]
    [Column("request_hash")]
    public string RequestHash { get; set; } = "";

    /// <summary>
    /// Normalized, non-sensitive request payload (e.g. sorted authorized idea
    /// ids + project id). Never contains provider keys, raw prompts, or
    /// unnecessary personal data. Lets AiAgentJobCoordinator safely retry a
    /// never-accepted Python start call without a browser present.
    /// </summary>
    [Column("request_json")]
    public string RequestJson { get; set; } = "{}";

    [MaxLength(20)]
    [Column("status")]
    public string Status { get; set; } = "queued";

    [MaxLength(60)]
    [Column("stage_key")]
    public string StageKey { get; set; } = "queued";

    /// <summary>
    /// Authoritative JSON map of every stage key for this agent to its state
    /// (upcoming|current|completed|skipped|failed|cancelled), mirrored
    /// verbatim from Python's AgentJobManager. Never inferred/derived here.
    /// </summary>
    [Column("stage_states_json")]
    public string StageStatesJson { get; set; } = "{}";

    [MaxLength(300)]
    [Column("message")]
    public string Message { get; set; } = "";

    [MaxLength(60)]
    [Column("provider")]
    public string? Provider { get; set; }

    [MaxLength(120)]
    [Column("model")]
    public string? Model { get; set; }

    [Column("provider_attempt_number")] public int ProviderAttemptNumber { get; set; }

    /// <summary>Chunk count for the CURRENT provider attempt only -- reset to 0 on fallback_started, never combined across providers.</summary>
    [Column("current_attempt_chunk_count")] public int CurrentAttemptChunkCount { get; set; }

    /// <summary>Token count for the CURRENT provider attempt only, from real provider usage metadata -- never estimated. Reset to null on fallback_started.</summary>
    [Column("current_attempt_token_count")] public int? CurrentAttemptTokenCount { get; set; }

    /// <summary>Audit history of every provider attempt: [{attemptNumber, provider, model, chunkCount, tokenCount, outcome, startedAtUtc, endedAtUtc}, ...].</summary>
    [Column("provider_attempts_json")] public string ProviderAttemptsJson { get; set; } = "[]";

    /// <summary>Monotonically increasing, atomically assigned per-job sequence for browser-facing SSE events (see AiAgentJobService.MirrorLiveStateAsync).</summary>
    [Column("last_event_sequence")] public long LastEventSequence { get; set; }

    /// <summary>Full raw result mirrored from Python before finalization -- protects a finished generation from being lost if Python restarts.</summary>
    [Column("result_json")] public string? ResultJson { get; set; }

    /// <summary>
    /// Set atomically in the same transaction that persists an agent's
    /// domain output rows. The crash-recovery signal: if the coordinator
    /// dies between persisting and calling CompleteJobAsync, this field lets
    /// a restarted coordinator know persistence already succeeded and it
    /// must not re-run the finalizer.
    /// </summary>
    [Column("result_persisted_at_utc")] public DateTime? ResultPersistedAtUtc { get; set; }

    /// <summary>Set once Python's POST /ai-jobs/{agent-name} actually returns 202 for this job -- distinguishes "row exists" from "Python has it".</summary>
    [Column("python_accepted_at_utc")] public DateTime? PythonAcceptedAtUtc { get; set; }

    /// <summary>Coordinator instance currently leasing this job (multi-instance safety net; single-instance deployment today -- see AiAgentJobCoordinator).</summary>
    [Column("coordinator_owner_id")] public Guid? CoordinatorOwnerId { get; set; }

    [Column("coordinator_lease_expires_at_utc")] public DateTime? CoordinatorLeaseExpiresAtUtc { get; set; }

    [MaxLength(60)]
    [Column("error_code")]
    public string? ErrorCode { get; set; }

    [MaxLength(500)]
    [Column("error_message")]
    public string? ErrorMessage { get; set; }

    [Column("created_at_utc")] public DateTime CreatedAtUtc { get; set; } = DateTime.UtcNow;
    [Column("started_at_utc")] public DateTime? StartedAtUtc { get; set; }
    [Column("updated_at_utc")] public DateTime UpdatedAtUtc { get; set; } = DateTime.UtcNow;
    [Column("completed_at_utc")] public DateTime? CompletedAtUtc { get; set; }

    // Optimistic concurrency uses Postgres's built-in xmin system column
    // (see ApplicationDbContext.OnModelCreating -- UseXminAsConcurrencyToken)
    // rather than an explicit version column -- Npgsql provisions the
    // shadow property automatically.

    [ForeignKey(nameof(UserId))] public User? User { get; set; }
    [ForeignKey(nameof(ProjectId))] public Project? Project { get; set; }
}
