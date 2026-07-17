using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("supervisor_assignments")]
public class SupervisorAssignment
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("student_id")]
    public int StudentId { get; set; }

    [Column("supervisor_id")]
    public int SupervisorId { get; set; }

    [Column("assigned_by_admin_id")]
    public int? AssignedByAdminId { get; set; }

    [Column("status")]
    public string Status { get; set; } = "pending_admin";
    // pending_admin, active, rejected, cancelled

    [Column("student_message")]
    public string StudentMessage { get; set; } = "";

    [Column("admin_note")]
    public string AdminNote { get; set; } = "";

    [Column("requested_at")]
    public DateTime RequestedAt { get; set; } = DateTime.UtcNow;

    [Column("approved_at")]
    public DateTime? ApprovedAt { get; set; }

    [Column("rejected_at")]
    public DateTime? RejectedAt { get; set; }

    [Column("updated_at")]
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [ForeignKey(nameof(StudentId))]
    public User? Student { get; set; }

    [ForeignKey(nameof(SupervisorId))]
    public User? Supervisor { get; set; }

    [ForeignKey(nameof(AssignedByAdminId))]
    public User? AssignedByAdmin { get; set; }
}