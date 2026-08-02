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

[Authorize(Policy = "MainAdminOnly")]
public class AdminAccountsModel(
    ApplicationDbContext db,
    ILogger<AdminAccountsModel> logger)
    : PageModel
{
    private const string MainAdminEmail =
        "admin@fyp.com";

    /*
     * Historical admin-authored records are preserved through
     * nullable foreign keys and SetNull relationships.
     */
    private static bool PermanentDeletionDatabaseReady =>
        true;

    [BindProperty]
    public CreateAdminInput Input { get; set; } =
        new();

    [BindProperty]
    public DeleteAdminInput DeleteInput { get; set; } =
        new();

    public List<AdminRow> Admins { get; private set; } =
        [];

    public string NextAdminEmail { get; private set; } =
        "";

    public bool IsMainAdminAccount { get; private set; }

    public bool PermanentDeletionReady =>
        PermanentDeletionDatabaseReady;

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        if (!await IsCurrentUserMainAdminAsync(
                cancellationToken))
        {
            return Forbid();
        }

        await LoadAsync(
            cancellationToken: cancellationToken);

        return Page();
    }

    public async Task<IActionResult> OnPostCreateAsync(
        CancellationToken cancellationToken)
    {
        if (!await IsCurrentUserMainAdminAsync(
                cancellationToken))
        {
            return Forbid();
        }

        /*
         * The page contains two independent bound forms.
         * Model binding validates both before the handler runs, so validate
         * only CreateAdminInput for this Create request.
         */
        ModelState.Clear();

        TryValidateModel(
            Input,
            nameof(Input));

        ValidatePasswordStrength();

        var nextNumber =
            await GetNextAdminNumberAsync(
                cancellationToken);

        var generatedEmail =
            BuildAdminEmail(nextNumber);

        if (!ModelState.IsValid)
        {
            await LoadAsync(
                nextNumber,
                cancellationToken);

            return Page();
        }

        var fullName =
            Input.FullName.Trim();

        var normalizedGeneratedEmail =
            generatedEmail.ToLowerInvariant();

        var emailExists =
            await db.Users.AnyAsync(
                user =>
                    user.Email.ToLower() ==
                        normalizedGeneratedEmail,
                cancellationToken);

        if (emailExists)
        {
            ModelState.AddModelError(
                string.Empty,
                "The generated administrator email is "
                + "already in use. Refresh the page and "
                + "try again.");

            await LoadAsync(
                cancellationToken:
                    cancellationToken);

            return Page();
        }

        var administrator =
            new User
            {
                FullName =
                    fullName,

                Email =
                    generatedEmail,

                /*
                 * This password is only the temporary
                 * first-login password.
                 */
                PasswordHash =
                    BCrypt.Net.BCrypt.HashPassword(
                        Input.Password),

                Role =
                    "admin",

                CreatedAt =
                    DateTime.UtcNow,

                /*
                 * The new administrator must change the
                 * temporary password before accessing
                 * administrator pages.
                 */
                MustChangePassword =
                    true,

                PasswordChangedAtUtc =
                    null,

                IsMainAdmin =
                    false
            };

        try
        {
            db.Users.Add(
                administrator);

            await db.SaveChangesAsync(
                cancellationToken);

            SuccessMessage =
                $"Administrator {generatedEmail} was "
                + "created successfully. They must "
                + "change their temporary password "
                + "during their first login.";

            return RedirectToPage();
        }
        catch (DbUpdateException exception)
        {
            logger.LogWarning(
                exception,
                "Could not create generated administrator "
                + "account {Email}.",
                generatedEmail);

            ErrorMessage =
                "The administrator could not be created. "
                + "The generated email may already be "
                + "in use.";

            return RedirectToPage();
        }
    }

    public async Task<IActionResult> OnPostDeleteAsync(
        CancellationToken cancellationToken)
    {
        /*
         * Never trust only the visible button or the
         * authentication cookie. Re-check the current
         * main administrator directly in the database.
         */
        if (!await IsCurrentUserMainAdminAsync(
                cancellationToken))
        {
            return Forbid();
        }

        /*
         * Ignore validation errors for CreateAdminInput,
         * because the delete modal posts only DeleteInput.
         */
        ModelState.Clear();

        if (!TryValidateModel(
                DeleteInput,
                nameof(DeleteInput)))
        {
            ErrorMessage =
                "Complete every deletion confirmation "
                + "field correctly.";

            return RedirectToPage();
        }

        var currentAdminId =
            CurrentUserId();

        if (!currentAdminId.HasValue)
        {
            return Forbid();
        }

        var currentAdmin =
            await db.Users
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    user =>
                        user.Id ==
                            currentAdminId.Value &&
                        user.Role.ToLower() ==
                            "admin",
                    cancellationToken);

        if (currentAdmin == null ||
            !currentAdmin.IsMainAdmin)
        {
            return Forbid();
        }

        var targetAdmin =
            await db.Users
                .FirstOrDefaultAsync(
                    user =>
                        user.Id ==
                            DeleteInput.AdminId,
                    cancellationToken);

        if (targetAdmin == null)
        {
            ErrorMessage =
                "The selected administrator no longer "
                + "exists.";

            return RedirectToPage();
        }

        if (!string.Equals(
                targetAdmin.Role,
                "admin",
                StringComparison.OrdinalIgnoreCase))
        {
            ErrorMessage =
                "Only secondary administrator accounts "
                + "can be deleted from this page.";

            return RedirectToPage();
        }

        if (targetAdmin.Id ==
            currentAdmin.Id)
        {
            ErrorMessage =
                "You cannot delete your own main "
                + "administrator account.";

            return RedirectToPage();
        }

        if (targetAdmin.IsMainAdmin)
        {
            ErrorMessage =
                "The main administrator account is "
                + "protected and cannot be deleted.";

            return RedirectToPage();
        }

        var confirmationEmail =
            DeleteInput.ConfirmationEmail
                .Trim();

        if (!string.Equals(
                confirmationEmail,
                targetAdmin.Email,
                StringComparison.OrdinalIgnoreCase))
        {
            ErrorMessage =
                "The confirmation email does not match "
                + "the selected administrator.";

            return RedirectToPage();
        }

        if (!DeleteInput.ConfirmPermanentDeletion)
        {
            ErrorMessage =
                "Confirm that you understand this action "
                + "permanently removes account access.";

            return RedirectToPage();
        }

        var passwordIsCorrect =
            BCrypt.Net.BCrypt.Verify(
                DeleteInput.CurrentPassword,
                currentAdmin.PasswordHash);

        if (!passwordIsCorrect)
        {
            ErrorMessage =
                "The main administrator password is "
                + "incorrect.";

            return RedirectToPage();
        }

        /*
         * The complete validation, authorization, UI,
         * transaction, and failure handling are ready.
         *
         * Actual deletion stays locked until User.cs,
         * ApplicationDbContext.cs, related nullable audit
         * foreign keys, and the migration are finalized.
         */
        if (!PermanentDeletionDatabaseReady)
        {
            ErrorMessage =
                "Permanent deletion is prepared safely, "
                + "but it is locked until the final "
                + "User.cs, ApplicationDbContext.cs, and "
                + "migration stage is completed.";

            return RedirectToPage();
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            /*
             * Once the final database rules are applied:
             *
             * - account-private rows are removed through
             *   safe cascade rules;
             * - historical business records remain;
             * - nullable audit references become null;
             * - the User row is permanently deleted.
             */
            db.Users.Remove(
                targetAdmin);

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);

            SuccessMessage =
                $"Administrator {targetAdmin.Email} was "
                + "permanently deleted and can no longer "
                + "sign in.";

            return RedirectToPage();
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Permanent deletion of administrator "
                + "{AdminId} ({Email}) was blocked by "
                + "database references.",
                targetAdmin.Id,
                targetAdmin.Email);

            ErrorMessage =
                "The account was not deleted because a "
                + "database relationship is still unsafe. "
                + "No partial deletion was committed.";

            return RedirectToPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            logger.LogError(
                exception,
                "Unexpected failure while deleting "
                + "administrator {AdminId} ({Email}).",
                targetAdmin.Id,
                targetAdmin.Email);

            ErrorMessage =
                "The administrator was not deleted. "
                + "No partial deletion was committed.";

            return RedirectToPage();
        }
    }

    private async Task LoadAsync(
        int? preparedNextNumber = null,
        CancellationToken cancellationToken = default)
    {
        IsMainAdminAccount =
            await IsCurrentUserMainAdminAsync(
                cancellationToken);

        var adminEntities =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    user.Role.ToLower() ==
                        "admin")
                .ToListAsync(
                    cancellationToken);

        Admins =
            adminEntities
                .Select(user =>
                    new AdminRow(
                        user.Id,
                        user.FullName,
                        user.Email,
                        user.CreatedAt,
                        user.IsMainAdmin,
                        GetAdminNumber(
                            user.Email)))
                .OrderBy(admin =>
                    admin.SequenceNumber ??
                    int.MaxValue)
                .ThenBy(admin =>
                    admin.CreatedAt)
                .ToList();

        var nextNumber =
            preparedNextNumber
            ?? await GetNextAdminNumberAsync(
                cancellationToken);

        NextAdminEmail =
            BuildAdminEmail(nextNumber);

        if (string.IsNullOrWhiteSpace(
                Input.FullName))
        {
            Input.FullName =
                $"Admin User {nextNumber}";
        }
    }

    private async Task<bool>
        IsCurrentUserMainAdminAsync(
            CancellationToken cancellationToken)
    {
        var currentUserId =
            CurrentUserId();

        if (!currentUserId.HasValue)
        {
            return false;
        }

        return await db.Users
            .AsNoTracking()
            .AnyAsync(
                user =>
                    user.Id ==
                        currentUserId.Value &&
                    user.Role.ToLower() ==
                        "admin" &&
                    user.IsMainAdmin,
                cancellationToken);
    }

    private int? CurrentUserId()
    {
        var value =
            User.FindFirst(
                ClaimTypes.NameIdentifier)
                ?.Value;

        return int.TryParse(
            value,
            out var userId)
                ? userId
                : null;
    }

    private async Task<int>
        GetNextAdminNumberAsync(
            CancellationToken cancellationToken)
    {
        var adminEmails =
            await db.Users
                .AsNoTracking()
                .Where(user =>
                    user.Role.ToLower() ==
                        "admin")
                .Select(user =>
                    user.Email)
                .ToListAsync(
                    cancellationToken);

        var highestNumber =
            1;

        foreach (var email in adminEmails)
        {
            var number =
                GetAdminNumber(email);

            if (number.HasValue)
            {
                highestNumber =
                    Math.Max(
                        highestNumber,
                        number.Value);
            }
        }

        return highestNumber + 1;
    }

    private static bool IsMainAdminEmail(
        string? email)
    {
        return string.Equals(
            email?.Trim(),
            MainAdminEmail,
            StringComparison.OrdinalIgnoreCase);
    }

    private static int? GetAdminNumber(
        string email)
    {
        if (IsMainAdminEmail(email))
        {
            return 1;
        }

        var match =
            Regex.Match(
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

    private static string BuildAdminEmail(
        int number)
    {
        return number <= 1
            ? MainAdminEmail
            : $"admin{number}@fyp.com";
    }

    private void ValidatePasswordStrength()
    {
        var password =
            Input.Password ?? "";

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
                "The temporary password must contain "
                + "an uppercase letter, a lowercase "
                + "letter, and a number.");
        }
    }

    public sealed class CreateAdminInput
    {
        [Required]
        [StringLength(
            100,
            MinimumLength = 3,
            ErrorMessage =
                "The administrator name must contain "
                + "at least 3 characters.")]
        [Display(Name = "Administrator Name")]
        public string FullName { get; set; } =
            "";

        [Required]
        [StringLength(
            100,
            MinimumLength = 8,
            ErrorMessage =
                "The temporary password must contain "
                + "at least 8 characters.")]
        [DataType(DataType.Password)]
        [Display(Name = "Temporary Password")]
        public string Password { get; set; } =
            "";

        [Required]
        [DataType(DataType.Password)]
        [Compare(
            nameof(Password),
            ErrorMessage =
                "The temporary passwords do not match.")]
        [Display(Name = "Confirm Temporary Password")]
        public string ConfirmPassword { get; set; } =
            "";
    }

    public sealed class DeleteAdminInput
    {
        [Range(
            1,
            int.MaxValue,
            ErrorMessage =
                "Select a valid administrator.")]
        public int AdminId { get; set; }

        [Required(
            ErrorMessage =
                "Enter the administrator email "
                + "to confirm deletion.")]
        [EmailAddress(
            ErrorMessage =
                "Enter a valid administrator email.")]
        [Display(Name = "Confirmation Email")]
        public string ConfirmationEmail { get; set; } =
            "";

        [Required(
            ErrorMessage =
                "Enter the main administrator password.")]
        [DataType(DataType.Password)]
        [Display(Name = "Main Administrator Password")]
        public string CurrentPassword { get; set; } =
            "";

        public bool ConfirmPermanentDeletion
        {
            get;
            set;
        }
    }

    public sealed record AdminRow(
        int Id,
        string FullName,
        string Email,
        DateTime CreatedAt,
        bool IsMainAdmin,
        int? SequenceNumber);
}