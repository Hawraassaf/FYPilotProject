using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("student_profiles")]
public class StudentProfile
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Column("university")]
    public string University { get; set; } = "";

    [Column("major")]
    public string Major { get; set; } = "Computer Science";

    [Column("year")]
    public string Year { get; set; } = "3rd Year";

    [Column("skills")]
    public string Skills { get; set; } = "";

    [Column("interests")]
    public string Interests { get; set; } = "";

    [Column("experience_level")]
    public string ExperienceLevel { get; set; } = "beginner";

    [Column("preferred_domain")]
    public string PreferredDomain { get; set; } = "";

    [Column("preferred_stack")]
    public string PreferredStack { get; set; } = "";

    [Column("available_hours_per_week")]
    public int AvailableHoursPerWeek { get; set; } = 20;

    [Column("team_members")]
    public int TeamMembers { get; set; } = 1;

    [Column("target_difficulty")]
    public string TargetDifficulty { get; set; } = "intermediate";

    [Column("project_goals")]
    public string ProjectGoals { get; set; } = "";

    [Column("profile_image_path")]
    public string? ProfileImagePath { get; set; }

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }
}

[Table("supervisor_profiles")]
public class SupervisorProfile
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Column("department")]
    public string Department { get; set; } = "Computer Science";

    [Column("specialization")]
    public string Specialization { get; set; } = "";

    [Column("bio")]
    public string Bio { get; set; } = "";

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }
}

[Table("company_profiles")]
public class CompanyProfile
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Column("company_name")]
    public string CompanyName { get; set; } = "";

    [Column("industry")]
    public string Industry { get; set; } = "Technology";

    [Column("description")]
    public string Description { get; set; } = "";

    [Column("website")]
    public string Website { get; set; } = "";

    [ForeignKey(nameof(UserId))]
    public User? User { get; set; }
}