using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_members")]
public class ProjectMember
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Required]
    [StringLength(30)]
    [Column("role")]
    public string Role { get; set; } = "collaborator";

    [Required]
    [StringLength(30)]
    [Column("status")]
    public string Status { get; set; } = "active";

    [Column("joined_at")]
    public DateTime JoinedAt { get; set; } = DateTime.UtcNow;

    [Column("left_at")]
    public DateTime? LeftAt { get; set; }

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }
}