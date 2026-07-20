namespace FYPilot.Application.DTOs;

/// <summary>
/// One finding from the Python review pipeline's semantic Reviewer Agent
/// (app/review/reviewer_agent.py). Mirrors app.review.models.ReviewerIssue.
/// </summary>
public record ReviewerIssueDto(
    string Severity,
    bool RequiresCorrection,
    string Category,
    string AffectedField,
    string Description,
    string RevisionInstruction
);

/// <summary>
/// The deterministic ReviewDecisionEngine's verdict for one candidate
/// (app.review.models.RewriteDecision). Safe to expose in full — contains no
/// secrets, prompts, or raw candidate text, only the Reviewer's own
/// structured issue list.
/// </summary>
public record RewriteDecisionDto(
    bool RequiresRewrite,
    string Reason,
    List<ReviewerIssueDto> BlockingIssues,
    string? HighestBlockingSeverity
);

/// <summary>
/// One audit-trail entry for a single Writer/Rewrite attempt
/// (app.review.models.AttemptRecord). Deliberately carries only a hash of
/// the candidate output (OutputHash), never the candidate text itself, and
/// only rule/category names for firewall findings, never raw matched
/// secrets or complete prompts.
/// </summary>
public record AttemptRecordDto(
    int AttemptNumber,
    string Stage,
    string OutputHash,
    bool FirewallPassed,
    List<string> FirewallFlags,
    bool SchemaValid,
    bool Reviewed,
    RewriteDecisionDto? Decision,
    string? GeneratorProvider,
    string? GeneratorModel,
    string? ReviewerProvider,
    string? ReviewerModel,
    bool Kept,
    DateTime? CreatedAt
);

/// <summary>
/// The AI Quality Passport — the full result of a ReviewPipeline run
/// (app/review/pipeline.py), returned alongside the answer by /fyp-chat and
/// persisted to AiOutputReview. Status is one of: approved,
/// approved_with_minor_warnings, unresolved, rejected, firewall_blocked,
/// review_unavailable, provider_unavailable, schema_invalid.
///
/// QualityScore is display/audit information only — it is never what
/// decided whether the answer needed a rewrite; see DecisionReason for that.
///
/// ReviewerProvider/ReviewerModel, FirewallInputFlags/FirewallOutputFlags,
/// and AttemptHistory are additive, nullable/defaulted properties added
/// after live verification found the audit trail was incomplete without
/// them — existing responses that predate these fields still deserialize
/// successfully, they simply come through as null/empty.
/// </summary>
public record AiQualityPassportDto(
    string Status,
    bool Usable,
    bool ReviewUnavailable,
    string Warning,
    int? QualityScore,
    List<string> Strengths,
    List<ReviewerIssueDto> Issues,
    string DecisionReason,
    int Attempts,
    string ReviewerVersion,
    string ReviewRunId,
    string? ReviewerProvider = null,
    string? ReviewerModel = null,
    List<string>? FirewallInputFlags = null,
    List<string>? FirewallOutputFlags = null,
    List<AttemptRecordDto>? AttemptHistory = null
);
