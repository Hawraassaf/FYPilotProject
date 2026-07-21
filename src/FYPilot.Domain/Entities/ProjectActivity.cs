using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("project_activities")]
public class ProjectActivity
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("project_id")]
    public int ProjectId { get; set; }

    /// <summary>
    /// The user who performed the action.
    /// </summary>
    [Column("user_id")]
    public int UserId { get; set; }

    /// <summary>
    /// Examples:
    /// project_created
    /// project_renamed
    /// idea_selected
    /// idea_replaced
    /// member_invited
    /// member_joined
    /// member_removed
    /// </summary>
    [Required]
    [StringLength(80)]
    [Column("action_type")]
    public string ActionType { get; set; } = "";

    [Required]
    [StringLength(1000)]
    [Column("description")]
    public string Description { get; set; } = "";

    /// <summary>
    /// Used when a project idea is replaced.
    /// </summary>
    [Column("previous_idea_id")]
    public int? PreviousIdeaId { get; set; }

    /// <summary>
    /// Used when an idea is selected or replaces another idea.
    /// </summary>
    [Column("new_idea_id")]
    public int? NewIdeaId { get; set; }

    [Column("created_at_utc")]
    public DateTime CreatedAtUtc { get; set; } = DateTime.UtcNow;

    [ForeignKey(nameof(ProjectId))]
    public Project? Project { get; set; }

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }

    [ForeignKey(nameof(PreviousIdeaId))]
    public ProjectIdea? PreviousIdea { get; set; }

    [ForeignKey(nameof(NewIdeaId))]
    public ProjectIdea? NewIdea { get; set; }
}