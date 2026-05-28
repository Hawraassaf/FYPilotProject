using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/feasibility")]
[Authorize]
public class FeasibilityController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    [HttpGet("{ideaId}")]
    public async Task<IActionResult> Get(int ideaId)
    {
        var report = await db.FeasibilityReports.FirstOrDefaultAsync(f => f.IdeaId == ideaId);
        if (report == null) return Ok(null);
        return Ok(MapReport(report));
    }

    [HttpPost("{ideaId}/analyze")]
    public async Task<IActionResult> Analyze(int ideaId)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == UserId);
        if (idea == null) return NotFound();

        var skills = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        var profile = await db.StudentProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);

        var existing = await db.FeasibilityReports.FirstOrDefaultAsync(f => f.IdeaId == ideaId);
        if (existing != null) db.FeasibilityReports.Remove(existing);

        var report = FeasibilityAnalyzer.Analyze(idea, skills, profile);
        report.IdeaId = ideaId;
        report.UserId = UserId;
        db.FeasibilityReports.Add(report);
        await db.SaveChangesAsync();

        return Ok(MapReport(report));
    }

    private static FeasibilityReportResponse MapReport(FeasibilityReport r)
    {
        var risks = System.Text.Json.JsonSerializer.Deserialize<List<RiskItem>>(r.RisksJson) ?? [];
        return new FeasibilityReportResponse(
            r.Id, r.IdeaId, r.SkillMatchScore, r.DifficultyMatchScore,
            r.TimelineFitScore, r.MarketUsefulnessScore, r.InnovationScore,
            r.RiskScore, r.FinalFeasibilityScore, r.Explanation, risks
        );
    }
}
