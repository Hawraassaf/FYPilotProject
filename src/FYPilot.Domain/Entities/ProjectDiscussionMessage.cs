using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_discussion_messages")]
public class ProjectDiscussionMessage
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Required]
    [StringLength(1000)]
    [Column("content")]
    public string Content { get; set; } = "";

    [Column("reply_to_message_id")]
    public int? ReplyToMessageId { get; set; }

    [Column("is_edited")]
    public bool IsEdited { get; set; }

    [Column("edited_at_utc")]
    public DateTime? EditedAtUtc { get; set; }

    [Column("is_deleted")]
    public bool IsDeleted { get; set; }

    [Column("deleted_at_utc")]
    public DateTime? DeletedAtUtc { get; set; }

    [Column("created_at_utc")]
    public DateTime CreatedAtUtc { get; set; } =
        DateTime.UtcNow;

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }

    [ForeignKey(nameof(ReplyToMessageId))]
    public ProjectDiscussionMessage? ReplyToMessage
    {
        get;
        set;
    }

    public ICollection<ProjectDiscussionMessage> Replies
    {
        get;
        set;
    } = [];

    public ICollection<ProjectDiscussionAttachment> Attachments
    {
        get;
        set;
    } = [];
}