using System.ComponentModel.DataAnnotations;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.EmailVerification;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class RegisterModel(
    ApplicationDbContext db,
    IEmailVerificationService emailVerificationService,
    ILogger<RegisterModel> logger)
    : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? ErrorMessage { get; private set; }

    public class InputModel
    {
        [Required]
        public string FullName { get; set; } = "";

        [Required]
        [EmailAddress]
        public string Email { get; set; } = "";

        [Required]
        [DataType(DataType.Password)]
        [StringLength(
            100,
            MinimumLength = 8,
            ErrorMessage =
                "Password must contain at least 8 characters.")]
        public string Password { get; set; } = "";

        [Required]
        public string Role { get; set; } = "student";
    }

    public IActionResult OnGet()
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        return Page();
    }

    public async Task<IActionResult> OnPostAsync(
        CancellationToken cancellationToken)
    {
        if (!ModelState.IsValid)
        {
            ErrorMessage =
                "Please correct the errors below.";

            return Page();
        }

        var email = Input.Email
            .Trim()
            .ToLowerInvariant();

        var fullName =
            Input.FullName.Trim();

        var role = Input.Role
            .Trim()
            .ToLowerInvariant();

        var validRoles = new[]
        {
            "student",
            "supervisor"
        };

        if (!validRoles.Contains(role))
        {
            ErrorMessage = "Invalid role.";
            return Page();
        }

        /*
         * Do not create a duplicate row for an account that was
         * registered earlier but has not completed email verification.
         *
         * Sending a code to that address is safe because possession of
         * the mailbox is still required to finish registration.
         */
        var existingUser =
            await db.Users
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    user =>
                        user.Email.ToLower() == email,
                    cancellationToken);

        if (existingUser != null)
        {
            if (existingUser.IsEmailVerified)
            {
                ErrorMessage =
                    "An account with this email already exists. "
                    + "Please sign in instead.";

                return Page();
            }

            var resendResult =
                await TrySendVerificationCodeAsync(
                    existingUser.Id,
                    cancellationToken);

            SetVerificationRedirectMessage(
                resendResult,
                isNewAccount: false);

            return RedirectToPage(
                "/Account/VerifyEmail",
                new
                {
                    userId = existingUser.Id
                });
        }

        User? createdUser = null;

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                cancellationToken);

        try
        {
            createdUser = new User
            {
                Email = email,
                FullName = fullName,
                Role = role,
                PasswordHash =
                    BCrypt.Net.BCrypt.HashPassword(
                        Input.Password),

                /*
                 * Publicly registered accounts must prove ownership
                 * of their email before they can sign in.
                 */
                IsEmailVerified = false,
                EmailVerifiedAtUtc = null,
                IsMainAdmin = false,
                MustChangePassword = false
            };

            db.Users.Add(createdUser);

            /*
             * Save temporarily to generate the user ID.
             * The transaction prevents this save from becoming permanent
             * until the role-specific profile also saves.
             */
            await db.SaveChangesAsync(
                cancellationToken);

            if (role == "student")
            {
                db.StudentProfiles.Add(
                    new StudentProfile
                    {
                        UserId = createdUser.Id,
                        Major = "Computer Science",
                        Year = "3rd Year",
                        ExperienceLevel = "beginner",
                        AvailableHoursPerWeek = 20,
                        TeamMembers = 1,
                        TargetDifficulty = "intermediate"
                    });
            }
            else
            {
                db.SupervisorProfiles.Add(
                    new SupervisorProfile
                    {
                        UserId = createdUser.Id,
                        Department = "Computer Science",
                        Specialization =
                            "General Software Engineering",
                        ResearchAreas =
                            "Software Engineering, Web Development, AI/Data Science",
                        AcademicTitle = "Supervisor",
                        Faculty = "Computer Science",
                        University = "",
                        OfficeLocation = "",
                        OfficeHours = "",
                        PreferredMeetingMode = "Online",
                        Bio = "",
                        LinkedInUrl = "",
                        WebsiteUrl = "",
                        UpdatedAt = DateTime.UtcNow
                    });
            }

            await db.SaveChangesAsync(
                cancellationToken);

            await transaction.CommitAsync(
                cancellationToken);
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            db.ChangeTracker.Clear();

            logger.LogWarning(
                exception,
                "Registration database update failed for email {Email}.",
                email);

            ErrorMessage =
                "Registration could not be completed. "
                + "The email may already be registered. "
                + "Please try again.";

            return Page();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync(
                cancellationToken);

            db.ChangeTracker.Clear();

            logger.LogError(
                exception,
                "Registration failed for email {Email}.",
                email);

            ErrorMessage =
                "Registration could not be completed. "
                + "Please try again.";

            return Page();
        }

        /*
         * Send only after the user and profile transaction has committed.
         * EmailVerificationService owns its own transaction while marking
         * the code as sent and invalidating older codes.
         */
        var sendResult =
            await TrySendVerificationCodeAsync(
                createdUser.Id,
                cancellationToken);

        SetVerificationRedirectMessage(
            sendResult,
            isNewAccount: true);

        /*
         * Do not create an authentication cookie here. The account stays
         * signed out until VerifyEmail confirms the six-digit code.
         */
        return RedirectToPage(
            "/Account/VerifyEmail",
            new
            {
                userId = createdUser.Id
            });
    }

    private async Task<SendEmailVerificationResult>
        TrySendVerificationCodeAsync(
            int userId,
            CancellationToken cancellationToken)
    {
        try
        {
            return await emailVerificationService
                .SendCodeAsync(
                    userId,
                    cancellationToken);
        }
        catch (Exception exception)
        {
            /*
             * The account remains safely unverified. The Verify Email page
             * will let the user request another code after the problem is
             * resolved.
             */
            logger.LogError(
                exception,
                "Could not prepare an email verification code "
                + "for user {UserId}.",
                userId);

            return new SendEmailVerificationResult(
                SendEmailVerificationStatus.DeliveryFailed);
        }
    }

    private void SetVerificationRedirectMessage(
        SendEmailVerificationResult result,
        bool isNewAccount)
    {
        switch (result.Status)
        {
            case SendEmailVerificationStatus.Sent:
                TempData["Success"] =
                    isNewAccount
                        ? "Your account was created. "
                          + "We sent a six-digit verification code "
                          + "to your email."
                        : "We sent a new verification code "
                          + "to your email.";
                break;

            case SendEmailVerificationStatus.CooldownActive:
                TempData["Info"] =
                    "A verification code was already sent. "
                    + $"You can request another code in "
                    + $"{result.RetryAfterSeconds} seconds.";
                break;

            case SendEmailVerificationStatus.AlreadyVerified:
                TempData["Info"] =
                    "This email address is already verified. "
                    + "You can sign in.";
                break;

            case SendEmailVerificationStatus.DeliveryFailed:
                TempData["Error"] =
                    "Your account was saved, but the verification "
                    + "email could not be delivered. "
                    + "Use Resend Code on the verification page.";
                break;

            default:
                TempData["Error"] =
                    "The verification code could not be sent. "
                    + "Use Resend Code on the verification page.";
                break;
        }
    }
}