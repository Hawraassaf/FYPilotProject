using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/challenges")]
[Authorize]
public class ChallengesController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    private static ChallengeResponse Map(Challenge c, CompanyProfile? profile) => new(
        c.Id, c.Title, c.Description, c.RequiredSkills, c.DifficultyLevel,
        c.CompanyId,
        profile?.CompanyName ?? c.Company?.FullName ?? "Company",
        profile?.Industry ?? "Technology",
        c.CreatedAt.ToString("o")
    );

    [HttpGet]
    public async Task<IActionResult> List([FromQuery] string? difficulty, [FromQuery] int? companyId)
    {
        var challenges = await db.Challenges
            .Include(c => c.Company)
            .ToListAsync();

        if (!string.IsNullOrEmpty(difficulty))
            challenges = challenges.Where(c => c.DifficultyLevel == difficulty).ToList();
        if (companyId.HasValue)
            challenges = challenges.Where(c => c.CompanyId == companyId.Value).ToList();

        var companyIds = challenges.Select(c => c.CompanyId).Distinct().ToList();
        var profiles = await db.CompanyProfiles
            .Where(p => companyIds.Contains(p.UserId))
            .ToDictionaryAsync(p => p.UserId);

        return Ok(challenges.Select(c => Map(c, profiles.GetValueOrDefault(c.CompanyId))));
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateChallengeRequest request)
    {
        var challenge = new Challenge
        {
            Title = request.Title,
            Description = request.Description,
            RequiredSkills = request.RequiredSkills ?? "",
            DifficultyLevel = request.DifficultyLevel ?? "intermediate",
            CompanyId = UserId,
        };
        db.Challenges.Add(challenge);
        await db.SaveChangesAsync();

        var profile = await db.CompanyProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        return StatusCode(201, Map(challenge, profile));
    }

    [HttpPut("{id}")]
    public async Task<IActionResult> Update(int id, [FromBody] UpdateChallengeRequest request)
    {
        var challenge = await db.Challenges.FindAsync(id);
        if (challenge == null) return NotFound(new { error = "Challenge not found" });

        if (request.Title != null) challenge.Title = request.Title;
        if (request.Description != null) challenge.Description = request.Description;
        if (request.RequiredSkills != null) challenge.RequiredSkills = request.RequiredSkills;
        if (request.DifficultyLevel != null) challenge.DifficultyLevel = request.DifficultyLevel;

        await db.SaveChangesAsync();
        var profile = await db.CompanyProfiles.FirstOrDefaultAsync(p => p.UserId == challenge.CompanyId);
        return Ok(Map(challenge, profile));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id)
    {
        var challenge = await db.Challenges.FindAsync(id);
        if (challenge == null) return NotFound();
        db.Challenges.Remove(challenge);
        await db.SaveChangesAsync();
        return NoContent();
    }
}
