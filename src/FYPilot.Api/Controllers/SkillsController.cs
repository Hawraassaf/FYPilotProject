using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/skills")]
[Authorize]
public class SkillsController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    private static readonly Dictionary<string, string> SkillDomains = new()
    {
        ["Python"] = "Data Science",
        ["Machine Learning"] = "Data Science",
        ["Data Analysis"] = "Data Science",
        ["JavaScript"] = "Web Development",
        ["HTML/CSS"] = "Web Development",
        ["APIs"] = "Web Development",
        ["C#"] = ".NET Development",
        ["Databases"] = ".NET Development",
        ["SQL"] = ".NET Development",
        ["Java"] = "Software Engineering",
        ["Git/GitHub"] = "Software Engineering",
        ["Documentation"] = "Software Engineering",
        ["UI/UX"] = "Design",
        ["Presentation"] = "Design",
        ["Cloud"] = "DevOps",
    };

    [HttpGet]
    public async Task<IActionResult> GetSkills()
    {
        var skills = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        // Return in frontend format: {skillName, proficiencyLevel}
        return Ok(skills.Select(s => new { skillName = s.SkillName, proficiencyLevel = s.Rating }));
    }

    [HttpPost("bulk")]
    [HttpPost("assess")]
    public async Task<IActionResult> Assess([FromBody] SkillAssessmentRequest request)
    {
        var existing = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        db.StudentSkills.RemoveRange(existing);

        foreach (var s in request.Skills)
        {
            db.StudentSkills.Add(new StudentSkill
            {
                UserId = UserId,
                SkillName = s.SkillName,
                Rating = Math.Clamp(s.EffectiveRating, 1, 5)
            });
        }
        await db.SaveChangesAsync();

        return Ok(new { message = "Skills saved", count = request.Skills.Count });
    }

    [HttpGet("assessment")]
    public async Task<IActionResult> GetAssessment()
    {
        var skills = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        if (!skills.Any()) return Ok(null);

        var skillDtos = skills.Select(s => new SkillRatingRequest(s.SkillName, s.Rating)).ToList();
        return Ok(CalculateAssessment(skillDtos));
    }

    private static SkillAssessmentResult CalculateAssessment(List<SkillRatingRequest> skills)
    {
        var total = skills.Sum(s => s.Rating);
        var maxPossible = skills.Count * 5;
        var totalScore = maxPossible > 0 ? (int)((double)total / maxPossible * 100) : 0;

        var domainScores = new Dictionary<string, List<int>>();
        foreach (var s in skills)
        {
            if (SkillDomains.TryGetValue(s.SkillName, out var domain))
            {
                if (!domainScores.ContainsKey(domain)) domainScores[domain] = [];
                domainScores[domain].Add(s.Rating);
            }
        }

        var domainAvgs = domainScores.ToDictionary(k => k.Key, v => v.Value.Average());
        var strongestDomain = domainAvgs.Any() ? domainAvgs.MaxBy(x => x.Value).Key : "General";
        var weakestDomain = domainAvgs.Any() ? domainAvgs.MinBy(x => x.Value).Key : "General";

        var missingSkills = skills.Where(s => s.Rating <= 2)
            .Select(s => s.SkillName).ToList();

        var complexity = totalScore >= 75 ? "Advanced" : totalScore >= 50 ? "Intermediate" : "Beginner";

        return new SkillAssessmentResult(skills, totalScore, strongestDomain, weakestDomain, missingSkills, complexity);
    }
}
