using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/admin")]
[Authorize]
public class AdminController(ApplicationDbContext db) : ControllerBase
{
    private string UserRole => User.FindFirst("userRole")!.Value;

    [HttpGet("stats")]
    public async Task<IActionResult> Stats()
    {
        if (UserRole != "admin") return Forbid();
        var users = await db.Users.ToListAsync();
        var stats = new AdminStatsResponse(
            users.Count,
            users.Count(u => u.Role == "student"),
            users.Count(u => u.Role == "supervisor"),
            users.Count(u => u.Role == "admin"),
            await db.ProjectIdeas.CountAsync(),
            await db.ProjectIdeas.CountAsync(i => i.IsSelected),
            await db.ProjectRoadmaps.CountAsync()
        );
        return Ok(stats);
    }

    [HttpGet("users")]
    public async Task<IActionResult> Users()
    {
        if (UserRole != "admin") return Forbid();
        var users = await db.Users.Select(u => new { u.Id, u.Email, u.FullName, u.Role, u.CreatedAt }).ToListAsync();
        return Ok(users);
    }

    [HttpGet("ideas")]
    public async Task<IActionResult> Ideas()
    {
        if (UserRole != "admin") return Forbid();
        var ideas = await db.ProjectIdeas.Include(i => i.User)
            .Select(i => new { i.Id, i.Title, i.Domain, i.DifficultyLevel, i.IsSelected, i.FeasibilityScore, StudentName = i.User!.FullName, i.CreatedAt })
            .OrderByDescending(i => i.CreatedAt).ToListAsync();
        return Ok(ideas);
    }

    [HttpGet("market-needs")]
    [Authorize(Roles = "admin")]
    public async Task<IActionResult> MarketNeeds()
    {
        var needs = await db.MarketNeeds.ToListAsync();
        return Ok(needs);
    }
}
