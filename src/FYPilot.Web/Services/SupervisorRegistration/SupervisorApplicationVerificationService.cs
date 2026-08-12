using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.SupervisorRegistration;

/// <summary>
/// Verification-code fields live directly on the
/// SupervisorRegistrationRequest row (there is no child table like
/// EmailVerificationCode), so at most one code can ever be valid for
/// a given request at a time -- a resend simply overwrites the
/// previous code's hash/timestamps.
/// </summary>
public sealed class SupervisorApplicationVerificationService(
    ApplicationDbContext db,
    IEmailSender emailSender,
    ILogger<SupervisorApplicationVerificationService> logger)
    : ISupervisorApplicationVerificationService
{
    private const int MaximumFailedAttempts = 5;

    private static readonly TimeSpan CodeLifetime =
        TimeSpan.FromMinutes(10);

    private static readonly TimeSpan ResendCooldown =
        TimeSpan.FromSeconds(60);

    public async Task<SendSupervisorVerificationResult> SendCodeAsync(
        int requestId,
        CancellationToken cancellationToken = default)
    {
        var request = await db.SupervisorRegistrationRequests
            .FirstOrDefaultAsync(
                item => item.Id == requestId,
                cancellationToken);

        if (request == null)
        {
            return new SendSupervisorVerificationResult(
                SendSupervisorVerificationStatus.RequestNotFound);
        }

        if (request.Status != SupervisorRegistrationStatus.PendingEmail)
        {
            return new SendSupervisorVerificationResult(
                SendSupervisorVerificationStatus.AlreadyVerified);
        }

        var now = DateTime.UtcNow;

        if (request.VerificationSentAtUtc is { } sentAt)
        {
            var elapsed = now - sentAt;

            if (elapsed < ResendCooldown)
            {
                var remainingSeconds =
                    (int)Math.Ceiling(
                        (ResendCooldown - elapsed).TotalSeconds);

                return new SendSupervisorVerificationResult(
                    SendSupervisorVerificationStatus.CooldownActive,
                    Math.Max(1, remainingSeconds));
            }
        }

        var readableCode =
            RandomNumberGenerator
                .GetInt32(0, 1_000_000)
                .ToString("D6", CultureInfo.InvariantCulture);

        /*
         * Send before touching the database. If delivery fails, the
         * previous code (if any and still valid) is left completely
         * untouched, so an applicant who already had a working code
         * is never locked out by a failed resend.
         */
        try
        {
            await emailSender.SendAsync(
                request.Email,
                "Verify your FYPilot Supervisor application email",
                BuildVerificationEmail(
                    request.FullName,
                    readableCode));
        }
        catch (Exception exception)
        {
            logger.LogWarning(
                exception,
                "Could not send a verification code for supervisor "
                + "application {RequestId}.",
                requestId);

            return new SendSupervisorVerificationResult(
                SendSupervisorVerificationStatus.DeliveryFailed);
        }

        var successfullySentAt = DateTime.UtcNow;

        request.VerificationCodeHash =
            BCrypt.Net.BCrypt.HashPassword(readableCode);
        request.VerificationSentAtUtc = successfullySentAt;
        request.VerificationExpiresAtUtc =
            successfullySentAt.Add(CodeLifetime);
        request.VerificationFailedAttemptCount = 0;

        try
        {
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            /*
             * The email was sent but the new code could not be
             * saved. Report a delivery failure so the applicant is
             * prompted to request a new code rather than trying the
             * one they just received (which can never validate).
             */
            logger.LogError(
                exception,
                "A verification code was sent but could not be "
                + "saved for supervisor application {RequestId}.",
                requestId);

            return new SendSupervisorVerificationResult(
                SendSupervisorVerificationStatus.DeliveryFailed);
        }

        return new SendSupervisorVerificationResult(
            SendSupervisorVerificationStatus.Sent);
    }

    public async Task<VerifySupervisorCodeResult> VerifyCodeAsync(
        int requestId,
        string code,
        CancellationToken cancellationToken = default)
    {
        var normalizedCode = code?.Trim() ?? "";

        if (normalizedCode.Length != 6 ||
            normalizedCode.Any(character =>
                !char.IsAsciiDigit(character)))
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.InvalidCode,
                MaximumFailedAttempts);
        }

        var request = await db.SupervisorRegistrationRequests
            .FirstOrDefaultAsync(
                item => item.Id == requestId,
                cancellationToken);

        if (request == null)
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.RequestNotFound);
        }

        if (request.Status != SupervisorRegistrationStatus.PendingEmail)
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.AlreadyVerified);
        }

        if (request.VerificationCodeHash == null ||
            request.VerificationSentAtUtc == null ||
            request.VerificationExpiresAtUtc == null)
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.NoActiveCode);
        }

        var now = DateTime.UtcNow;

        if (request.VerificationExpiresAtUtc.Value <= now)
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.Expired);
        }

        if (request.VerificationFailedAttemptCount >=
            MaximumFailedAttempts)
        {
            return new VerifySupervisorCodeResult(
                VerifySupervisorCodeStatus.Locked);
        }

        var matches = false;

        try
        {
            matches = BCrypt.Net.BCrypt.Verify(
                normalizedCode,
                request.VerificationCodeHash);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Stored supervisor application verification hash is "
                + "invalid for request {RequestId}.",
                requestId);
        }

        if (!matches)
        {
            request.VerificationFailedAttemptCount++;

            var remainingAttempts = Math.Max(
                0,
                MaximumFailedAttempts -
                request.VerificationFailedAttemptCount);

            await db.SaveChangesAsync(cancellationToken);

            return new VerifySupervisorCodeResult(
                remainingAttempts == 0
                    ? VerifySupervisorCodeStatus.Locked
                    : VerifySupervisorCodeStatus.InvalidCode,
                remainingAttempts);
        }

        request.Status = SupervisorRegistrationStatus.AwaitingDetails;
        request.VerifiedAtUtc = now;

        /*
         * The verification code is no longer needed once the email
         * is confirmed -- clear it immediately rather than waiting
         * for a terminal (approved/rejected) state.
         */
        request.VerificationCodeHash = null;
        request.VerificationSentAtUtc = null;
        request.VerificationExpiresAtUtc = null;
        request.VerificationFailedAttemptCount = 0;

        await db.SaveChangesAsync(cancellationToken);

        return new VerifySupervisorCodeResult(
            VerifySupervisorCodeStatus.Verified);
    }

    private static string BuildVerificationEmail(
        string? fullName,
        string code)
    {
        var safeName = WebUtility.HtmlEncode(
            string.IsNullOrWhiteSpace(fullName)
                ? "there"
                : fullName.Trim());

        var safeCode = WebUtility.HtmlEncode(code);

        return $$"""
            <!DOCTYPE html>
            <html lang="en">
            <body style="margin:0;padding:24px;background:#f4f6f9;font-family:Arial,sans-serif;color:#1f2937;">
                <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;padding:32px;">
                    <h1 style="margin:0 0 16px;color:#28385e;font-size:24px;">Verify your Supervisor application email</h1>
                    <p style="margin:0 0 16px;line-height:1.6;">Hello {{safeName}},</p>
                    <p style="margin:0 0 20px;line-height:1.6;">Enter this code to confirm your email address for your FYPilot Supervisor application:</p>
                    <div style="margin:0 0 20px;padding:18px;text-align:center;background:#eef2f6;border-radius:12px;font-size:32px;font-weight:700;letter-spacing:8px;color:#28385e;">{{safeCode}}</div>
                    <p style="margin:0 0 8px;line-height:1.6;">This code expires in 10 minutes.</p>
                    <p style="margin:0;color:#6b7280;line-height:1.6;">Do not share this code with anyone. If you did not request a Supervisor account, you can ignore this email.</p>
                </div>
            </body>
            </html>
            """;
    }
}
