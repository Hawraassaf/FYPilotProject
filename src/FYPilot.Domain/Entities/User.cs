using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("users")]
public class User
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("email")]
    [Required]
    public string Email { get; set; } = "";

    [Column("full_name")]
    [Required]
    public string FullName { get; set; } = "";

    [Column("password_hash")]
    [Required]
    public string PasswordHash { get; set; } = "";

    [Column("role")]
    [Required]
    public string Role { get; set; } = "student";

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public StudentProfile? StudentProfile { get; set; }
    public SupervisorProfile? SupervisorProfile { get; set; }
    public CompanyProfile? CompanyProfile { get; set; }
    public ICollection<Project> Projects { get; set; } = [];
    public ICollection<Activity> Activities { get; set; } = [];
}
