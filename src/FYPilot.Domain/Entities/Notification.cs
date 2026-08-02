using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("notifications")]
public class Notification
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("recipient_user_id")]
    public int RecipientUserId { get; set; }

    [Column("project_id")]
    public int? ProjectId { get; set; }

    [Column("actor_user_id")]
    public int? ActorUserId { get; set; }

    [Required]
    [StringLength(200)]
    [Column("title")]
    public string Title { get; set; } = "";

    [Required]
    [StringLength(1200)]
    [Column("message")]
    public string Message { get; set; } = "";

    [Required]
    [StringLength(80)]
    [Column("type")]
    public string Type { get; set; } = "general";

    [StringLength(500)]
    [Column("url")]
    public string Url { get; set; } = "";

    [Column("is_read")]
    public bool IsRead { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("read_at")]
    public DateTime? ReadAt { get; set; }

    [ForeignKey(nameof(RecipientUserId))]
    public User? RecipientUser { get; set; }

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(ActorUserId))]
    public User? ActorUser { get; set; }
}
