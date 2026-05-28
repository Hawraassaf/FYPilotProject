using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/users")]
[Authorize]
public class UsersController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    [HttpGet("supervisors")]
    public async Task<IActionResult> ListSupervisors()
    {
        var supervisors = await db.Users
            .Where(u => u.Role == "supervisor")
            .Join(db.SupervisorProfiles, u => u.Id, p => p.UserId,
                (u, p) => new SupervisorListItem(u.Id, u.FullName, p.Department))
            .ToListAsync();
        return Ok(supervisors);
    }

    [HttpGet("student-profile")]
    public async Task<IActionResult> GetStudentProfile()
    {
        var profile = await db.StudentProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return Ok(null);
        return Ok(MapStudentProfile(profile));
    }

    [HttpPut("student-profile")]
    public async Task<IActionResult> UpdateStudentProfile([FromBody] UpdateStudentProfileRequest request)
    {
        var profile = await db.StudentProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return NotFound(new { error = "Profile not found" });

        if (request.University != null) profile.University = request.University;
        if (request.Major != null) profile.Major = request.Major;
        if (request.Year != null) profile.Year = request.Year;
        if (request.Skills != null) profile.Skills = request.Skills;
        if (request.Interests != null) profile.Interests = request.Interests;
        if (request.ExperienceLevel != null) profile.ExperienceLevel = request.ExperienceLevel;
        if (request.PreferredDomain != null) profile.PreferredDomain = request.PreferredDomain;
        if (request.PreferredStack != null) profile.PreferredStack = request.PreferredStack;
        if (request.AvailableHoursPerWeek.HasValue) profile.AvailableHoursPerWeek = request.AvailableHoursPerWeek.Value;
        if (request.TeamMembers.HasValue) profile.TeamMembers = request.TeamMembers.Value;
        if (request.TargetDifficulty != null) profile.TargetDifficulty = request.TargetDifficulty;
        if (request.ProjectGoals != null) profile.ProjectGoals = request.ProjectGoals;

        await db.SaveChangesAsync();
        return Ok(MapStudentProfile(profile));
    }

    [HttpGet("supervisor-profile")]
    public async Task<IActionResult> GetSupervisorProfile()
    {
        var profile = await db.SupervisorProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return Ok(null);
        var user = await db.Users.FindAsync(UserId);
        return Ok(new SupervisorProfileResponse(
            profile.Id, profile.UserId, user?.FullName ?? "", user?.Email ?? "",
            profile.Department, profile.Specialization, profile.Bio));
    }

    [HttpPut("supervisor-profile")]
    public async Task<IActionResult> UpdateSupervisorProfile([FromBody] UpdateSupervisorProfileRequest request)
    {
        var profile = await db.SupervisorProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return NotFound();
        if (request.Department != null) profile.Department = request.Department;
        if (request.Specialization != null) profile.Specialization = request.Specialization;
        if (request.Bio != null) profile.Bio = request.Bio;
        await db.SaveChangesAsync();
        var user = await db.Users.FindAsync(UserId);
        return Ok(new SupervisorProfileResponse(
            profile.Id, profile.UserId, user?.FullName ?? "", user?.Email ?? "",
            profile.Department, profile.Specialization, profile.Bio));
    }

    [HttpGet("company-profile")]
    public async Task<IActionResult> GetCompanyProfile()
    {
        var profile = await db.CompanyProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return Ok(null);
        var user = await db.Users.FindAsync(UserId);
        return Ok(new CompanyProfileResponse(
            profile.Id, profile.UserId, user?.FullName ?? "", user?.Email ?? "",
            profile.CompanyName, profile.Industry, profile.Description, profile.Website));
    }

    [HttpPut("company-profile")]
    public async Task<IActionResult> UpdateCompanyProfile([FromBody] UpdateCompanyProfileRequest request)
    {
        var profile = await db.CompanyProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);
        if (profile == null) return NotFound();
        if (request.CompanyName != null) profile.CompanyName = request.CompanyName;
        if (request.Industry != null) profile.Industry = request.Industry;
        if (request.Description != null) profile.Description = request.Description;
        if (request.Website != null) profile.Website = request.Website;
        await db.SaveChangesAsync();
        var user = await db.Users.FindAsync(UserId);
        return Ok(new CompanyProfileResponse(
            profile.Id, profile.UserId, user?.FullName ?? "", user?.Email ?? "",
            profile.CompanyName, profile.Industry, profile.Description, profile.Website));
    }

    private static StudentProfileResponse MapStudentProfile(StudentProfile p) => new(
        p.Id, p.UserId, p.University, p.Major, p.Year,
        p.ExperienceLevel, p.PreferredDomain, p.PreferredStack,
        p.AvailableHoursPerWeek, p.TeamMembers, p.TargetDifficulty,
        p.ProjectGoals, p.Interests, p.Skills
    );
}

public record UpdateSupervisorProfileRequest(string? Department, string? Specialization, string? Bio);
