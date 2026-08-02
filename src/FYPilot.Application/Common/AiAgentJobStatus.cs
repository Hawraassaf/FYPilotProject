namespace FYPilot.Application.Common;

/// <summary>
/// The closed set of AiAgentJob.Status values. Plain strings (matching this
/// codebase's existing status-field convention, e.g. AiOutputReview.Status),
/// centralized here so every reader/writer agrees on the exact literals.
/// There is no numeric percent status -- progress is represented only by
/// AiAgentJob.StageStatesJson.
/// </summary>
public static class AiAgentJobStatus
{
    public const string Queued = "queued";
    public const string Running = "running";
    public const string CancelRequested = "cancel_requested";
    public const string AwaitingFinalize = "awaiting_finalize";
    public const string Finalizing = "finalizing";
    public const string Completed = "completed";
    public const string Failed = "failed";
    public const string Cancelled = "cancelled";

    /// <summary>Statuses that still require watching/mirroring/finalizing -- everything a fresh AiAgentJobCoordinator must resume on startup.</summary>
    public static readonly string[] ActiveStatuses =
    [
        Queued, Running, CancelRequested, AwaitingFinalize, Finalizing
    ];

    /// <summary>Statuses eligible for reuse by StartJobAsync's duplicate-job check (the partial unique index mirrors this exact set).</summary>
    public static readonly string[] ReusableStatuses =
    [
        Queued, Running, CancelRequested, AwaitingFinalize, Finalizing
    ];

    public static bool IsTerminal(string status) =>
        status is Completed or Failed or Cancelled;
}
