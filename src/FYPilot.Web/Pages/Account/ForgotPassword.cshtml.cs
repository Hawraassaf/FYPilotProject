using System.ComponentModel.DataAnnotations;
using System.Security.Cryptography;
using System.Text;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class ForgotPasswordModel(ApplicationDbContext db) : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? Message { get; set; }

    public string? DemoResetLink { get; set; }

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

        var email = Input.Email.Trim().ToLowerInvariant();

        var user = await db.Users
            .FirstOrDefaultAsync(u => u.Email.ToLower() == email);

        Message = "If an account with this email exists, a reset link has been generated.";

        if (user == null)
        {
            return Page();
        }

        var oldTokens = await db.PasswordResetTokens
            .Where(t => t.UserId == user.Id && t.UsedAt == null)
            .ToListAsync();

        foreach (var token in oldTokens)
        {
            token.UsedAt = DateTime.UtcNow;
        }

        var rawToken = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
        var tokenHash = HashToken(rawToken);

        var resetToken = new PasswordResetToken
        {
            UserId = user.Id,
            TokenHash = tokenHash,
            ExpiresAt = DateTime.UtcNow.AddMinutes(30),
            CreatedAt = DateTime.UtcNow
        };

        db.PasswordResetTokens.Add(resetToken);
        await db.SaveChangesAsync();

        DemoResetLink = Url.Page(
            "/Account/ResetPassword",
            pageHandler: null,
            values: new { token = rawToken },
            protocol: Request.Scheme
        );

        return Page();
    }

    private static string HashToken(string token)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(token));
        return Convert.ToHexString(bytes);
    }
}