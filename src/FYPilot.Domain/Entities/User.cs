using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("users")]
public class User
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Required]
    [Column("email")]
    public string Email { get; set; } = "";

    [Required]
    [Column("full_name")]
    public string FullName { get; set; } = "";

    [Required]
    [Column("password_hash")]
    public string PasswordHash { get; set; } = "";

    [Required]
    [Column("role")]
    public string Role { get; set; } = "student";

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public StudentProfile? StudentProfile { get; set; }

    public SupervisorProfile? SupervisorProfile { get; set; }

    public CompanyProfile? CompanyProfile { get; set; }

    /// <summary>
    /// Projects originally created/owned by this user.
    /// </summary>
    public ICollection<Project> Projects { get; set; } = [];

    /// <summary>
    /// Every project where the user is an active, removed,
    /// or former project member.
    /// </summary>
    public ICollection<ProjectMember> ProjectMemberships { get; set; } = [];

    /// <summary>
    /// Existing user-level activity records.
    /// These remain separate from shared project activity.
    /// </summary>
    public ICollection<Activity> Activities { get; set; } = [];

    /// <summary>
    /// Shared project actions performed by this user.
    /// </summary>
    public ICollection<ProjectActivity> ProjectActivities { get; set; } = [];

    public bool MustChangePassword { get; set; }

    public DateTime? PasswordChangedAtUtc { get; set; }

    /// <summary>
    /// The project the student was using most recently.
    /// </summary>
    [Column("last_active_project_id")]
    public int? LastActiveProjectId { get; set; }

    /// <summary>
    /// The last approved project-related Razor Page route visited
    /// by this user, such as /Student/Roadmap.
    /// </summary>
    [StringLength(200)]
    [Column("last_project_page")]
    public string? LastProjectPage { get; set; }

    [Column("last_project_visited_at_utc")]
    public DateTime? LastProjectVisitedAtUtc { get; set; }

    [ForeignKey(nameof(LastActiveProjectId))]
    public Project? LastActiveProject { get; set; }
}