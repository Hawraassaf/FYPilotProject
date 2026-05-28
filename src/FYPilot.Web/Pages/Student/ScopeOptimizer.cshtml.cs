using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ScopeOptimizerModel(ApplicationDbContext db) : PageModel
{
    public ProjectIdea?    Idea            { get; private set; }
    public List<RiskItem>  ScopeRisks      { get; private set; } = [];
    public string          MvpDescription  { get; private set; } = "";
    public List<string>    FeaturesToKeep  { get; private set; } = [];
    public List<string>    FeaturesToDefer { get; private set; } = [];
    public List<(string Phase, int Weeks, string Color)> Timeline { get; private set; } = [];

    public record RiskItem(string Category, string Level, string Description, string Fix);

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas.Where(i => i.UserId == userId).OrderByDescending(i => i.CreatedAt).FirstOrDefaultAsync();
        if (Idea == null) return;

        var skills = await db.StudentSkills.Where(s => s.UserId == userId).ToListAsync();

        // Scope risk analysis
        if (Idea.DifficultyLevel == "advanced")
            ScopeRisks.Add(new("Complexity Risk", "high", "Advanced scope may exceed semester timeline", "Reduce to intermediate complexity for the MVP"));
        if (skills.Count < 4)
            ScopeRisks.Add(new("Skill Gap Risk", "medium", "Limited skill breadth for full feature set", "Focus on 2-3 core features matching your strongest skills"));
        if (Idea.ExpectedDurationWeeks > 16)
            ScopeRisks.Add(new("Timeline Risk", "high", $"Estimated {Idea.ExpectedDurationWeeks} weeks exceeds typical FYP semester", "Scope down to a focused MVP deliverable"));
        if (Idea.Domain.Contains("AI") && !skills.Any(s => s.SkillName.Contains("Python") || s.SkillName.Contains("Machine Learning")))
            ScopeRisks.Add(new("AI Scope Risk", "medium", "AI components without supporting ML skills", "Use pre-built APIs (OpenAI, HuggingFace) instead of training models"));
        if (!ScopeRisks.Any())
            ScopeRisks.Add(new("Scope", "low", "Project scope appears manageable for the given timeline", "Proceed with the full feature set as planned"));

        // MVP recommendation
        var features = Idea.FinalDeliverables.Split(',').Select(f => f.Trim()).Where(f => f.Length > 0).ToList();
        if (!features.Any()) features = ["Core CRUD operations", "User authentication", "Basic dashboard", "API integration", "Reporting"];

        MvpDescription  = $"A working {Idea.Domain.ToLower()} system demonstrating the core problem-solution with authentication and basic analytics.";
        FeaturesToKeep  = features.Take(3).ToList();
        FeaturesToDefer = features.Skip(3).Take(3).ToList();
        if (!FeaturesToDefer.Any()) FeaturesToDefer = ["Advanced analytics", "Mobile version", "Third-party integrations"];

        // Optimised timeline
        var totalWeeks = Math.Min(Idea.ExpectedDurationWeeks, 16);
        Timeline =
        [
            ("Research & Design", totalWeeks / 4,     "#3b82f6"),
            ("Development",       totalWeeks / 2,     "#10b981"),
            ("Testing & Deploy",  totalWeeks / 4 + 1, "#f59e0b"),
        ];
    }
}
