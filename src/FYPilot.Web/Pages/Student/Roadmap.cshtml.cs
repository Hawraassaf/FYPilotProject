using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class RoadmapModel(ApplicationDbContext db) : PageModel
{
    public ProjectIdea?       Idea   { get; private set; }
    public List<RoadmapPhase> Phases { get; private set; } = [];

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = UserId();
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas.Where(i => i.UserId == userId).OrderByDescending(i => i.CreatedAt).FirstOrDefaultAsync();

        if (Idea == null) return;

        var roadmap = await db.ProjectRoadmaps
            .Include(r => r.Phases)
            .FirstOrDefaultAsync(r => r.IdeaId == Idea.Id && r.UserId == userId);

        Phases = roadmap?.Phases.OrderBy(p => p.PhaseNumber).ToList() ?? [];
    }

    public async Task<IActionResult> OnPostAsync(int ideaId)
    {
        var userId = UserId();
        Idea = await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId);
        if (Idea == null) return RedirectToPage();

        // Remove existing roadmap if any
        var existing = db.ProjectRoadmaps.Where(r => r.IdeaId == ideaId && r.UserId == userId);
        db.ProjectRoadmaps.RemoveRange(existing);

        var phases  = RoadmapGenerator.Generate(Idea);
        var roadmap = new ProjectRoadmap { IdeaId = ideaId, UserId = userId, Phases = phases };
        db.ProjectRoadmaps.Add(roadmap);
        await db.SaveChangesAsync();

        Phases = phases.OrderBy(p => p.PhaseNumber).ToList();
        TempData["Success"] = $"Roadmap with {phases.Count} phases generated.";
        return RedirectToPage(new { ideaId });
    }

    public async Task<IActionResult> OnPostCompleteAsync(int phaseId)
    {
        await db.RoadmapPhases.Where(p => p.Id == phaseId)
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.IsCompleted, true));
        TempData["Success"] = "Phase marked as completed.";
        return RedirectToPage();
    }

    private int UserId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
