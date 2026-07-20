using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

/// <summary>
/// Persisted audit record for one AI Output Review Pipeline run
/// (services/FYPilot.AI/app/review/pipeline.py). One row per /fyp-chat
/// request handled by ReviewPipeline (the Mentor Chat pilot) — trivial
/// short-circuit exchanges (greetings, empty messages) are not persisted
/// here since they never reach the pipeline.
///
/// Never stores: API keys, system prompts, connection strings, auth tokens,
/// or full private prompts — only structured, already-redacted review
/// metadata (see the Python pipeline's redaction rules).
/// </summary>
[Table("ai_output_reviews")]
public class AiOutputReview
{
    [Key][Column("id")] public int Id { get; set; }

    [Column("review_run_id")] public Guid ReviewRunId { get; set; }

    [Column("user_id")] public int UserId { get; set; }

    [Column("project_idea_id")] public int? ProjectIdeaId { get; set; }

    [Column("mentor_chat_session_id")] public int? MentorChatSessionId { get; set; }

    [MaxLength(80)]
    [Column("agent_name")]
    public string AgentName { get; set; } = "";

    [MaxLength(40)]
    [Column("status")]
    public string Status { get; set; } = "";

    [Column("usable")] public bool Usable { get; set; }

    [Column("was_rewritten")] public bool WasRewritten { get; set; }

    [Column("attempts")] public int Attempts { get; set; }

    [Column("quality_score")] public int? QualityScore { get; set; }

    [Column("decision_reason")] public string DecisionReason { get; set; } = "";

    [MaxLength(60)]
    [Column("generator_provider")]
    public string? GeneratorProvider { get; set; }

    [MaxLength(120)]
    [Column("generator_model")]
    public string? GeneratorModel { get; set; }

    [MaxLength(60)]
    [Column("reviewer_provider")]
    public string? ReviewerProvider { get; set; }

    [MaxLength(120)]
    [Column("reviewer_model")]
    public string? ReviewerModel { get; set; }

    [MaxLength(40)]
    [Column("firewall_status")]
    public string FirewallStatus { get; set; } = "passed";

    [Column("firewall_input_flags_json")] public string FirewallInputFlagsJson { get; set; } = "[]";
    [Column("firewall_output_flags_json")] public string FirewallOutputFlagsJson { get; set; } = "[]";

    [Column("issues_json")] public string IssuesJson { get; set; } = "[]";
    [Column("strengths_json")] public string StrengthsJson { get; set; } = "[]";
    [Column("attempt_history_json")] public string AttemptHistoryJson { get; set; } = "[]";

    [MaxLength(40)]
    [Column("reviewer_version")]
    public string ReviewerVersion { get; set; } = "";

    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [Column("completed_at")] public DateTime? CompletedAt { get; set; }

    [ForeignKey(nameof(UserId))] public User? User { get; set; }
    [ForeignKey(nameof(MentorChatSessionId))] public MentorChatSession? MentorChatSession { get; set; }
}
