using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/auth")]
public class AuthController(ApplicationDbContext db, ITokenService tokenService) : ControllerBase
{
    [HttpPost("login")]
    public async Task<IActionResult> Login([FromBody] LoginRequest request)
    {
        var user = await db.Users.FirstOrDefaultAsync(u => u.Email == request.Email);
        if (user == null || !BCrypt.Net.BCrypt.Verify(request.Password, user.PasswordHash))
            return Unauthorized(new { error = "Invalid email or password" });

        var token = tokenService.GenerateToken(user);
        return Ok(new AuthResponse(token, new UserDto(user.Id, user.Email, user.FullName, user.Role)));
    }

    [HttpPost("register")]
    public async Task<IActionResult> Register(
       [FromBody] RegisterRequest request)
    {
        var email = request.Email.Trim().ToLowerInvariant();
        var fullName = request.FullName.Trim();
        var role = request.Role.Trim().ToLowerInvariant();

        var emailExists = await db.Users
            .AnyAsync(u => u.Email.ToLower() == email);

        if (emailExists)
        {
            return Conflict(new
            {
                error = "Email already in use"
            });
        }

        // Public API registration can create only students or supervisors.
        // Administrators are created only from the protected
        // Manage Administrators page.
        var validRoles = new[]
        {
        "student",
        "supervisor"
    };

        if (!validRoles.Contains(role))
        {
            return BadRequest(new
            {
                error = "Invalid role"
            });
        }
        if (string.IsNullOrWhiteSpace(request.Password) ||
              request.Password.Length < 8)
        {
            return BadRequest(new
            {
                error = "Password must contain at least 8 characters."
            });
        }
        var user = new User
        {
            Email = email,
            FullName = fullName,
            PasswordHash =
                BCrypt.Net.BCrypt.HashPassword(request.Password),
            Role = role
        };

        db.Users.Add(user);
        await db.SaveChangesAsync();

        if (role == "student")
        {
            db.StudentProfiles.Add(new StudentProfile
            {
                UserId = user.Id
            });
        }
        else
        {
            db.SupervisorProfiles.Add(new SupervisorProfile
            {
                UserId = user.Id
            });
        }

        await db.SaveChangesAsync();

        var token = tokenService.GenerateToken(user);

        return StatusCode(
            StatusCodes.Status201Created,
            new AuthResponse(
                token,
                new UserDto(
                    user.Id,
                    user.Email,
                    user.FullName,
                    user.Role)));
    }

    [HttpGet("me")]
    public async Task<IActionResult> Me()
    {
        var userId = GetUserId();
        if (userId == null) return Unauthorized();

        var user = await db.Users.FindAsync(userId);
        if (user == null) return Unauthorized();

        return Ok(new UserDto(user.Id, user.Email, user.FullName, user.Role));
    }

    private int? GetUserId()
    {
        var claim = User.FindFirst("userId")?.Value;
        return int.TryParse(claim, out var id) ? id : null;
    }
}
