using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using System.Text.RegularExpressions;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class AdminAccountsModel(ApplicationDbContext db) : PageModel
{
    private const string MainAdminEmail = "admin@fyp.com";

    [BindProperty]
    public CreateAdminInput Input { get; set; } = new();

    public List<AdminRow> Admins { get; private set; } = [];

    public string NextAdminEmail { get; private set; } = "";

    public bool IsMainAdminAccount { get; private set; }

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public async Task<IActionResult> OnGetAsync()
    {
        if (!IsCurrentUserMainAdmin())
        {
            return Forbid();
        }

        await LoadAsync();
        return Page();
    }

    public async Task<IActionResult> OnPostCreateAsync()
    {
        if (!IsCurrentUserMainAdmin())
        {
            return Forbid();
        }

        ValidatePasswordStrength();

        var nextNumber = await GetNextAdminNumberAsync();
        var generatedEmail = BuildAdminEmail(nextNumber);

        if (!ModelState.IsValid)
        {
            await LoadAsync(nextNumber);
            return Page();
        }

        var fullName = Input.FullName.Trim();

        var emailExists = await db.Users
            .AnyAsync(u =>
                u.Email.ToLower() == generatedEmail.ToLower());

        if (emailExists)
        {
            ModelState.AddModelError(
                string.Empty,
                "The generated administrator email is already in use. Refresh the page and try again.");

            await LoadAsync();
            return Page();
        }

        var administrator = new User
        {
            FullName = fullName,
            Email = generatedEmail,

            // This password is only the temporary first-login password.
            PasswordHash = BCrypt.Net.BCrypt.HashPassword(Input.Password),

            Role = "admin",
            CreatedAt = DateTime.UtcNow,

            // The new admin must change the temporary password
            // before accessing the administrator pages.
            MustChangePassword = true,
            PasswordChangedAtUtc = null
        };

        try
        {
            db.Users.Add(administrator);
            await db.SaveChangesAsync();

            SuccessMessage =
                $"Administrator {generatedEmail} was created successfully. " +
                "They must change their temporary password during their first login.";

            return RedirectToPage();
        }
        catch (DbUpdateException)
        {
            ErrorMessage =
                "The administrator could not be created. " +
                "The generated email may already be in use.";

            return RedirectToPage();
        }
    }

    private async Task LoadAsync(int? preparedNextNumber = null)
    {
        IsMainAdminAccount = IsCurrentUserMainAdmin();

        var adminEntities = await db.Users
            .AsNoTracking()
            .Where(u => u.Role.ToLower() == "admin")
            .ToListAsync();

        Admins = adminEntities
            .Select(u => new AdminRow(
                u.Id,
                u.FullName,
                u.Email,
                u.CreatedAt,
                string.Equals(
                    u.Email,
                    MainAdminEmail,
                    StringComparison.OrdinalIgnoreCase),
                GetAdminNumber(u.Email)))
            .OrderBy(a => a.SequenceNumber ?? int.MaxValue)
            .ThenBy(a => a.CreatedAt)
            .ToList();

        var nextNumber =
            preparedNextNumber ?? await GetNextAdminNumberAsync();

        NextAdminEmail = BuildAdminEmail(nextNumber);

        if (string.IsNullOrWhiteSpace(Input.FullName))
        {
            Input.FullName = $"Admin User {nextNumber}";
        }
    }

    private bool IsCurrentUserMainAdmin()
    {
        var email = User.FindFirst(ClaimTypes.Email)?.Value;

        return string.Equals(
            email,
            MainAdminEmail,
            StringComparison.OrdinalIgnoreCase);
    }

    private async Task<int> GetNextAdminNumberAsync()
    {
        var adminEmails = await db.Users
            .AsNoTracking()
            .Where(u => u.Role.ToLower() == "admin")
            .Select(u => u.Email)
            .ToListAsync();

        var highestNumber = 1;

        foreach (var email in adminEmails)
        {
            var number = GetAdminNumber(email);

            if (number.HasValue)
            {
                highestNumber = Math.Max(
                    highestNumber,
                    number.Value);
            }
        }

        return highestNumber + 1;
    }

    private static int? GetAdminNumber(string email)
    {
        if (string.Equals(
                email,
                MainAdminEmail,
                StringComparison.OrdinalIgnoreCase))
        {
            return 1;
        }

        var match = Regex.Match(
            email.Trim(),
            @"^admin(?<number>\d+)@fyp\.com$",
            RegexOptions.IgnoreCase);

        if (!match.Success)
        {
            return null;
        }

        return int.TryParse(
            match.Groups["number"].Value,
            out var number)
                ? number
                : null;
    }

    private static string BuildAdminEmail(int number)
    {
        return number <= 1
            ? MainAdminEmail
            : $"admin{number}@fyp.com";
    }

    private void ValidatePasswordStrength()
    {
        var password = Input.Password ?? "";

        if (password.Length < 8)
        {
            return;
        }

        if (!password.Any(char.IsUpper) ||
            !password.Any(char.IsLower) ||
            !password.Any(char.IsDigit))
        {
            ModelState.AddModelError(
                "Input.Password",
                "The temporary password must contain an uppercase letter, a lowercase letter, and a number.");
        }
    }

    public sealed class CreateAdminInput
    {
        [Required]
        [StringLength(
            100,
            MinimumLength = 3,
            ErrorMessage = "The administrator name must contain at least 3 characters.")]
        [Display(Name = "Administrator Name")]
        public string FullName { get; set; } = "";

        [Required]
        [StringLength(
            100,
            MinimumLength = 8,
            ErrorMessage = "The temporary password must contain at least 8 characters.")]
        [DataType(DataType.Password)]
        [Display(Name = "Temporary Password")]
        public string Password { get; set; } = "";

        [Required]
        [DataType(DataType.Password)]
        [Compare(
            nameof(Password),
            ErrorMessage = "The temporary passwords do not match.")]
        [Display(Name = "Confirm Temporary Password")]
        public string ConfirmPassword { get; set; } = "";
    }

    public sealed record AdminRow(
        int Id,
        string FullName,
        string Email,
        DateTime CreatedAt,
        bool IsMainAdmin,
        int? SequenceNumber);
}