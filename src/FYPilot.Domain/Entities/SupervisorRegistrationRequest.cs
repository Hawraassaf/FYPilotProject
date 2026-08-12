using System.ComponentModel.DataAnnotations;
using System.ComponentModel.DataAnnotations.Schema;

namespace FYPilot.Domain.Entities;

/// <summary>
/// A Supervisor's application before an Admin approves it.
///
/// No <see cref="User"/> and no <see cref="SupervisorProfile"/> exist
/// for the applicant until an Admin approves this request. Selecting
/// Supervisor during public registration must never, by itself,
/// create an active Supervisor account.
///
/// PasswordHash and VerificationCodeHash are cleared once they are no
/// longer operationally necessary (see SupervisorRegistrationStatus).
/// </summary>
[Table("supervisor_registration_requests")]
public class SupervisorRegistrationRequest
{
    [Key]
    [Column("id")]
    public int Id { get; set; }

    [Required]
    [Column("full_name")]
    public string FullName { get; set; } = "";

    [Required]
    [Column("email")]
    public string Email { get; set; } = "";

    /// <summary>
    /// BCrypt hash of the applicant's chosen password, using the same
    /// hashing mechanism as <see cref="User.PasswordHash"/>. Cleared
    /// once the real User is created (approval) or the application
    /// is rejected -- the real User then owns the credential.
    /// </summary>
    [Column("password_hash")]
    public string? PasswordHash { get; set; }

    [Column("academic_title")]
    public string? AcademicTitle { get; set; }

    [Column("university")]
    public string? University { get; set; }

    [Column("department")]
    public string? Department { get; set; }

    [Column("specialization")]
    public string? Specialization { get; set; }

    [Column("professional_profile_url")]
    public string? ProfessionalProfileUrl { get; set; }

    /// <summary>
    /// BCrypt hash of the current six-digit verification code. Null
    /// once verified or once the application reaches a terminal
    /// state. The readable code itself is never stored.
    /// </summary>
    [Column("verification_code_hash")]
    public string? VerificationCodeHash { get; set; }

    /// <summary>
    /// Set only after the verification email was successfully sent.
    /// </summary>
    [Column("verification_sent_at_utc")]
    public DateTime? VerificationSentAtUtc { get; set; }

    [Column("verification_expires_at_utc")]
    public DateTime? VerificationExpiresAtUtc { get; set; }

    [Column("verification_failed_attempt_count")]
    public int VerificationFailedAttemptCount { get; set; }

    /// <summary>
    /// pending_email, awaiting_details, pending_admin, approved, or
    /// rejected. See <see cref="SupervisorRegistrationStatus"/>.
    /// </summary>
    [Column("status")]
    public string Status { get; set; } = SupervisorRegistrationStatus.PendingEmail;

    [Column("created_at_utc")]
    public DateTime CreatedAtUtc { get; set; } = DateTime.UtcNow;

    [Column("verified_at_utc")]
    public DateTime? VerifiedAtUtc { get; set; }

    [Column("submitted_at_utc")]
    public DateTime? SubmittedAtUtc { get; set; }

    [Column("reviewed_at_utc")]
    public DateTime? ReviewedAtUtc { get; set; }

    [Column("reviewed_by_admin_id")]
    public int? ReviewedByAdminId { get; set; }

    [Column("rejection_reason")]
    public string? RejectionReason { get; set; }

    [ForeignKey(nameof(ReviewedByAdminId))]
    public User? ReviewedByAdmin { get; set; }
}
