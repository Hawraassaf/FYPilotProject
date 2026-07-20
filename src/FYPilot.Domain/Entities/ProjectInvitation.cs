using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_invitations")]
public class ProjectInvitation
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    [Column("invited_by_user_id")]
    public int InvitedByUserId { get; set; }

    [Column("invited_user_id")]
    public int? InvitedUserId { get; set; }

    [Required]
    [EmailAddress]
    [StringLength(256)]
    [Column("invited_email")]
    public string InvitedEmail { get; set; } = "";

    [Required]
    [StringLength(64)]
    [Column("token_hash")]
    public string TokenHash { get; set; } = "";

    [Required]
    [StringLength(30)]
    [Column("status")]
    public string Status { get; set; } = "pending";

    [Required]
    [StringLength(30)]
    [Column("source")]
    public string Source { get; set; } = "student_invite";

    [Column("teammate_request_id")]
    public int? TeammateRequestId { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("expires_at")]
    public DateTime ExpiresAt { get; set; }

    [Column("responded_at")]
    public DateTime? RespondedAt { get; set; }

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(InvitedByUserId))]
    public User? InvitedByUser { get; set; }

    [ForeignKey(nameof(InvitedUserId))]
    public User? InvitedUser { get; set; }

    [ForeignKey(nameof(TeammateRequestId))]
    public TeammateRequest? TeammateRequest { get; set; }
}