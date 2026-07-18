using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("mentor_chat_sessions")]
public class MentorChatSession
{
    [Key][Column("id")] public int Id { get; set; }

    [Column("user_id")] public int UserId { get; set; }

    [Column("idea_id")] public int? IdeaId { get; set; }

    [MaxLength(120)]
    [Column("title")]
    public string Title { get; set; } = "New chat";

    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("updated_at")] public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [Column("deleted_at")] public DateTime? DeletedAt { get; set; }

    [ForeignKey(nameof(UserId))] public User? User { get; set; }

    public List<ChatMessage> Messages { get; set; } = [];
}