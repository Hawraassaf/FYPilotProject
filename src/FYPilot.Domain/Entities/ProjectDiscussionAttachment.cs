using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_discussion_attachments")]
public class ProjectDiscussionAttachment
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("message_id")]
    public int MessageId { get; set; }

    [Required]
    [StringLength(255)]
    [Column("original_file_name")]
    public string OriginalFileName { get; set; } = "";

    [Required]
    [StringLength(255)]
    [Column("stored_file_name")]
    public string StoredFileName { get; set; } = "";

    [Required]
    [StringLength(150)]
    [Column("content_type")]
    public string ContentType { get; set; } = "";

    [Column("size_bytes")]
    public long SizeBytes { get; set; }

    [Required]
    [StringLength(500)]
    [Column("relative_path")]
    public string RelativePath { get; set; } = "";

    [Column("uploaded_at_utc")]
    public DateTime UploadedAtUtc { get; set; } =
        DateTime.UtcNow;

    [ForeignKey(nameof(MessageId))]
    public ProjectDiscussionMessage? Message
    {
        get;
        set;
    }
}