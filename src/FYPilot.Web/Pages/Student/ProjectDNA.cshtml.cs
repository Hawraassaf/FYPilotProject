using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProjectDNAModel(ApplicationDbContext db) : PageModel
{
    public ProjectIdea?       Idea          { get; private set; }
    public List<StudentSkill> StudentSkills { get; private set; } = [];
    public List<(string Label, int Value, string Color)> DnaDimensions { get; private set; } = [];
    public List<RiskItem>     Risks         { get; private set; } = [];

    public record RiskItem(string Category, string Level, string Description, string Mitigation);

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas.Where(i => i.UserId == userId).OrderByDescending(i => i.CreatedAt).FirstOrDefaultAsync();

        if (Idea == null) return;

        StudentSkills = await db.StudentSkills.Where(s => s.UserId == userId).ToListAsync();

        DnaDimensions =
        [
            ("Innovation Score",      Idea.InnovationScore,                            "#3b82f6"),
            ("Feasibility Score",     Idea.FeasibilityScore,                           "#10b981"),
            ("Market Demand",         Idea.MarketDemandScore,                          "#f59e0b"),
            ("Academic Value",        75,                                               "#8b5cf6"),
            ("AI Complexity",         Idea.Domain.Contains("AI") ? 80 : 30,            "#06b6d4"),
            ("Data Dependency",       !string.IsNullOrEmpty(Idea.DatasetNeeded) ? 70 : 20, "#ef4444"),
            ("Deployment Complexity", Idea.DifficultyLevel == "advanced" ? 80 : 40,   "#6366f1"),
            ("Team Fit Score",        Math.Min(StudentSkills.Count * 8, 100),          "#14b8a6"),
        ];

        var required = Idea.RequiredSkills.Split(',').Select(s => s.Trim()).Where(s => s.Length > 0).ToList();
        var missing  = required.Where(rs => !StudentSkills.Any(ss => ss.SkillName.Contains(rs, StringComparison.OrdinalIgnoreCase))).ToList();

        if (missing.Count > 2)
            Risks.Add(new("Skill Gap", "high", $"Missing {missing.Count} skills: {string.Join(", ", missing.Take(3))}", "Take online courses or reduce scope"));
        else if (missing.Count > 0)
            Risks.Add(new("Skill Gap", "medium", $"Missing: {string.Join(", ", missing)}", "Allocate 2–3 weeks for skill acquisition"));

        if (Idea.DifficultyLevel == "advanced" && StudentSkills.Count < 5)
            Risks.Add(new("Complexity Risk", "high", "Advanced project with limited skill breadth", "Consider intermediate complexity first"));

        if (!string.IsNullOrEmpty(Idea.DatasetNeeded))
            Risks.Add(new("Data Risk", "medium", $"Dataset required: {Idea.DatasetNeeded}", "Identify and validate data source in week 1"));

        if (!Risks.Any())
            Risks.Add(new("Overall Risk", "low", "Project appears well-suited to your profile", "Proceed with confidence"));
    }
}
