using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_dna_analyses")]
public class ProjectDnaAnalysisRecord
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    [Column("project_idea_id")]
    public int ProjectIdeaId { get; set; }

    [Column("generated_by_user_id")]
    public int GeneratedByUserId { get; set; }

    [Column("analysis_json", TypeName = "text")]
    public string AnalysisJson { get; set; } = "{}";

    [Column("llm_used")]
    public bool LlmUsed { get; set; }

    [MaxLength(120)]
    [Column("source")]
    public string? Source { get; set; }

    [MaxLength(120)]
    [Column("provider")]
    public string? Provider { get; set; }

    [MaxLength(200)]
    [Column("model_used")]
    public string? ModelUsed { get; set; }

    [Column("generated_at_utc")]
    public DateTime GeneratedAtUtc { get; set; }
        = DateTime.UtcNow;

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(ProjectIdeaId))]
    public ProjectIdea? ProjectIdea { get; set; }

    [ForeignKey(nameof(GeneratedByUserId))]
    public User? GeneratedByUser { get; set; }
}