using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using FYPilot.Infrastructure.Data;
using FYPilot.Application.DTOs;
using FYPilot.Domain.Entities;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Services;

namespace FYPilot.Api.Controllers;

[ApiController]
[Route("api/ideas")]
[Authorize]
public class IdeasController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);

    private static ProjectIdeaResponse MapIdea(ProjectIdea i) => new(
        i.Id, i.Title, i.ProblemStatement, i.TargetUsers, i.WhyUseful,
        i.LebaneseMarketRelevance, i.RequiredTechnologies, i.RequiredSkills,
        i.MissingSkills, i.DifficultyLevel, i.InnovationScore, i.FeasibilityScore,
        i.MarketDemandScore, i.ExpectedDurationWeeks, i.SupervisorCategory,
        i.DatasetNeeded, i.FinalDeliverables, i.Domain, i.LebanesesSector,
        i.IsSelected, i.CreatedAt
    );

    [HttpGet]
    public async Task<IActionResult> List()
    {
        var ideas = await db.ProjectIdeas.Where(i => i.UserId == UserId)
            .OrderByDescending(i => i.CreatedAt).ToListAsync();
        return Ok(ideas.Select(MapIdea));
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> Get(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        return Ok(MapIdea(idea));
    }

    [HttpGet("selected")]
    public async Task<IActionResult> GetSelected()
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == UserId && i.IsSelected);
        if (idea == null) return Ok(null);
        return Ok(MapIdea(idea));
    }

    [HttpPost("generate")]
    public async Task<IActionResult> Generate([FromBody] GenerateIdeasRequest request)
    {
        var userSkills = await db.StudentSkills.Where(s => s.UserId == UserId).ToListAsync();
        var profile = await db.StudentProfiles.FirstOrDefaultAsync(p => p.UserId == UserId);

        var major = request.Major ?? profile?.Major ?? "Computer Science";
        var level = request.ExperienceLevel ?? profile?.ExperienceLevel ?? "intermediate";
        var domain = request.PreferredDomain ?? profile?.PreferredDomain ?? "Web Development";
        var difficulty = request.TargetDifficulty ?? profile?.TargetDifficulty ?? "intermediate";
        var stack = request.PreferredStack ?? profile?.PreferredStack ?? "Web";
        var hours = request.AvailableHoursPerWeek ?? profile?.AvailableHoursPerWeek ?? 20;
        var team = request.TeamMembers ?? profile?.TeamMembers ?? 1;

        var skillNames = userSkills.Select(s => s.SkillName).ToList();
        if (request.Skills != null)
            skillNames = request.Skills.Select(s => s.SkillName).ToList();

        var ideas = IdeaGenerator.Generate(major, level, domain, difficulty, stack, hours, team, skillNames);

        var saved = new List<ProjectIdea>();
        foreach (var ideaData in ideas)
        {
            var idea = new ProjectIdea
            {
                UserId = UserId,
                Title = ideaData.Title,
                ProblemStatement = ideaData.ProblemStatement,
                TargetUsers = ideaData.TargetUsers,
                WhyUseful = ideaData.WhyUseful,
                LebaneseMarketRelevance = ideaData.LebaneseMarketRelevance,
                RequiredTechnologies = ideaData.RequiredTechnologies,
                RequiredSkills = ideaData.RequiredSkills,
                MissingSkills = ideaData.MissingSkills,
                DifficultyLevel = ideaData.DifficultyLevel,
                InnovationScore = ideaData.InnovationScore,
                FeasibilityScore = ideaData.FeasibilityScore,
                MarketDemandScore = ideaData.MarketDemandScore,
                ExpectedDurationWeeks = ideaData.ExpectedDurationWeeks,
                SupervisorCategory = ideaData.SupervisorCategory,
                DatasetNeeded = ideaData.DatasetNeeded,
                FinalDeliverables = ideaData.FinalDeliverables,
                Domain = ideaData.Domain,
                LebanesesSector = ideaData.LebanesesSector,
            };
            db.ProjectIdeas.Add(idea);
            saved.Add(idea);
        }
        await db.SaveChangesAsync();
        return Ok(saved.Select(MapIdea));
    }

    [HttpPost("{id}/select")]
    public async Task<IActionResult> Select(int id)
    {
        var existing = await db.ProjectIdeas.Where(i => i.UserId == UserId && i.IsSelected).ToListAsync();
        foreach (var e in existing) e.IsSelected = false;

        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        idea.IsSelected = true;
        await db.SaveChangesAsync();
        return Ok(MapIdea(idea));
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        db.ProjectIdeas.Remove(idea);
        await db.SaveChangesAsync();
        return NoContent();
    }

    [HttpGet("{id}/implementation-plan")]
    public async Task<IActionResult> ImplementationPlan(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        var plan = PlanGenerator.GenerateDotNetPlan(idea);
        return Ok(plan);
    }

    [HttpGet("{id}/data-science-plan")]
    public async Task<IActionResult> DataSciencePlan(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        var plan = PlanGenerator.GeneratePythonPlan(idea);
        return Ok(plan);
    }

    [HttpGet("{id}/documentation")]
    public async Task<IActionResult> Documentation(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        var doc = DocumentationGenerator.Generate(idea);
        return Ok(doc);
    }

    [HttpGet("{id}/presentation")]
    public async Task<IActionResult> Presentation(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();
        var pres = PresentationGenerator.Generate(idea);
        return Ok(pres);
    }

    [HttpGet("{id}/similarity")]
    public async Task<IActionResult> Similarity(int id)
    {
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == id && i.UserId == UserId);
        if (idea == null) return NotFound();

        var previous = await db.PreviousProjects.ToListAsync();
        var result = SimilarityChecker.Check(idea, previous);
        return Ok(result);
    }
}
