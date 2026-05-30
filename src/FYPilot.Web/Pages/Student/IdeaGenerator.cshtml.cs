using System.ComponentModel.DataAnnotations;
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
    [BindProperty]
    public InputModel Input { get; set; } = new();

    public List<ProjectIdea> GeneratedIdeas { get; private set; } = [];
    public List<StudentSkill> AssessedSkills { get; private set; } = [];

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    public bool HasAssessedSkills => AssessedSkills.Any();

    public class InputModel
    {
        [Required(ErrorMessage = "Major is required.")]
        [StringLength(100)]
        public string Major { get; set; } = "Computer Science";

        [Required(ErrorMessage = "Experience level is required.")]
        public string ExperienceLevel { get; set; } = "intermediate";

        [Required(ErrorMessage = "Preferred domain is required.")]
        public string PreferredDomain { get; set; } = "General Software System";

        [Required(ErrorMessage = "Target difficulty is required.")]
        public string TargetDifficulty { get; set; } = "intermediate";

        [Range(1, 60, ErrorMessage = "Available hours must be between 1 and 60.")]
        public int AvailableHours { get; set; } = 20;

        [Range(1, 6, ErrorMessage = "Team size must be between 1 and 6.")]
        public int TeamSize { get; set; } = 1;
    }

    public async Task OnGetAsync()
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        var userId = UserId();

        await LoadProfileIntoInputAsync(userId);
        await LoadPageDataAsync(userId);
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId = UserId();

        await LoadPageDataAsync(userId);

        if (!AssessedSkills.Any())
        {
            ErrorMessage = "Please complete your Skill Assessment before generating project ideas.";
            return Page();
        }

        if (!ModelState.IsValid)
        {
            ErrorMessage = "Please correct the generation inputs before continuing.";
            return Page();
        }

        /*
         * Later, when your friend's AI service is ready,
         * this is the place where .NET should send:
         * profile + preferences + skills with ratings.
         *
         * For now, we keep the local IdeaGenerator as a fallback
         * so the feature still works during demo.
         */

        var skillNames = AssessedSkills
            .OrderByDescending(s => s.Rating)
            .Select(s => s.SkillName)
            .ToList();

        var rawIdeas = IdeaGenerator.Generate(
            Input.Major.Trim(),
            Input.ExperienceLevel.Trim().ToLowerInvariant(),
            Input.PreferredDomain.Trim(),
            Input.TargetDifficulty.Trim().ToLowerInvariant(),
            "Any",
            Input.AvailableHours,
            Input.TeamSize,
            skillNames
        );

        if (rawIdeas == null || !rawIdeas.Any())
        {
            ErrorMessage = "No ideas were generated. Please adjust your inputs and try again.";
            return Page();
        }

        var entities = new List<ProjectIdea>();

        foreach (var idea in rawIdeas)
        {
            var entity = new ProjectIdea
            {
                UserId = userId,
                Title = idea.Title,
                ProblemStatement = idea.ProblemStatement,
                TargetUsers = idea.TargetUsers,
                WhyUseful = idea.WhyUseful,
                LebaneseMarketRelevance = idea.LebaneseMarketRelevance,
                RequiredTechnologies = idea.RequiredTechnologies,
                RequiredSkills = idea.RequiredSkills,
                MissingSkills = idea.MissingSkills,
                DifficultyLevel = idea.DifficultyLevel,
                InnovationScore = idea.InnovationScore,
                FeasibilityScore = idea.FeasibilityScore,
                MarketDemandScore = idea.MarketDemandScore,
                ExpectedDurationWeeks = idea.ExpectedDurationWeeks,
                SupervisorCategory = idea.SupervisorCategory,
                DatasetNeeded = idea.DatasetNeeded,
                FinalDeliverables = idea.FinalDeliverables,
                Domain = idea.Domain,
                LebanesesSector = idea.LebanesesSector,
                IsSelected = false,
                CreatedAt = DateTime.UtcNow
            };

            db.ProjectIdeas.Add(entity);
            entities.Add(entity);
        }

        await db.SaveChangesAsync();

        GeneratedIdeas = entities
            .OrderByDescending(i => i.CreatedAt)
            .ToList();

        SuccessMessage = $"{entities.Count} project idea(s) generated and saved successfully.";

        return Page();
    }

    public async Task<IActionResult> OnPostSelectAsync(int ideaId)
    {
        var userId = UserId();

        var ideaExists = await db.ProjectIdeas
            .AnyAsync(i => i.Id == ideaId && i.UserId == userId);

        if (!ideaExists)
        {
            TempData["Error"] = "The selected idea was not found or does not belong to your account.";
            return RedirectToPage();
        }

        await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.IsSelected, false));

        await db.ProjectIdeas
            .Where(i => i.Id == ideaId && i.UserId == userId)
            .ExecuteUpdateAsync(s => s.SetProperty(p => p.IsSelected, true));

        TempData["Success"] = "Project idea selected successfully.";
        return RedirectToPage();
    }

    private async Task LoadProfileIntoInputAsync(int userId)
    {
        var profile = await db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.UserId == userId);

        if (profile == null)
        {
            return;
        }

        Input = new InputModel
        {
            Major = string.IsNullOrWhiteSpace(profile.Major)
                ? "Computer Science"
                : profile.Major,

            ExperienceLevel = string.IsNullOrWhiteSpace(profile.ExperienceLevel)
                ? "intermediate"
                : profile.ExperienceLevel.ToLowerInvariant(),

            PreferredDomain = string.IsNullOrWhiteSpace(profile.PreferredDomain)
                ? "General Software System"
                : profile.PreferredDomain,

            TargetDifficulty = string.IsNullOrWhiteSpace(profile.TargetDifficulty)
                ? "intermediate"
                : profile.TargetDifficulty.ToLowerInvariant(),

            AvailableHours = profile.AvailableHoursPerWeek <= 0
                ? 20
                : profile.AvailableHoursPerWeek,

            TeamSize = profile.TeamMembers <= 0
                ? 1
                : profile.TeamMembers
        };
    }

    private async Task LoadPageDataAsync(int userId)
    {
        AssessedSkills = await db.StudentSkills
            .AsNoTracking()
            .Where(s => s.UserId == userId)
            .OrderByDescending(s => s.Rating)
            .ThenBy(s => s.SkillName)
            .ToListAsync();

        GeneratedIdeas = await db.ProjectIdeas
            .AsNoTracking()
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .Take(6)
            .ToListAsync();
    }

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}