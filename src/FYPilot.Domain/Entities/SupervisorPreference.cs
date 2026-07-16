using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("supervisor_preferences")]
public class SupervisorPreference
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("batch_id")]
    public int BatchId { get; set; }

    [Column("student_id")]
    public int StudentId { get; set; }

    [Column("supervisor_id")]
    public int SupervisorId { get; set; }

    [Column("preference_rank")]
    public int PreferenceRank { get; set; }
    // 1, 2, 3

    [Column("match_score")]
    public int MatchScore { get; set; }

    [Column("match_reason")]
    public string MatchReason { get; set; } = "";

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [ForeignKey(nameof(BatchId))]
    public SupervisorPreferenceBatch? Batch { get; set; }

    [ForeignKey(nameof(StudentId))]
    public User? Student { get; set; }

    [ForeignKey(nameof(SupervisorId))]
    public User? Supervisor { get; set; }
}