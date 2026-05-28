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
public class IdeaGeneratorModel(ApplicationDbContext db) : PageModel
{
    [BindProperty] public InputModel Input { get; set; } = new();
    public List<ProjectIdea> GeneratedIdeas { get; private set; } = [];

    public class InputModel
    {
        public string Major            { get; set; } = "Computer Science";
        public string ExperienceLevel  { get; set; } = "intermediate";
        public string? PreferredDomain { get; set; }
        public string TargetDifficulty { get; set; } = "intermediate";
        public int    AvailableHours   { get; set; } = 20;
        public int    TeamSize         { get; set; } = 1;
    }

    public async Task OnGetAsync()
    {
        GeneratedIdeas = await db.ProjectIdeas
            .Where(i => i.UserId == UserId())
            .OrderByDescending(i => i.CreatedAt).Take(6).ToListAsync();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId   = UserId();
        var skills   = await db.StudentSkills.Where(s => s.UserId == userId).Select(s => s.SkillName).ToListAsync();
        var rawIdeas = IdeaGenerator.Generate(
            Input.Major, Input.ExperienceLevel, Input.PreferredDomain ?? "General",
            Input.TargetDifficulty, "Any", Input.AvailableHours, Input.TeamSize, skills);

        var entities = new List<ProjectIdea>();
        foreach (var d in rawIdeas)
        {
            var entity = new ProjectIdea
            {
                UserId                  = userId,
                Title                   = d.Title,
                ProblemStatement        = d.ProblemStatement,
                TargetUsers             = d.TargetUsers,
                WhyUseful               = d.WhyUseful,
                LebaneseMarketRelevance = d.LebaneseMarketRelevance,
                RequiredTechnologies    = d.RequiredTechnologies,
                RequiredSkills          = d.RequiredSkills,
                MissingSkills           = d.MissingSkills,
                DifficultyLevel         = d.DifficultyLevel,
                InnovationScore         = d.InnovationScore,
                FeasibilityScore        = d.FeasibilityScore,
                MarketDemandScore       = d.MarketDemandScore,
                ExpectedDurationWeeks   = d.ExpectedDurationWeeks,
                SupervisorCategory      = d.SupervisorCategory,
                DatasetNeeded           = d.DatasetNeeded,
                FinalDeliverables       = d.FinalDeliverables,
                Domain                  = d.Domain,
                LebanesesSector         = d.LebanesesSector,
            };
            db.ProjectIdeas.Add(entity);
            entities.Add(entity);
        }
        await db.SaveChangesAsync();

        GeneratedIdeas = entities;
        TempData["Success"] = $"{entities.Count} idea(s) generated.";
        return Page();
    }

    public async Task<IActionResult> OnPostSelectAsync(int ideaId)
    {
        var userId = UserId();
        await db.ProjectIdeas.Where(i => i.UserId == userId)
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.IsSelected, false));
        await db.ProjectIdeas.Where(i => i.Id == ideaId && i.UserId == userId)
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.IsSelected, true));
        TempData["Success"] = "Project idea selected.";
        return RedirectToPage();
    }

    private int UserId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
