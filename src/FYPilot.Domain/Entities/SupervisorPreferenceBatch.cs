using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("supervisor_preference_batches")]
public class SupervisorPreferenceBatch
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("student_id")]
    public int StudentId { get; set; }

    [Column("status")]
    public string Status { get; set; } = "submitted";
    // submitted, assigned, cancelled

    [Column("submitted_at")]
    public DateTime SubmittedAt { get; set; } = DateTime.UtcNow;

    [Column("assigned_at")]
    public DateTime? AssignedAt { get; set; }

    [ForeignKey(nameof(StudentId))]
    public User? Student { get; set; }

    public ICollection<SupervisorPreference> Preferences { get; set; } = [];
}