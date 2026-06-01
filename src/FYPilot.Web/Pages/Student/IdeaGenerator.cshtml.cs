using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class IdeaGeneratorModel(ApplicationDbContext db, IAiServiceClient aiServiceClient) : PageModel
{
    private const int IdeasPerView = 2;
    private const int IdeasPerBatch = 4;

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

        var aiRequest = BuildAiRequest(
            regenerate: false,
            previousIdeaTitles: new List<string>()
        );

        var aiResponse = await aiServiceClient.GenerateIdeasAsync(aiRequest);

        if (aiResponse == null || aiResponse.Ideas == null || !aiResponse.Ideas.Any())
        {
            ErrorMessage = "AI service could not generate ideas. Make sure the Python AI service is running.";
            return Page();
        }

        var entities = await SaveGeneratedIdeasAsync(userId, aiResponse.Ideas);

        HttpContext.Session.SetInt32("IdeaGroupIndex", 0);

        GeneratedIdeas = entities
            .OrderBy(i => i.Id)
            .Take(IdeasPerView)
            .ToList();

        var sourceText = aiResponse.LlmUsed
            ? "using the local AI model"
            : "using the AI fallback engine";

        SuccessMessage = $"{entities.Count} project idea(s) generated and saved successfully {sourceText}.";

        return Page();
    }

    public async Task<IActionResult> OnPostShuffleAsync()
    {
        var userId = UserId();

        var recentIdeasCount = await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .ThenByDescending(i => i.Id)
            .Take(IdeasPerBatch)
            .CountAsync();

        if (recentIdeasCount == 0)
        {
            TempData["Error"] = "Generate ideas first before shuffling.";
            return RedirectToPage();
        }

        var currentIndex = HttpContext.Session.GetInt32("IdeaGroupIndex") ?? 0;
        var maxGroups = Math.Max(1, (int)Math.Ceiling(recentIdeasCount / (double)IdeasPerView));
        var nextIndex = (currentIndex + 1) % maxGroups;

        HttpContext.Session.SetInt32("IdeaGroupIndex", nextIndex);

        TempData["Success"] = "Showing another group of generated ideas.";
        return RedirectToPage();
    }

    public async Task<IActionResult> OnPostRegenerateAsync()
    {
        var userId = UserId();

        await LoadProfileIntoInputAsync(userId);
        await LoadPageDataAsync(userId);

        if (!AssessedSkills.Any())
        {
            ErrorMessage = "Please complete your Skill Assessment before regenerating project ideas.";
            return Page();
        }

        var previousTitles = await db.ProjectIdeas
            .AsNoTracking()
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .ThenByDescending(i => i.Id)
            .Take(30)
            .Select(i => i.Title)
            .ToListAsync();

        var aiRequest = BuildAiRequest(
            regenerate: true,
            previousIdeaTitles: previousTitles
        );

        var aiResponse = await aiServiceClient.GenerateIdeasAsync(aiRequest);

        if (aiResponse == null || aiResponse.Ideas == null || !aiResponse.Ideas.Any())
        {
            ErrorMessage = "AI service could not regenerate ideas. Make sure the Python AI service is running.";
            return Page();
        }

        var entities = await SaveGeneratedIdeasAsync(userId, aiResponse.Ideas);

        HttpContext.Session.SetInt32("IdeaGroupIndex", 0);

        GeneratedIdeas = entities
            .OrderBy(i => i.Id)
            .Take(IdeasPerView)
            .ToList();

        var sourceText = aiResponse.LlmUsed
            ? "using the local AI model"
            : "using the AI fallback engine";

        SuccessMessage = $"{entities.Count} new project idea(s) regenerated successfully {sourceText}.";

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

    private GenerateIdeasRequest BuildAiRequest(bool regenerate, List<string> previousIdeaTitles)
    {
        return new GenerateIdeasRequest(
            Major: Input.Major.Trim(),
            ExperienceLevel: Input.ExperienceLevel.Trim().ToLowerInvariant(),
            PreferredDomain: Input.PreferredDomain.Trim(),
            TargetDifficulty: Input.TargetDifficulty.Trim().ToLowerInvariant(),
            PreferredStack: "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
            AvailableHoursPerWeek: Input.AvailableHours,
            TeamMembers: Input.TeamSize,
            ProjectGoals: "Build a useful final year project based on the student's skills and preferred domain.",
            Regenerate: regenerate,
            PreviousIdeaTitles: previousIdeaTitles,
            Skills: AssessedSkills
                .OrderByDescending(s => s.Rating)
                .Select(s => new GenerateIdeaSkillDto(
                    SkillName: s.SkillName,
                    Rating: Math.Clamp(s.Rating, 1, 5),
                    ProficiencyLevel: Math.Clamp(s.Rating, 1, 5)
                ))
                .ToList()
        );
    }

    private async Task<List<ProjectIdea>> SaveGeneratedIdeasAsync(int userId, List<GeneratedIdeaDto> ideas)
    {
        var entities = new List<ProjectIdea>();

        foreach (var idea in ideas.Take(IdeasPerBatch))
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
                DifficultyLevel = idea.DifficultyLevel.ToString(),
                InnovationScore = (int)Math.Round(idea.InnovationScore),
                FeasibilityScore = (int)Math.Round(idea.FeasibilityScore),
                MarketDemandScore = (int)Math.Round(idea.MarketDemandScore),
                ExpectedDurationWeeks = idea.ExpectedDurationWeeks,
                SupervisorCategory = idea.SupervisorCategory,
                DatasetNeeded = idea.DatasetNeeded,
                FinalDeliverables = idea.FinalDeliverables,
                Domain = idea.Domain,
                LebanesesSector = idea.LebaneseSector,
                IsSelected = false,
                CreatedAt = DateTime.UtcNow
            };

            db.ProjectIdeas.Add(entity);
            entities.Add(entity);
        }

        await db.SaveChangesAsync();

        return entities;
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

        var recentBatch = await db.ProjectIdeas
            .AsNoTracking()
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .ThenByDescending(i => i.Id)
            .Take(IdeasPerBatch)
            .ToListAsync();

        var orderedBatch = recentBatch
            .OrderBy(i => i.Id)
            .ToList();

        var groupIndex = HttpContext.Session.GetInt32("IdeaGroupIndex") ?? 0;
        var maxGroups = Math.Max(1, (int)Math.Ceiling(orderedBatch.Count / (double)IdeasPerView));

        if (groupIndex >= maxGroups)
        {
            groupIndex = 0;
            HttpContext.Session.SetInt32("IdeaGroupIndex", groupIndex);
        }

        GeneratedIdeas = orderedBatch
            .Skip(groupIndex * IdeasPerView)
            .Take(IdeasPerView)
            .ToList();
    }

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}