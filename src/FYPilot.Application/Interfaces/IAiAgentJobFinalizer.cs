using System.Text.Json;
using FYPilot.Domain.Entities;

namespace FYPilot.Application.Interfaces;

/// <summary>
/// Persists one agent's job output. Registered via keyed DI (one
/// implementation per AgentName, e.g. AddKeyedScoped&lt;IAiAgentJobFinalizer,
/// IdeaComparisonJobFinalizer&gt;("IdeaComparisonAgent")) and invoked only by
/// AiAgentJobCoordinator -- never directly by a browser-facing handler --
/// so job completion never depends on a connected browser.
///
/// Idempotency is applied at the output-PARENT level, not per child row:
/// each implementation identifies the single natural parent row for its
/// agent's output (e.g. AiOutputReview for Idea Comparison, the ProjectIdea
/// batch keyed by GenerationBatchId for Idea Generator) and guards it with
/// a JobId/AiAgentJobId correlation column backed by a unique constraint.
/// FinalizeAsync MUST be safe to call more than once for the same job (a
/// coordinator crash between committing output and calling
/// CompleteJobAsync means a restarted coordinator may re-run it) -- on a
/// repeat call it must detect the already-persisted parent and return
/// without writing again, never throwing and never duplicating rows.
/// </summary>
public interface IAiAgentJobFinalizer
{
    string AgentName { get; }

    /// <summary>
    /// Persists this job's output. Must, within the same SaveChangesAsync
    /// transaction that writes the parent output row, also set
    /// job.ResultPersistedAtUtc -- that field is the crash-recovery signal
    /// AiAgentJobCoordinator relies on to know persistence already
    /// succeeded even if it dies before calling CompleteJobAsync.
    /// </summary>
    Task FinalizeAsync(AiAgentJob job, JsonElement resultPayload, CancellationToken ct);

    /// <summary>
    /// Re-reads the parent (+children) this finalizer already persisted and
    /// shapes a small, page-specific DTO for GET /api/ai-agent-jobs/{jobId}/result.
    /// Only ever called after job.Status == "completed". Never returns the
    /// raw internal ResultJson -- this is the sole place a browser can read
    /// an agent's output.
    /// </summary>
    Task<object> BuildSafeResultViewAsync(AiAgentJob job, CancellationToken ct);
}
