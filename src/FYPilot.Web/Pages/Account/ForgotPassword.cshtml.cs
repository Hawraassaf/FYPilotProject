using System.ComponentModel.DataAnnotations;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace FYPilot.Web.Pages.Account;

public class ForgotPasswordModel(
    ApplicationDbContext db,
    IEmailSender emailSender,
    ILogger<ForgotPasswordModel> logger) : PageModel
{
    private const string GenericResponse =
    "If an account exists for this email, " +
    "password reset instructions will be sent.";

    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? Message { get; set; }
    public string? ErrorMessage { get; set; }

    public class InputModel
    {
        [Required(ErrorMessage = "Email is required.")]
        [EmailAddress(ErrorMessage = "Please enter a valid email address.")]
        public string Email { get; set; } = "";
    }

    public void OnGet()
    {
    }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
        {
            return Page();
        }

        var email = Input.Email
            .Trim()
            .ToLowerInvariant();

        var user = await db.Users
            .FirstOrDefaultAsync(u =>
                u.Email.ToLower() == email);

        // Do not reveal whether the email is registered.
        if (user == null)
        {
            Message = GenericResponse;
            return Page();
        }

        var oldTokens = await db.PasswordResetTokens
            .Where(t =>
                t.UserId == user.Id &&
                t.UsedAt == null)
            .ToListAsync();

        foreach (var oldToken in oldTokens)
        {
            oldToken.UsedAt = DateTime.UtcNow;
        }

        var rawToken = Convert.ToHexString(
            RandomNumberGenerator.GetBytes(32));

        var tokenHash = HashToken(rawToken);

        var resetToken =
            new FYPilot.Domain.Entities.PasswordResetToken
            {
                UserId = user.Id,
                TokenHash = tokenHash,
                ExpiresAt = DateTime.UtcNow.AddMinutes(30),
                CreatedAt = DateTime.UtcNow
            };

        db.PasswordResetTokens.Add(resetToken);
        await db.SaveChangesAsync();

        var resetLink = Url.Page(
            "/Account/ResetPassword",
            pageHandler: null,
            values: new
            {
                token = rawToken
            },
            protocol: Request.Scheme);

        if (string.IsNullOrWhiteSpace(resetLink))
        {
            logger.LogWarning(
                "Password reset link could not be generated " +
                "for user ID {UserId}.",
                user.Id);

            Message = GenericResponse;
            return Page();
        }

        var safeResetLink =
            WebUtility.HtmlEncode(resetLink);

        var emailBody = $"""
        <div style="font-family:Arial,sans-serif;line-height:1.6;">
            <h2>Reset your FYPilot password</h2>

            <p>
                Reset your password by clicking
                <a href="{safeResetLink}">here</a>.
            </p>

            <p>This link will expire in 30 minutes.</p>

            <p>
                If you did not request this password reset,
                you can ignore this email.
            </p>
        </div>
        """;

        try
        {
            await emailSender.SendAsync(
                user.Email,
                "Reset your FYPilot password",
                emailBody);
        }
        catch (Exception ex)
        {
            // Keep the technical error in the server logs,
            // not on the public page.
            logger.LogError(
                ex,
                "Password reset email failed for user ID {UserId}.",
                user.Id);
        }

        Message = GenericResponse;
        return Page();
    }

    private static string HashToken(string token)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(token));
        return Convert.ToHexString(bytes);
    }
}