using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

[Table("google_calendar_tokens")]
public class GoogleCalendarToken
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("supervisor_id")]
    public int SupervisorId { get; set; }

    [Column("access_token")]
    public string AccessToken { get; set; } = "";

    [Column("refresh_token")]
    public string RefreshToken { get; set; } = "";

    [Column("expiration")]
    public DateTime Expiration { get; set; }

    [Column("created_at")]
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    [Column("updated_at")]
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    [ForeignKey(nameof(SupervisorId))]
    public User? Supervisor { get; set; }
}