using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("projects")]
public class Project
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("title")] [Required] public string Title { get; set; } = "";
    [Column("description")] public string Description { get; set; } = "";
    [Column("technologies")] public string Technologies { get; set; } = "";
    [Column("status")] public string Status { get; set; } = "planning";
    [Column("start_date")] public string? StartDate { get; set; }
    [Column("end_date")] public string? EndDate { get; set; }
    [Column("progress_percentage")] public int ProgressPercentage { get; set; } = 0;
    [Column("student_id")] public int StudentId { get; set; }
    [Column("supervisor_id")] public int? SupervisorId { get; set; }
    [Column("project_idea_id")] public int? ProjectIdeaId { get; set; }

    [Range(1, 3)]
    [Column("maximum_members")] public int MaximumMembers { get; set; } = 3;
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [Column("updated_at")] public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(StudentId))] public User? Student { get; set; }
    [ForeignKey(nameof(SupervisorId))] public User? Supervisor { get; set; }
    [ForeignKey(nameof(ProjectIdeaId))]
    public ProjectIdea? ProjectIdea { get; set; }

    public ICollection<ProjectMember> Members { get; set; } = [];
    public ICollection<ProjectIdea> GeneratedCandidateIdeas { get;  set;  } = [];

    public ICollection<ProjectInvitation> Invitations { get; set; } = [];

    public ICollection<TeammateRequest> TeammateRequests { get; set; } = [];

    public ICollection<ProjectActivity> Activities { get; set; } = [];

    public ICollection<ProjectTask> Tasks { get; set; } = [];
    public ICollection<Milestone> Milestones { get; set; } = [];
    public ICollection<Feedback> Feedbacks { get; set; } = [];
}

[Table("tasks")]
public class ProjectTask
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("title")] [Required] public string Title { get; set; } = "";
    [Column("description")] public string Description { get; set; } = "";
    [Column("status")] public string Status { get; set; } = "todo";
    [Column("priority")] public string Priority { get; set; } = "medium";
    [Column("due_date")] public string? DueDate { get; set; }
    [NotMapped] public string? Deadline { get => DueDate; set => DueDate = value; }
    [Column("project_id")] public int ProjectId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [Column("updated_at")] public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(ProjectId))] public Project? Project { get; set; }
}

[Table("milestones")]
public class Milestone
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("title")] [Required] public string Title { get; set; } = "";
    [Column("description")] public string Description { get; set; } = "";
    [Column("due_date")] public string? DueDate { get; set; }
    [Column("is_completed")] public bool IsCompleted { get; set; } = false;
    [Column("completion_percentage")] public int CompletionPercentage { get; set; } = 0;
    [Column("project_id")] public int ProjectId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(ProjectId))] public Project? Project { get; set; }
}

[Table("feedback")]
public class Feedback
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("content")] [Required] public string Content { get; set; } = "";
    [Column("rating")] public int Rating { get; set; } = 5;
    [Column("project_id")] public int ProjectId { get; set; }
    [Column("supervisor_id")] public int SupervisorId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(ProjectId))] public Project? Project { get; set; }
    [ForeignKey(nameof(SupervisorId))] public User? Supervisor { get; set; }
}

[Table("challenges")]
public class Challenge
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("title")] [Required] public string Title { get; set; } = "";
    [Column("description")] [Required] public string Description { get; set; } = "";
    [Column("required_skills")] public string RequiredSkills { get; set; } = "";
    [Column("difficulty_level")] public string DifficultyLevel { get; set; } = "intermediate";
    [Column("company_id")] public int CompanyId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(CompanyId))] public User? Company { get; set; }
}

[Table("activity")]
public class Activity
{
    [Key] [Column("id")] public int Id { get; set; }
    [Column("type")] [Required] public string Type { get; set; } = "";
    [Column("message")] [Required] public string Message { get; set; } = "";
    [Column("user_id")] public int UserId { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    [ForeignKey(nameof(UserId))] public User? User { get; set; }
}
