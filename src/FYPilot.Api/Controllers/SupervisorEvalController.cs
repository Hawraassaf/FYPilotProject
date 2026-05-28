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
[Route("api/evaluations")]
[Authorize]
public class SupervisorEvalController(ApplicationDbContext db) : ControllerBase
{
    private int UserId => int.Parse(User.FindFirst("userId")!.Value);
    private string UserRole => User.FindFirst("userRole")!.Value;

    [HttpGet]
    public async Task<IActionResult> List()
    {
        IQueryable<SupervisorEvaluation> query;
        if (UserRole == "supervisor")
            query = db.SupervisorEvaluations.Include(e => e.Idea).Include(e => e.Supervisor)
                .Where(e => e.SupervisorId == UserId);
        else
            query = db.SupervisorEvaluations.Include(e => e.Idea).Include(e => e.Supervisor)
                .Where(e => e.Idea!.UserId == UserId);

        var evals = await query.OrderByDescending(e => e.CreatedAt).ToListAsync();
        return Ok(evals.Select(MapEval));
    }

    [HttpGet("{id}")]
    public async Task<IActionResult> Get(int id)
    {
        var eval = await db.SupervisorEvaluations.Include(e => e.Idea).Include(e => e.Supervisor)
            .FirstOrDefaultAsync(e => e.Id == id);
        if (eval == null) return NotFound();
        return Ok(MapEval(eval));
    }

    [HttpPost("{ideaId}")]
    [Authorize]
    public async Task<IActionResult> Evaluate(int ideaId, [FromBody] SupervisorEvalRequest request)
    {
        if (UserRole != "supervisor") return Forbid();

        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId);
        if (idea == null) return NotFound();

        var previous = await db.PreviousProjects.ToListAsync();
        var similarity = SimilarityChecker.QuickSimilarity(idea.Title, previous);

        var existing = await db.SupervisorEvaluations
            .FirstOrDefaultAsync(e => e.IdeaId == ideaId && e.SupervisorId == UserId);

        if (existing != null)
        {
            existing.Status = request.Status;
            existing.Comment = request.Comment;
            existing.ImprovementSuggestions = request.ImprovementSuggestions;
            existing.SimilarityScore = similarity;
            existing.OriginalityScore = 100 - similarity;
            existing.UpdatedAt = DateTime.UtcNow;
        }
        else
        {
            db.SupervisorEvaluations.Add(new SupervisorEvaluation
            {
                IdeaId = ideaId,
                SupervisorId = UserId,
                Status = request.Status,
                Comment = request.Comment,
                ImprovementSuggestions = request.ImprovementSuggestions,
                SimilarityScore = similarity,
                OriginalityScore = 100 - similarity,
            });
        }
        await db.SaveChangesAsync();
        return Ok(new { message = "Evaluation saved" });
    }

    [HttpGet("pending")]
    public async Task<IActionResult> GetPending()
    {
        if (UserRole != "supervisor") return Forbid();

        var ideas = await db.ProjectIdeas
            .Include(i => i.User)
            .Include(i => i.FeasibilityReport)
            .Where(i => i.IsSelected)
            .ToListAsync();

        var evaluated = await db.SupervisorEvaluations
            .Where(e => e.SupervisorId == UserId)
            .Select(e => e.IdeaId).ToListAsync();

        var pending = ideas.Where(i => !evaluated.Contains(i.Id)).ToList();
        return Ok(pending.Select(i => new
        {
            i.Id, i.Title, i.Domain, i.DifficultyLevel,
            StudentName = i.User?.FullName ?? "Unknown",
            i.FeasibilityScore, i.InnovationScore, i.MarketDemandScore,
            i.CreatedAt
        }));
    }

    private static SupervisorEvalResponse MapEval(SupervisorEvaluation e) => new(
        e.Id, e.IdeaId, e.Idea?.Title ?? "", e.Supervisor?.FullName ?? "",
        e.Status, e.Comment, e.ImprovementSuggestions,
        e.SimilarityScore, e.OriginalityScore, e.CreatedAt
    );
}
