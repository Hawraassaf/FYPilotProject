using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class LoginModel(
    ApplicationDbContext db,
    IActiveProjectService activeProjectService)
    : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? ErrorMessage { get; private set; }

    public string? ReturnUrl { get; private set; }

    public class InputModel
    {
        [Required]
        [EmailAddress]
        public string Email { get; set; } = "";

        [Required]
        [DataType(DataType.Password)]
        public string Password { get; set; } = "";
    }

    public async Task<IActionResult> OnGetAsync(
        string? returnUrl = null,
        string? reason = null,
        CancellationToken cancellationToken = default)
    {
        ReturnUrl = returnUrl;

        ErrorMessage = reason?.Trim().ToLowerInvariant() switch
        {
            "account_deleted" =>
                "This administrator account has been permanently deleted.",

            "session_invalid" =>
                "Your previous session is no longer valid. Sign in again.",

            _ => null
        };

        if (User.Identity?.IsAuthenticated != true)
        {
            return Page();
        }

        var userIdValue = User.FindFirst(
            ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(userIdValue, out var userId))
        {
            await HttpContext.SignOutAsync(
                CookieAuthenticationDefaults.AuthenticationScheme);

            ErrorMessage =
                "Your previous session is no longer valid. Sign in again.";

            return Page();
        }

        var currentUser = await db.Users
            .AsNoTracking()
            .FirstOrDefaultAsync(
                user => user.Id == userId,
                cancellationToken);

        if (currentUser == null)
        {
            await HttpContext.SignOutAsync(
                CookieAuthenticationDefaults.AuthenticationScheme);

            ErrorMessage =
                "This administrator account has been permanently deleted.";

            return Page();
        }

        /*
         * Defense in depth:
         * an unverified account must not keep using an authentication
         * cookie that may have been created before email verification
         * was introduced or by an earlier registration flow.
         */
        if (!currentUser.IsEmailVerified)
        {
            await HttpContext.SignOutAsync(
                CookieAuthenticationDefaults.AuthenticationScheme);

            TempData["Info"] =
                "Verify your email address before signing in.";

            return RedirectToPage(
                "/Account/VerifyEmail",
                new
                {
                    userId = currentUser.Id
                });
        }

        if (currentUser.MustChangePassword)
        {
            return RedirectToPage(
                "/Account/ForceChangePassword");
        }

        return await RedirectAfterLoginAsync(
            currentUser.Id,
            currentUser.Role,
            cancellationToken);
    }

    public async Task<IActionResult> OnPostAsync(
        string? returnUrl = null,
        CancellationToken cancellationToken = default)
    {
        ReturnUrl = returnUrl;

        if (!ModelState.IsValid)
        {
            ErrorMessage =
                "Please enter a valid email and password.";

            return Page();
        }

        var email = Input.Email
            .Trim()
            .ToLowerInvariant();

        var user = await db.Users
            .FirstOrDefaultAsync(
                item => item.Email.ToLower() == email,
                cancellationToken);

        if (user == null ||
            !BCrypt.Net.BCrypt.Verify(
                Input.Password,
                user.PasswordHash))
        {
            ErrorMessage =
                "Invalid email or password.";

            return Page();
        }

        /*
         * The password is correct, but no authentication cookie may be
         * created until the owner has verified the registered mailbox.
         */
        if (!user.IsEmailVerified)
        {
            TempData["Info"] =
                "Verify your email address before signing in.";

            return RedirectToPage(
                "/Account/VerifyEmail",
                new
                {
                    userId = user.Id
                });
        }

        var claims = new List<Claim>
        {
            new(
                ClaimTypes.NameIdentifier,
                user.Id.ToString()),

            new(
                ClaimTypes.Email,
                user.Email),

            new(
                ClaimTypes.Name,
                user.FullName),

            new(
                ClaimTypes.Role,
                user.Role),

            new(
                "is_main_admin",
                user.IsMainAdmin
                    ? "true"
                    : "false"),

            new(
                "userId",
                user.Id.ToString())
        };

        var identity = new ClaimsIdentity(
            claims,
            CookieAuthenticationDefaults.AuthenticationScheme);

        var principal = new ClaimsPrincipal(identity);

        await HttpContext.SignInAsync(
            CookieAuthenticationDefaults.AuthenticationScheme,
            principal,
            new AuthenticationProperties
            {
                IsPersistent = true
            });

        if (user.MustChangePassword)
        {
            return RedirectToPage(
                "/Account/ForceChangePassword");
        }

        if (!string.IsNullOrWhiteSpace(returnUrl) &&
            Url.IsLocalUrl(returnUrl))
        {
            return LocalRedirect(returnUrl);
        }

        return await RedirectAfterLoginAsync(
            user.Id,
            user.Role,
            cancellationToken);
    }

    private async Task<IActionResult> RedirectAfterLoginAsync(
        int userId,
        string? role,
        CancellationToken cancellationToken)
    {
        var normalizedRole =
            role?.Trim().ToLowerInvariant();

        if (normalizedRole == "student")
        {
            var destination =
                await activeProjectService
                    .GetResumeDestinationAsync(
                        userId,
                        cancellationToken);

            if (destination != null)
            {
                return RedirectToPage(
                    destination.PageName,
                    new
                    {
                        projectId = destination.ProjectId
                    });
            }

            return RedirectToPage(
                "/Student/MyProjects");
        }

        if (normalizedRole == "supervisor")
        {
            return RedirectToPage(
                "/Supervisor/Dashboard");
        }

        if (normalizedRole == "admin")
        {
            return RedirectToPage(
                "/Admin/Dashboard");
        }

        await HttpContext.SignOutAsync(
            CookieAuthenticationDefaults.AuthenticationScheme);

        ErrorMessage =
            "Your account role could not be recognized.";

        return Page();
    }
}