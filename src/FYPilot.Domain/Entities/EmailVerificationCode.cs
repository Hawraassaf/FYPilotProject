using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

/// <summary>
/// Stores one temporary email-verification code.
///
/// The readable six-digit code is never stored.
/// CodeHash contains only the BCrypt hash.
/// </summary>
[Table("email_verification_codes")]
public class EmailVerificationCode
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Column("user_id")]
    public int UserId { get; set; }

    [Required]
    [StringLength(100)]
    [Column("code_hash")]
    public string CodeHash { get; set; } = "";

    [Column("created_at_utc")]
    public DateTime CreatedAtUtc { get; set; } =
        DateTime.UtcNow;

    /// <summary>
    /// Set only after the verification email was successfully sent.
    /// </summary>
    [Column("sent_at_utc")]
    public DateTime? SentAtUtc { get; set; }

    [Column("expires_at_utc")]
    public DateTime ExpiresAtUtc { get; set; }

    /// <summary>
    /// Set after successful verification or when the code is
    /// invalidated by a newer code.
    /// </summary>
    [Column("used_at_utc")]
    public DateTime? UsedAtUtc { get; set; }

    [Column("failed_attempt_count")]
    public int FailedAttemptCount { get; set; }

    [ForeignKey(nameof(UserId))]
    public User User { get; set; } = null!;
}