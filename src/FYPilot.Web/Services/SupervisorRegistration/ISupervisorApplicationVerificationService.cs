namespace FYPilot.Web.Services.SupervisorRegistration;

/// <summary>
/// Pre-account email verification for a
/// FYPilot.Domain.Entities.SupervisorRegistrationRequest.
///
/// This intentionally does not reuse
/// FYPilot.Web.Services.EmailVerification.IEmailVerificationService:
/// that service's EmailVerificationCode table has a required
/// (non-nullable) foreign key to an existing User, and a Supervisor
/// applicant has no User until an Admin approves the request. The
/// security rules (six digits, BCrypt hash, 10-minute lifetime,
/// 60-second resend cooldown, 5 failed attempts) mirror
/// IEmailVerificationService exactly.
/// </summary>
public interface ISupervisorApplicationVerificationService
{
    Task<SendSupervisorVerificationResult> SendCodeAsync(
        int requestId,
        CancellationToken cancellationToken = default);

    Task<VerifySupervisorCodeResult> VerifyCodeAsync(
        int requestId,
        string code,
        CancellationToken cancellationToken = default);
}

public enum SendSupervisorVerificationStatus
{
    Sent,
    AlreadyVerified,
    CooldownActive,
    RequestNotFound,
    DeliveryFailed
}

public sealed record SendSupervisorVerificationResult(
    SendSupervisorVerificationStatus Status,
    int RetryAfterSeconds = 0);

public enum VerifySupervisorCodeStatus
{
    Verified,
    AlreadyVerified,
    InvalidCode,
    Expired,
    Locked,
    NoActiveCode,
    RequestNotFound
}

public sealed record VerifySupervisorCodeResult(
    VerifySupervisorCodeStatus Status,
    int RemainingAttempts = 0);
