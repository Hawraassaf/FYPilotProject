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
        CancellationToken cancellationToken = default)
    {
        ReturnUrl = returnUrl;

        if (User.Identity?.IsAuthenticated != true)
        {
            return Page();
        }

        var userIdValue = User.FindFirst(
            ClaimTypes.NameIdentifier)?.Value;

        if (!int.TryParse(
                userIdValue,
                out var userId))
        {
            await HttpContext.SignOutAsync(
                CookieAuthenticationDefaults
                    .AuthenticationScheme);

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
                CookieAuthenticationDefaults
                    .AuthenticationScheme);

            return Page();
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
                item =>
                    item.Email.ToLower() == email,
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
                "userId",
                user.Id.ToString())
        };

        var identity = new ClaimsIdentity(
            claims,
            CookieAuthenticationDefaults
                .AuthenticationScheme);

        var principal =
            new ClaimsPrincipal(identity);

        await HttpContext.SignInAsync(
            CookieAuthenticationDefaults
                .AuthenticationScheme,
            principal,
            new AuthenticationProperties
            {
                IsPersistent = true
            });

        /*
         * Administrators who still use the temporary password
         * cannot bypass the forced password-change page.
         */
        if (user.MustChangePassword)
        {
            return RedirectToPage(
                "/Account/ForceChangePassword");
        }

        /*
         * Preserve valid local return URLs.
         */
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

    private async Task<IActionResult>
        RedirectAfterLoginAsync(
            int userId,
            string? role,
            CancellationToken cancellationToken)
    {
        var normalizedRole =
            role?.Trim().ToLowerInvariant();

        if (normalizedRole == "student")
        {
            /*
             * Try to resume the student's last valid project.
             * The service checks membership again before returning
             * the destination.
             */
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
                        projectId =
                            destination.ProjectId
                    });
            }

            /*
             * A student without an active accessible project must
             * choose or create one first.
             */
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

        /*
         * Unknown roles are not sent to a student page.
         */
        await HttpContext.SignOutAsync(
            CookieAuthenticationDefaults
                .AuthenticationScheme);

        ErrorMessage =
            "Your account role could not be recognized.";

        return Page();
    }
}