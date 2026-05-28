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
        [Required] [EmailAddress] public string Email    { get; set; } = "";
        [Required]                public string Password { get; set; } = "";
    }

    public IActionResult OnGet(string? returnUrl = null)
    {
        ReturnUrl = returnUrl;
        if (User.Identity?.IsAuthenticated == true) return RedirectToDashboard();
        return Page();
    }

    public async Task<IActionResult> OnPostAsync(string? returnUrl = null)
    {
        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please enter email and password.";
            return Page();
        }

        var email = Input.Email.Trim().ToLowerInvariant();

        var user = await db.Users
            .FirstOrDefaultAsync(u => u.Email.ToLower() == email);

        if (user == null || !BCrypt.Net.BCrypt.Verify(Input.Password, user.PasswordHash))
        {
            ErrorMessage = "Invalid email or password.";
            return Page();
        }

        var claims = new List<Claim>
    {
        new(ClaimTypes.NameIdentifier, user.Id.ToString()),
        new(ClaimTypes.Email, user.Email),
        new(ClaimTypes.Name, user.FullName),
        new(ClaimTypes.Role, user.Role),
        new("userId", user.Id.ToString())
    };

        var identity = new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme);
        var principal = new ClaimsPrincipal(identity);

        await HttpContext.SignInAsync(
            CookieAuthenticationDefaults.AuthenticationScheme,
            principal,
            new AuthenticationProperties { IsPersistent = true }
        );

        if (!string.IsNullOrEmpty(returnUrl) && Url.IsLocalUrl(returnUrl))
        {
            return Redirect(returnUrl);
        }

        return RedirectToDashboard(user.Role);
    }

    private IActionResult RedirectToDashboard(string? role = null)
    {
        role ??= User.FindFirst(ClaimTypes.Role)?.Value;
        return role switch
        {
            "supervisor" => RedirectToPage("/Supervisor/Dashboard"),
            "admin"      => RedirectToPage("/Admin/Dashboard"),
            _            => RedirectToPage("/Student/Dashboard"),
        };
    }
}
