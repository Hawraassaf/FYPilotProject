namespace FYPilot.Web.Services.EmailVerification;

public interface IEmailVerificationService
{
    Task<SendEmailVerificationResult> SendCodeAsync(
        int userId,
        CancellationToken cancellationToken = default);

    Task<VerifyEmailCodeResult> VerifyCodeAsync(
        int userId,
        string code,
        CancellationToken cancellationToken = default);
}

public enum SendEmailVerificationStatus
{
    Sent,
    AlreadyVerified,
    CooldownActive,
    UserNotFound,
    DeliveryFailed
}

public sealed record SendEmailVerificationResult(
    SendEmailVerificationStatus Status,
    int RetryAfterSeconds = 0);

public enum VerifyEmailCodeStatus
{
    Verified,
    AlreadyVerified,
    InvalidCode,
    Expired,
    Locked,
    NoActiveCode,
    UserNotFound
}

public sealed record VerifyEmailCodeResult(
    VerifyEmailCodeStatus Status,
    int RemainingAttempts = 0);