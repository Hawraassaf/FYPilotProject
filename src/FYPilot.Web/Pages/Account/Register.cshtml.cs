using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Account;

public class RegisterModel(ApplicationDbContext db) : PageModel
{
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public string? ErrorMessage { get; private set; }

    public class InputModel
    {
        [Required] public string FullName { get; set; } = "";
        [Required] [EmailAddress] public string Email { get; set; } = "";
        [Required] [MinLength(6)] public string Password { get; set; } = "";
        [Required] public string Role { get; set; } = "student";
    }

    public IActionResult OnGet()
    {
        if (User.Identity?.IsAuthenticated == true) return RedirectToPage("/Index");
        return Page();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please correct the errors below.";
            return Page();
        }

        var email = Input.Email.Trim().ToLowerInvariant();
        var fullName = Input.FullName.Trim();
        var role = Input.Role.Trim().ToLowerInvariant();

        var validRoles = new[] { "student", "supervisor" };
        if (!validRoles.Contains(role))
        {
            ErrorMessage = "Invalid role.";
            return Page();
        }

        var emailExists = await db.Users
            .AnyAsync(u => u.Email.ToLower() == email);

        if (emailExists)
        {
            ErrorMessage = "An account with this email already exists.";
            return Page();
        }

        var user = new User
        {
            Email = email,
            FullName = fullName,
            Role = role,
            PasswordHash = BCrypt.Net.BCrypt.HashPassword(Input.Password)
        };

        db.Users.Add(user);
        await db.SaveChangesAsync();

        if (role == "student")
        {
            db.StudentProfiles.Add(new StudentProfile
            {
                UserId = user.Id,
                Major = "Computer Science",
                Year = "3rd Year",
                ExperienceLevel = "beginner",
                AvailableHoursPerWeek = 20,
                TeamMembers = 1,
                TargetDifficulty = "intermediate"
            });
        }
        else if (role == "supervisor")
        {
            db.SupervisorProfiles.Add(new SupervisorProfile
            {
                UserId = user.Id,
                Department = "Computer Science"
            });
        }

        await db.SaveChangesAsync();

        var claims = new List<Claim>
    {
        new(ClaimTypes.NameIdentifier, user.Id.ToString()),
        new(ClaimTypes.Email, user.Email),
        new(ClaimTypes.Name, user.FullName),
        new(ClaimTypes.Role, user.Role),
        new("userId", user.Id.ToString())
    };

        var identity = new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme);

        await HttpContext.SignInAsync(
            CookieAuthenticationDefaults.AuthenticationScheme,
            new ClaimsPrincipal(identity)
        );

        TempData["Success"] = $"Welcome to FYPilot, {user.FullName}!";

        return user.Role == "supervisor"
            ? RedirectToPage("/Supervisor/Dashboard")
            : RedirectToPage("/Student/Dashboard");
    }
}
