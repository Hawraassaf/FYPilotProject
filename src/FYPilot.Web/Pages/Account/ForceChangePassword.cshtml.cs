using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

[Authorize(Roles = "admin")]
public class ForceChangePasswordModel(
    ApplicationDbContext db) : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? UserEmail { get; private set; }

    public sealed class InputModel
    {
        [Required(
            ErrorMessage = "Enter your current temporary password.")]
        [DataType(DataType.Password)]
        [Display(Name = "Current temporary password")]
        public string CurrentPassword { get; set; } = "";

        [Required(
            ErrorMessage = "Enter your new password.")]
        [StringLength(
            100,
            MinimumLength = 8,
            ErrorMessage =
                "The new password must contain at least 8 characters.")]
        [DataType(DataType.Password)]
        [Display(Name = "New secure password")]
        public string NewPassword { get; set; } = "";

        [Required(
            ErrorMessage = "Confirm your new password.")]
        [DataType(DataType.Password)]
        [Compare(
            nameof(NewPassword),
            ErrorMessage = "The new passwords do not match.")]
        [Display(Name = "Confirm new password")]
        public string ConfirmPassword { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync()
    {
        var user = await GetCurrentUserAsync();

        if (user == null)
        {
            return RedirectToPage("/Account/Login");
        }

        UserEmail = user.Email;

        if (!user.MustChangePassword)
        {
            return RedirectToPage("/Admin/Dashboard");
        }

        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var user = await GetCurrentUserAsync();

        if (user == null)
        {
            return RedirectToPage("/Account/Login");
        }

        UserEmail = user.Email;

        if (!user.MustChangePassword)
        {
            return RedirectToPage("/Admin/Dashboard");
        }

        ValidateNewPasswordStrength();

        if (!ModelState.IsValid)
        {
            return Page();
        }

        var currentPasswordIsCorrect =
            BCrypt.Net.BCrypt.Verify(
                Input.CurrentPassword,
                user.PasswordHash);

        if (!currentPasswordIsCorrect)
        {
            ModelState.AddModelError(
                "Input.CurrentPassword",
                "The current temporary password is incorrect.");

            return Page();
        }

        var newPasswordMatchesCurrentPassword =
            BCrypt.Net.BCrypt.Verify(
                Input.NewPassword,
                user.PasswordHash);

        if (newPasswordMatchesCurrentPassword)
        {
            ModelState.AddModelError(
                "Input.NewPassword",
                "Your new password must be different from the temporary password.");

            return Page();
        }

        user.PasswordHash =
            BCrypt.Net.BCrypt.HashPassword(
                Input.NewPassword);

        user.MustChangePassword = false;
        user.PasswordChangedAtUtc = DateTime.UtcNow;

        try
        {
            db.Users.Update(user);

            var savedChanges =
                await db.SaveChangesAsync();

            if (savedChanges == 0)
            {
                ModelState.AddModelError(
                    string.Empty,
                    "The password change was not saved. Please try again.");

                return Page();
            }
        }
        catch (DbUpdateConcurrencyException)
        {
            ModelState.AddModelError(
                string.Empty,
                "Your account was updated by another request. Please try again.");

            return Page();
        }
        catch (DbUpdateException)
        {
            ModelState.AddModelError(
                string.Empty,
                "The password could not be saved. Please try again.");

            return Page();
        }

        TempData["SuccessMessage"] =
            "Your password was changed successfully. " +
            "Your administrator account is now secured.";

        return RedirectToPage("/Admin/Dashboard");
    }

    private async Task<FYPilot.Domain.Entities.User?>
        GetCurrentUserAsync()
    {
        var userIdValue =
            User.FindFirst(
                ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(
                userIdValue,
                out var userId))
        {
            return null;
        }

        return await db.Users
            .FirstOrDefaultAsync(
                u => u.Id == userId);
    }

    private void ValidateNewPasswordStrength()
    {
        var password =
            Input.NewPassword ?? "";

        if (password.Length < 8)
        {
            return;
        }

        if (!password.Any(char.IsUpper))
        {
            ModelState.AddModelError(
                "Input.NewPassword",
                "The new password must contain at least one uppercase letter.");
        }

        if (!password.Any(char.IsLower))
        {
            ModelState.AddModelError(
                "Input.NewPassword",
                "The new password must contain at least one lowercase letter.");
        }

        if (!password.Any(char.IsDigit))
        {
            ModelState.AddModelError(
                "Input.NewPassword",
                "The new password must contain at least one number.");
        }
    }
}