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
    public async Task<IActionResult> Register([FromBody] RegisterRequest request)
    {
        if (await db.Users.AnyAsync(u => u.Email == request.Email))
            return Conflict(new { error = "Email already in use" });

        var validRoles = new[] { "student", "supervisor", "admin" };
        if (!validRoles.Contains(request.Role))
            return BadRequest(new { error = "Invalid role" });

        var user = new User
        {
            Email = request.Email,
            FullName = request.FullName,
            PasswordHash = BCrypt.Net.BCrypt.HashPassword(request.Password),
            Role = request.Role,
        };
        db.Users.Add(user);
        await db.SaveChangesAsync();

        if (request.Role == "student")
            db.StudentProfiles.Add(new StudentProfile { UserId = user.Id });
        else if (request.Role == "supervisor")
            db.SupervisorProfiles.Add(new SupervisorProfile { UserId = user.Id });
        else if (request.Role == "admin")
            db.CompanyProfiles.Add(new CompanyProfile { UserId = user.Id });

        await db.SaveChangesAsync();

        var token = tokenService.GenerateToken(user);
        return StatusCode(201, new AuthResponse(token, new UserDto(user.Id, user.Email, user.FullName, user.Role)));
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
