using System.Text.RegularExpressions;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Web.Services.SupervisorRegistration;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

/// <summary>
/// Pre-account email verification for a Supervisor application. The
/// applicant has no User and no authentication cookie, so the
/// request is identified by a protected, time-limited token rather
/// than a raw database id (see ISupervisorApplicationTokenService).
/// </summary>
public partial class VerifySupervisorApplicationModel(
    ApplicationDbContext db,
    ISupervisorApplicationVerificationService verificationService,
    ISupervisorApplicationTokenService tokenService,
    ILogger<VerifySupervisorApplicationModel> logger)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public string? Token { get; set; }

    [BindProperty]
    public string Code { get; set; } = "";

    public string MaskedEmail { get; private set; } = "";

    public string? ErrorMessage { get; private set; }

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        if (User.Identity?.IsAuthenticated == true)
        {
            return RedirectToPage("/Index");
        }

        if (!tokenService.TryReadToken(
                Token,
                SupervisorApplicationTokenPurpose.VerifyEmail,
                out var requestId))
        {
            return RedirectToInvalidLink();
        }

        var request = await LoadRequestAsync(
            requestId,
            cancellationToken);

        if (request == null)
        {
            return RedirectToInvalidLink();
        }

        if (request.Status !=
            SupervisorRegistrationStatus.PendingEmail)
        {
            return RedirectForStatus(request);
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

        if (!tokenService.TryReadToken(
                Token,
                SupervisorApplicationTokenPurpose.VerifyEmail,
                out var requestId))
        {
            return RedirectToInvalidLink();
        }

        Code = (Code ?? "").Trim();

        if (!SixDigitCodeRegex().IsMatch(Code))
        {
            ErrorMessage =
                "Enter the six-digit code sent to your email.";

            if (!await PreparePageAsync(
                    requestId,
                    cancellationToken))
            {
                return RedirectToInvalidLink();
            }

            return Page();
        }

        VerifySupervisorCodeResult result;

        try
        {
            result = await verificationService.VerifyCodeAsync(
                requestId,
                Code,
                cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Supervisor application verification failed "
                + "unexpectedly for request {RequestId}.",
                requestId);

            ErrorMessage =
                "We could not verify the code right now. "
                + "Please try again.";

            if (!await PreparePageAsync(
                    requestId,
                    cancellationToken))
            {
                return RedirectToInvalidLink();
            }

            return Page();
        }

        switch (result.Status)
        {
            case VerifySupervisorCodeStatus.Verified:
            {
                TempData["Success"] =
                    "Your email was verified successfully. "
                    + "Please complete your Supervisor application.";

                var detailsToken = tokenService.CreateToken(
                    requestId,
                    SupervisorApplicationTokenPurpose
                        .SupervisorDetails);

                return RedirectToPage(
                    "/Account/SupervisorApplicationDetails",
                    new { token = detailsToken });
            }

            case VerifySupervisorCodeStatus.AlreadyVerified:
            {
                var detailsToken = tokenService.CreateToken(
                    requestId,
                    SupervisorApplicationTokenPurpose
                        .SupervisorDetails);

                return RedirectToPage(
                    "/Account/SupervisorApplicationDetails",
                    new { token = detailsToken });
            }

            case VerifySupervisorCodeStatus.InvalidCode:
                ErrorMessage =
                    result.RemainingAttempts == 1
                        ? "The code is incorrect. "
                          + "You have 1 attempt remaining."
                        : "The code is incorrect. "
                          + $"You have {result.RemainingAttempts} "
                          + "attempts remaining.";
                break;

            case VerifySupervisorCodeStatus.Expired:
                ErrorMessage =
                    "This verification code has expired. "
                    + "Request a new code.";
                break;

            case VerifySupervisorCodeStatus.Locked:
                ErrorMessage =
                    "This verification code can no longer be used "
                    + "because the maximum number of attempts was "
                    + "reached. Request a new code.";
                break;

            case VerifySupervisorCodeStatus.NoActiveCode:
                ErrorMessage =
                    "There is no active verification code. "
                    + "Request a new code.";
                break;

            case VerifySupervisorCodeStatus.RequestNotFound:
                return RedirectToInvalidLink();

            default:
                ErrorMessage =
                    "The code could not be verified. "
                    + "Please request a new code.";
                break;
        }

        Code = "";

        if (!await PreparePageAsync(
                requestId,
                cancellationToken))
        {
            return RedirectToInvalidLink();
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

        if (!tokenService.TryReadToken(
                Token,
                SupervisorApplicationTokenPurpose.VerifyEmail,
                out var requestId))
        {
            return RedirectToInvalidLink();
        }

        SendSupervisorVerificationResult result;

        try
        {
            result = await verificationService.SendCodeAsync(
                requestId,
                cancellationToken);
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Resending a supervisor application verification "
                + "code failed unexpectedly for request {RequestId}.",
                requestId);

            TempData["Error"] =
                "The verification email could not be sent right "
                + "now. Please try again.";

            return RedirectToPage(
                "/Account/VerifySupervisorApplication",
                new { token = Token });
        }

        switch (result.Status)
        {
            case SendSupervisorVerificationStatus.Sent:
                TempData["Success"] =
                    "A new six-digit verification code was sent "
                    + "to your email.";

                return RedirectToPage(
                    "/Account/VerifySupervisorApplication",
                    new { token = Token });

            case SendSupervisorVerificationStatus.CooldownActive:
                TempData["Info"] =
                    "A code was sent recently. "
                    + $"You can request another code in "
                    + $"{result.RetryAfterSeconds} seconds.";

                return RedirectToPage(
                    "/Account/VerifySupervisorApplication",
                    new { token = Token });

            case SendSupervisorVerificationStatus.AlreadyVerified:
            {
                var detailsToken = tokenService.CreateToken(
                    requestId,
                    SupervisorApplicationTokenPurpose
                        .SupervisorDetails);

                return RedirectToPage(
                    "/Account/SupervisorApplicationDetails",
                    new { token = detailsToken });
            }

            case SendSupervisorVerificationStatus.DeliveryFailed:
                TempData["Error"] =
                    "The email could not be delivered. "
                    + "Check the address and try again shortly.";

                return RedirectToPage(
                    "/Account/VerifySupervisorApplication",
                    new { token = Token });

            case SendSupervisorVerificationStatus.RequestNotFound:
                return RedirectToInvalidLink();

            default:
                TempData["Error"] =
                    "The verification code could not be sent. "
                    + "Please try again.";

                return RedirectToPage(
                    "/Account/VerifySupervisorApplication",
                    new { token = Token });
        }
    }

    private async Task<bool> PreparePageAsync(
        int requestId,
        CancellationToken cancellationToken)
    {
        var request = await LoadRequestAsync(
            requestId,
            cancellationToken);

        return request != null &&
            request.Status ==
                SupervisorRegistrationStatus.PendingEmail;
    }

    private async Task<SupervisorRegistrationRequest?>
        LoadRequestAsync(
            int requestId,
            CancellationToken cancellationToken)
    {
        var request = await db.SupervisorRegistrationRequests
            .AsNoTracking()
            .FirstOrDefaultAsync(
                item => item.Id == requestId,
                cancellationToken);

        if (request == null)
        {
            return null;
        }

        MaskedEmail = MaskEmail(request.Email);

        return request;
    }

    private IActionResult RedirectForStatus(
        SupervisorRegistrationRequest request)
    {
        if (request.Status ==
            SupervisorRegistrationStatus.AwaitingDetails)
        {
            var detailsToken = tokenService.CreateToken(
                request.Id,
                SupervisorApplicationTokenPurpose.SupervisorDetails);

            return RedirectToPage(
                "/Account/SupervisorApplicationDetails",
                new { token = detailsToken });
        }

        if (request.Status ==
            SupervisorRegistrationStatus.PendingAdmin)
        {
            return RedirectToPage(
                "/Account/SupervisorApplicationPending");
        }

        if (request.Status == SupervisorRegistrationStatus.Approved)
        {
            TempData["Success"] =
                "Your Supervisor application was approved. "
                + "Please sign in.";

            return RedirectToPage("/Account/Login");
        }

        TempData["Error"] =
            "This Supervisor application is no longer active.";

        return RedirectToPage("/Account/Register");
    }

    private IActionResult RedirectToInvalidLink()
    {
        TempData["Error"] =
            "This application link is invalid or has expired. "
            + "Please register again.";

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

        var visibleStart = localPart[..1];

        var hiddenLength =
            Math.Clamp(localPart.Length - 1, 3, 8);

        return visibleStart
            + new string('*', hiddenLength)
            + "@"
            + domain;
    }

    [GeneratedRegex(@"^\d{6}$")]
    private static partial Regex SixDigitCodeRegex();
}
