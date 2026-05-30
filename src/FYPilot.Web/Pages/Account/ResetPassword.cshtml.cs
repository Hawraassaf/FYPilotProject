using System.ComponentModel.DataAnnotations;
using System.Security.Cryptography;
using System.Text;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class ResetPasswordModel(ApplicationDbContext db) : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? ErrorMessage { get; set; }

    public class InputModel
    {
        [Required]
        public string Token { get; set; } = "";

        [Required(ErrorMessage = "New password is required.")]
        [MinLength(6, ErrorMessage = "Password must be at least 6 characters.")]
        public string NewPassword { get; set; } = "";

        [Required(ErrorMessage = "Confirm password is required.")]
        [Compare(nameof(NewPassword), ErrorMessage = "Passwords do not match.")]
        public string ConfirmPassword { get; set; } = "";
    }

    public IActionResult OnGet(string? token)
    {
        if (string.IsNullOrWhiteSpace(token))
        {
            ErrorMessage = "Invalid password reset link.";
            return Page();
        }

        Input.Token = token;
        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
        {
            return Page();
        }

        var tokenHash = HashToken(Input.Token);

        var resetToken = await db.PasswordResetTokens
            .FirstOrDefaultAsync(t =>
                t.TokenHash == tokenHash &&
                t.UsedAt == null &&
                t.ExpiresAt > DateTime.UtcNow);

        if (resetToken == null)
        {
            ErrorMessage = "This password reset link is invalid or expired.";
            return Page();
        }

        var user = await db.Users
            .FirstOrDefaultAsync(u => u.Id == resetToken.UserId);

        if (user == null)
        {
            ErrorMessage = "User account was not found.";
            return Page();
        }

        user.PasswordHash = BCrypt.Net.BCrypt.HashPassword(Input.NewPassword);
        resetToken.UsedAt = DateTime.UtcNow;

        await db.SaveChangesAsync();

        TempData["PasswordResetMessage"] = "Password reset successfully. You can now log in with your new password.";
        return RedirectToPage("/Account/Login");
    }

    private static string HashToken(string token)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(token));
        return Convert.ToHexString(bytes);
    }
}