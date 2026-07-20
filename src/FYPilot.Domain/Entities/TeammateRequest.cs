using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("teammate_requests")]
public class TeammateRequest
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    [Column("requested_by_user_id")]
    public int RequestedByUserId { get; set; }

    [Required]
    [StringLength(200)]
    [Column("domain")]
    public string Domain { get; set; } = "";

    [Column("required_skills")]
    public string RequiredSkills { get; set; } = "";

    [Column("student_message")]
    public string StudentMessage { get; set; } = "";

    [Range(1, 2)]
    [Column("requested_members_count")]
    public int RequestedMembersCount { get; set; } = 1;

    [Required]
    [StringLength(30)]
    [Column("status")]
    public string Status { get; set; } = "pending";

    [Column("matched_user_id")]
    public int? MatchedUserId { get; set; }

    [Column("matched_by_supervisor_id")]
    public int? MatchedBySupervisorId { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("updated_at")]
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [Column("matched_at")]
    public DateTime? MatchedAt { get; set; }

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(RequestedByUserId))]
    public User? RequestedByUser { get; set; }

    [ForeignKey(nameof(MatchedUserId))]
    public User? MatchedUser { get; set; }

    [ForeignKey(nameof(MatchedBySupervisorId))]
    public User? MatchedBySupervisor { get; set; }

    public ICollection<ProjectInvitation> Invitations { get; set; } = [];
}