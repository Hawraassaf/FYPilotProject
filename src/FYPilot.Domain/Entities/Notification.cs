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

    [Column("title")]
    public string Title { get; set; } = "";

    [Column("message")]
    public string Message { get; set; } = "";

    [Column("type")]
    public string Type { get; set; } = "";
    // assignment_approved, meeting_created, meeting_reminder

    [Column("url")]
    public string Url { get; set; } = "";

    [Column("is_read")]
    public bool IsRead { get; set; } = false;

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("read_at")]
    public DateTime? ReadAt { get; set; }

    [ForeignKey(nameof(RecipientUserId))]
    public User? RecipientUser { get; set; }
}