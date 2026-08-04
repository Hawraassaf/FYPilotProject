using System.Data;
using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Services.EmailVerification;

public sealed class EmailVerificationService(
    ApplicationDbContext db,
    IEmailSender emailSender,
    ILogger<EmailVerificationService> logger)
    : IEmailVerificationService
{
    private const int MaximumFailedAttempts = 5;

    private static readonly TimeSpan CodeLifetime =
        TimeSpan.FromMinutes(10);

    private static readonly TimeSpan ResendCooldown =
        TimeSpan.FromSeconds(60);

    public async Task<SendEmailVerificationResult> SendCodeAsync(
        int userId,
        CancellationToken cancellationToken = default)
    {
        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Id == userId,
                cancellationToken);

        if (user == null)
        {
            return new SendEmailVerificationResult(
                SendEmailVerificationStatus.UserNotFound);
        }

        if (user.IsEmailVerified)
        {
            return new SendEmailVerificationResult(
                SendEmailVerificationStatus.AlreadyVerified);
        }

        var now = DateTime.UtcNow;

        var latestSuccessfullySentCode =
            await db.EmailVerificationCodes
                .AsNoTracking()
                .Where(item =>
                    item.UserId == userId &&
                    item.SentAtUtc != null)
                .OrderByDescending(item => item.SentAtUtc)
                .FirstOrDefaultAsync(cancellationToken);

        if (latestSuccessfullySentCode?.SentAtUtc is { } sentAt)
        {
            var elapsed = now - sentAt;

            if (elapsed < ResendCooldown)
            {
                var remainingSeconds =
                    (int)Math.Ceiling(
                        (ResendCooldown - elapsed)
                            .TotalSeconds);

                return new SendEmailVerificationResult(
                    SendEmailVerificationStatus.CooldownActive,
                    Math.Max(1, remainingSeconds));
            }
        }

        var readableCode =
            RandomNumberGenerator
                .GetInt32(0, 1_000_000)
                .ToString("D6", CultureInfo.InvariantCulture);

        var pendingCode = new EmailVerificationCode
        {
            UserId = user.Id,
            CodeHash = BCrypt.Net.BCrypt.HashPassword(
                readableCode),
            CreatedAtUtc = now,
            ExpiresAtUtc = now.Add(CodeLifetime),
            FailedAttemptCount = 0
        };

        db.EmailVerificationCodes.Add(pendingCode);
        await db.SaveChangesAsync(cancellationToken);

        try
        {
            await emailSender.SendAsync(
                user.Email,
                "Verify your FYPilot email address",
                BuildVerificationEmail(
                    user.FullName,
                    readableCode));
        }
        catch (Exception exception)
        {
            /*
             * SentAtUtc remains null, so this record can never be used
             * for verification. Keep the previous successfully sent code
             * valid when delivery of the replacement fails.
             */
            pendingCode.UsedAtUtc = DateTime.UtcNow;

            try
            {
                await db.SaveChangesAsync(cancellationToken);
            }
            catch (Exception cleanupException)
            {
                logger.LogWarning(
                    cleanupException,
                    "Could not invalidate undelivered email verification "
                    + "code {CodeId} for user {UserId}.",
                    pendingCode.Id,
                    user.Id);
            }

            logger.LogWarning(
                exception,
                "Could not send an email verification code to user "
                + "{UserId}.",
                user.Id);

            return new SendEmailVerificationResult(
                SendEmailVerificationStatus.DeliveryFailed);
        }

        var successfullySentAt = DateTime.UtcNow;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var previousActiveCodes =
                await db.EmailVerificationCodes
                    .Where(item =>
                        item.UserId == user.Id &&
                        item.Id != pendingCode.Id &&
                        item.SentAtUtc != null &&
                        item.UsedAtUtc == null)
                    .ToListAsync(cancellationToken);

            foreach (var previousCode in previousActiveCodes)
            {
                previousCode.UsedAtUtc = successfullySentAt;
            }

            pendingCode.SentAtUtc = successfullySentAt;
            pendingCode.ExpiresAtUtc =
                successfullySentAt.Add(CodeLifetime);

            await db.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch
        {
            await transaction.RollbackAsync(cancellationToken);
            throw;
        }

        return new SendEmailVerificationResult(
            SendEmailVerificationStatus.Sent);
    }

    public async Task<VerifyEmailCodeResult> VerifyCodeAsync(
        int userId,
        string code,
        CancellationToken cancellationToken = default)
    {
        var normalizedCode = code?.Trim() ?? "";

        if (normalizedCode.Length != 6 ||
            normalizedCode.Any(character =>
                !char.IsAsciiDigit(character)))
        {
            return new VerifyEmailCodeResult(
                VerifyEmailCodeStatus.InvalidCode,
                MaximumFailedAttempts);
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                IsolationLevel.Serializable,
                cancellationToken);

        try
        {
            var user = await db.Users
                .FirstOrDefaultAsync(
                    item => item.Id == userId,
                    cancellationToken);

            if (user == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return new VerifyEmailCodeResult(
                    VerifyEmailCodeStatus.UserNotFound);
            }

            if (user.IsEmailVerified)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return new VerifyEmailCodeResult(
                    VerifyEmailCodeStatus.AlreadyVerified);
            }

            var activeCode =
                await db.EmailVerificationCodes
                    .Where(item =>
                        item.UserId == user.Id &&
                        item.SentAtUtc != null &&
                        item.UsedAtUtc == null)
                    .OrderByDescending(item => item.SentAtUtc)
                    .FirstOrDefaultAsync(cancellationToken);

            if (activeCode == null)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return new VerifyEmailCodeResult(
                    VerifyEmailCodeStatus.NoActiveCode);
            }

            var now = DateTime.UtcNow;

            if (activeCode.ExpiresAtUtc <= now)
            {
                activeCode.UsedAtUtc = now;
                await db.SaveChangesAsync(cancellationToken);
                await transaction.CommitAsync(cancellationToken);

                return new VerifyEmailCodeResult(
                    VerifyEmailCodeStatus.Expired);
            }

            if (activeCode.FailedAttemptCount >=
                MaximumFailedAttempts)
            {
                await transaction.RollbackAsync(
                    cancellationToken);

                return new VerifyEmailCodeResult(
                    VerifyEmailCodeStatus.Locked);
            }

            var matches = false;

            try
            {
                matches = BCrypt.Net.BCrypt.Verify(
                    normalizedCode,
                    activeCode.CodeHash);
            }
            catch (Exception exception)
            {
                logger.LogError(
                    exception,
                    "Stored email verification hash {CodeId} is invalid.",
                    activeCode.Id);
            }

            if (!matches)
            {
                activeCode.FailedAttemptCount++;

                var remainingAttempts = Math.Max(
                    0,
                    MaximumFailedAttempts -
                    activeCode.FailedAttemptCount);

                if (remainingAttempts == 0)
                {
                    activeCode.UsedAtUtc = now;
                }

                await db.SaveChangesAsync(cancellationToken);
                await transaction.CommitAsync(cancellationToken);

                return new VerifyEmailCodeResult(
                    remainingAttempts == 0
                        ? VerifyEmailCodeStatus.Locked
                        : VerifyEmailCodeStatus.InvalidCode,
                    remainingAttempts);
            }

            user.IsEmailVerified = true;
            user.EmailVerifiedAtUtc = now;

            var unusedCodes =
                await db.EmailVerificationCodes
                    .Where(item =>
                        item.UserId == user.Id &&
                        item.UsedAtUtc == null)
                    .ToListAsync(cancellationToken);

            foreach (var unusedCode in unusedCodes)
            {
                unusedCode.UsedAtUtc = now;
            }

            await db.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);

            return new VerifyEmailCodeResult(
                VerifyEmailCodeStatus.Verified);
        }
        catch
        {
            await transaction.RollbackAsync(cancellationToken);
            throw;
        }
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
                    <h1 style="margin:0 0 16px;color:#28385e;font-size:24px;">Verify your FYPilot email</h1>
                    <p style="margin:0 0 16px;line-height:1.6;">Hello {{safeName}},</p>
                    <p style="margin:0 0 20px;line-height:1.6;">Enter this code in FYPilot to confirm that this email address belongs to you:</p>
                    <div style="margin:0 0 20px;padding:18px;text-align:center;background:#eef2f6;border-radius:12px;font-size:32px;font-weight:700;letter-spacing:8px;color:#28385e;">{{safeCode}}</div>
                    <p style="margin:0 0 8px;line-height:1.6;">This code expires in 10 minutes.</p>
                    <p style="margin:0;color:#6b7280;line-height:1.6;">Do not share this code with anyone. If you did not create this account, you can ignore this email.</p>
                </div>
            </body>
            </html>
            """;
    }
}