using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class LoginModel(ApplicationDbContext db) : PageModel
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
        string? returnUrl = null)
    {
        ReturnUrl = returnUrl;

        if (User.Identity?.IsAuthenticated != true)
        {
            return Page();
        }

        var userIdValue =
            User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdValue, out var userId))
        {
            var currentUser = await db.Users
                .AsNoTracking()
                .FirstOrDefaultAsync(u => u.Id == userId);

            if (currentUser?.MustChangePassword == true)
            {
                return RedirectToPage(
                    "/Account/ForceChangePassword");
            }

            return RedirectToDashboard(currentUser?.Role);
        }

        return RedirectToDashboard();
    }

    public async Task<IActionResult> OnPostAsync(
        string? returnUrl = null)
    {
        ReturnUrl = returnUrl;

        if (!ModelState.IsValid)
        {
            ErrorMessage =
                "Please enter a valid email and password.";

            return Page();
        }

        var email =
            Input.Email.Trim().ToLowerInvariant();

        var user = await db.Users
            .FirstOrDefaultAsync(
                u => u.Email.ToLower() == email);

        if (user == null ||
            !BCrypt.Net.BCrypt.Verify(
                Input.Password,
                user.PasswordHash))
        {
            ErrorMessage = "Invalid email or password.";
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
            CookieAuthenticationDefaults.AuthenticationScheme);

        var principal = new ClaimsPrincipal(identity);

        await HttpContext.SignInAsync(
            CookieAuthenticationDefaults.AuthenticationScheme,
            principal,
            new AuthenticationProperties
            {
                IsPersistent = true
            });

        /*
         * A newly created administrator is redirected immediately
         * to the forced password-change page.
         *
         * This check must happen before processing returnUrl so the
         * administrator cannot bypass the password-change screen.
         */
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

        return RedirectToDashboard(user.Role);
    }

    private IActionResult RedirectToDashboard(
        string? role = null)
    {
        role ??=
            User.FindFirst(ClaimTypes.Role)?.Value;

        return role?.ToLowerInvariant() switch
        {
            "supervisor" =>
                RedirectToPage("/Supervisor/Dashboard"),

            "admin" =>
                RedirectToPage("/Admin/Dashboard"),

            _ =>
                RedirectToPage("/Student/Dashboard")
        };
    }
}