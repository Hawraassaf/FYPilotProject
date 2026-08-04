using System.Text.RegularExpressions;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.EmailVerification;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public partial class VerifyEmailModel(
    ApplicationDbContext db,
    IEmailVerificationService emailVerificationService,
    ILogger<VerifyEmailModel> logger)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public int UserId { get; set; }

    [BindProperty]
    public string Code { get; set; } = "";

    public string MaskedEmail { get; private set; } = "";

    public string? ErrorMessage { get; private set; }

    public string? InformationMessage { get; private set; }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        var userState = await LoadUserStateAsync(
            cancellationToken);

        if (userState == null)
        {
            TempData["Error"] =
                "The verification request is invalid or no longer available.";

            return RedirectToPage("/Account/Register");
        }

        if (userState.IsEmailVerified)
        {
            TempData["Success"] =
                "Your email is already verified. You can sign in.";

            return RedirectToPage("/Account/Login");
        }

        return Page();
    }

    public async Task<IActionResult> OnPostVerifyAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        Code = (Code ?? "").Trim();

        if (!SixDigitCodeRegex().IsMatch(Code))
        {
            ErrorMessage =
                "Enter the six-digit code sent to your email.";

            if (!await PreparePageAsync(cancellationToken))
            {
                return RedirectToRegistration();
            }

            return Page();
        }

        VerifyEmailCodeResult result;

        try
        {
            result = await emailVerificationService
                .VerifyCodeAsync(
                    UserId,
                    Code,
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Email verification failed unexpectedly for user {UserId}.",
                UserId);

            ErrorMessage =
                "We could not verify the code right now. "
                + "Please try again.";

            if (!await PreparePageAsync(cancellationToken))
            {
                return RedirectToRegistration();
            }

            return Page();
        }

        switch (result.Status)
        {
            case VerifyEmailCodeStatus.Verified:
                TempData["Success"] =
                    "Your email was verified successfully. "
                    + "You can now sign in.";

                return RedirectToPage("/Account/Login");

            case VerifyEmailCodeStatus.AlreadyVerified:
                TempData["Success"] =
                    "Your email is already verified. You can sign in.";

                return RedirectToPage("/Account/Login");

            case VerifyEmailCodeStatus.InvalidCode:
                ErrorMessage =
                    result.RemainingAttempts == 1
                        ? "The code is incorrect. "
                          + "You have 1 attempt remaining."
                        : "The code is incorrect. "
                          + $"You have {result.RemainingAttempts} "
                          + "attempts remaining.";
                break;

            case VerifyEmailCodeStatus.Expired:
                ErrorMessage =
                    "This verification code has expired. "
                    + "Request a new code.";
                break;

            case VerifyEmailCodeStatus.Locked:
                ErrorMessage =
                    "This verification code can no longer be used "
                    + "because the maximum number of attempts was reached. "
                    + "Request a new code.";
                break;

            case VerifyEmailCodeStatus.NoActiveCode:
                ErrorMessage =
                    "There is no active verification code. "
                    + "Request a new code.";
                break;

            case VerifyEmailCodeStatus.UserNotFound:
                return RedirectToRegistration();

            default:
                ErrorMessage =
                    "The code could not be verified. "
                    + "Please request a new code.";
                break;
        }

        Code = "";

        if (!await PreparePageAsync(cancellationToken))
        {
            return RedirectToRegistration();
        }

        return Page();
    }

    public async Task<IActionResult> OnPostResendAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        SendEmailVerificationResult result;

        try
        {
            result = await emailVerificationService
                .SendCodeAsync(
                    UserId,
                    cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Resending an email verification code failed "
                + "unexpectedly for user {UserId}.",
                UserId);

            TempData["Error"] =
                "The verification email could not be sent right now. "
                + "Please try again.";

            return RedirectToPage(
                "/Account/VerifyEmail",
                new { userId = UserId });
        }

        switch (result.Status)
        {
            case SendEmailVerificationStatus.Sent:
                TempData["Success"] =
                    "A new six-digit verification code was sent "
                    + "to your email.";

                return RedirectToPage(
                    "/Account/VerifyEmail",
                    new { userId = UserId });

            case SendEmailVerificationStatus.CooldownActive:
                TempData["Info"] =
                    "A code was sent recently. "
                    + $"You can request another code in "
                    + $"{result.RetryAfterSeconds} seconds.";

                return RedirectToPage(
                    "/Account/VerifyEmail",
                    new { userId = UserId });

            case SendEmailVerificationStatus.AlreadyVerified:
                TempData["Success"] =
                    "Your email is already verified. You can sign in.";

                return RedirectToPage("/Account/Login");

            case SendEmailVerificationStatus.DeliveryFailed:
                TempData["Error"] =
                    "The email could not be delivered. "
                    + "Check the address and try again shortly.";

                return RedirectToPage(
                    "/Account/VerifyEmail",
                    new { userId = UserId });

            case SendEmailVerificationStatus.UserNotFound:
                return RedirectToRegistration();

            default:
                TempData["Error"] =
                    "The verification code could not be sent. "
                    + "Please try again.";

                return RedirectToPage(
                    "/Account/VerifyEmail",
                    new { userId = UserId });
        }
    }

    private async Task<bool> PreparePageAsync(
        CancellationToken cancellationToken)
    {
        var userState = await LoadUserStateAsync(
            cancellationToken);

        if (userState == null)
        {
            return false;
        }

        if (userState.IsEmailVerified)
        {
            TempData["Success"] =
                "Your email is already verified. You can sign in.";

            return false;
        }

        return true;
    }

    private async Task<UserVerificationState?>
        LoadUserStateAsync(
            CancellationToken cancellationToken)
    {
        if (UserId <= 0)
        {
            return null;
        }

        var user = await db.Users
            .AsNoTracking()
            .Where(item => item.Id == UserId)
            .Select(item => new
            {
                item.Email,
                item.IsEmailVerified
            })
            .FirstOrDefaultAsync(cancellationToken);

        if (user == null)
        {
            return null;
        }

        MaskedEmail = MaskEmail(user.Email);

        return new UserVerificationState(
            user.IsEmailVerified);
    }

    private IActionResult RedirectToRegistration()
    {
        TempData["Error"] =
            "The verification request is invalid or no longer available.";

        return RedirectToPage("/Account/Register");
    }

    private static string MaskEmail(string email)
    {
        if (string.IsNullOrWhiteSpace(email))
        {
            return "your email address";
        }

        var parts = email.Split(
            '@',
            2,
            StringSplitOptions.TrimEntries);

        if (parts.Length != 2 ||
            string.IsNullOrWhiteSpace(parts[0]) ||
            string.IsNullOrWhiteSpace(parts[1]))
        {
            return "your email address";
        }

        var localPart = parts[0];
        var domain = parts[1];

        var visibleStart =
            localPart[..1];

        var hiddenLength =
            Math.Clamp(localPart.Length - 1, 3, 8);

        return visibleStart
            + new string('*', hiddenLength)
            + "@"
            + domain;
    }

    [GeneratedRegex(@"^\d{6}$")]
    private static partial Regex SixDigitCodeRegex();

    private sealed record UserVerificationState(
        bool IsEmailVerified);
}