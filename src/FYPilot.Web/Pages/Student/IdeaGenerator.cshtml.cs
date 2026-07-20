using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using System.Text.Json;
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
public class IdeaGeneratorModel(
    ApplicationDbContext db,
    IAiServiceClient aiServiceClient,
    ILogger<IdeaGeneratorModel> logger) : PageModel
{
    private const int IdeasPerView = 2;
    private const int IdeasPerBatch = 4;

    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    [BindProperty]
    public InputModel Input { get; set; } = new();

    [BindProperty(SupportsGet = true)]
    public int? OpenIdeaId { get; set; }

    public List<ProjectIdea> GeneratedIdeas { get; private set; } = [];
    public List<StudentSkill> AssessedSkills { get; private set; } = [];

    /// <summary>Latest Regional Demand Footprint snapshot per visible idea (ProjectIdeaId -> snapshot).</summary>
    public Dictionary<int, MarketOpportunitySnapshot> LatestMarketInsights { get; private set; } = [];

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    public bool HasAssessedSkills => AssessedSkills.Any();

    public sealed record MarketFootprintSourceView(
        string Title,
        string Url,
        string Publisher,
        bool IsVerified);

    /// <summary>View helper: parses a snapshot/region's simple string-list JSON columns.</summary>
    public static List<string> ParseStringList(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];

        try
        {
            return JsonSerializer.Deserialize<List<string>>(json) ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }

    /// <summary>View helper: parses a snapshot's SourcesJson column for display.</summary>
    public static List<MarketFootprintSourceView> ParseSources(string? json)
    {
        if (string.IsNullOrWhiteSpace(json)) return [];

        try
        {
            using var document = JsonDocument.Parse(json);
            var results = new List<MarketFootprintSourceView>();

            foreach (var item in document.RootElement.EnumerateArray())
            {
                results.Add(new MarketFootprintSourceView(
                    item.TryGetProperty("title", out var title) ? title.GetString() ?? "" : "",
                    item.TryGetProperty("url", out var url) ? url.GetString() ?? "" : "",
                    item.TryGetProperty("publisher", out var publisher) ? publisher.GetString() ?? "" : "",
                    item.TryGetProperty("isVerified", out var verified) && verified.ValueKind == JsonValueKind.True));
            }

            return results;
        }
        catch (JsonException)
        {
            return [];
        }
    }

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

        await using var transaction =
            await db.Database.BeginTransactionAsync(
                System.Data.IsolationLevel.Serializable);

        try
        {
            var idea = await db.ProjectIdeas
                .FirstOrDefaultAsync(i =>
                    i.Id == ideaId &&
                    i.UserId == userId);

            if (idea == null)
            {
                TempData["Error"] =
                    "The selected idea was not found or does not belong to your account.";

                await transaction.RollbackAsync();

                return RedirectToPage();
            }

            /*
             * Check whether this idea already has a project.
             * This prevents duplicate project workspaces when the user
             * clicks Select Idea more than once.
             */
            var existingProject = await db.Projects
                .Include(project => project.Members)
                .FirstOrDefaultAsync(project =>
                    project.ProjectIdeaId == ideaId);

            if (existingProject != null)
            {
                if (existingProject.StudentId != userId)
                {
                    TempData["Error"] =
                        "This project workspace does not belong to your account.";

                    await transaction.RollbackAsync();

                    return RedirectToPage();
                }

                var alreadyActiveMember =
                    existingProject.Members.Any(member =>
                        member.UserId == userId &&
                        member.Status == "active");

                /*
                 * Repair an incomplete older project record when the project
                 * exists but its owner membership was not created.
                 */
                if (!alreadyActiveMember)
                {
                    existingProject.Members.Add(
                        new ProjectMember
                        {
                            UserId = userId,
                            Role = "owner",
                            Status = "active",
                            JoinedAt = DateTime.UtcNow
                        });
                }

                idea.IsSelected = true;
                existingProject.UpdatedAt = DateTime.UtcNow;

                await db.SaveChangesAsync();
                await transaction.CommitAsync();

                TempData["Success"] =
                    "This idea already has a project workspace. " +
                    "Your owner access was confirmed.";

                return RedirectToPage();
            }

            /*
             * Create one shared project workspace from the selected idea.
             */
            var project = new Project
            {
                ProjectIdeaId = idea.Id,

                // Temporary compatibility owner field.
                StudentId = userId,

                SupervisorId = null,

                Title = idea.Title,

                Description =
                    !string.IsNullOrWhiteSpace(idea.ProblemStatement)
                        ? idea.ProblemStatement
                        : idea.WhyUseful,

                Technologies = idea.RequiredTechnologies,

                Status = "planning",

                ProgressPercentage = 0,

                // One owner plus up to two collaborators.
                MaximumMembers = 3,

                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            /*
             * The project creator automatically becomes the first member
             * and owner of this specific project.
             */
            project.Members.Add(
                new ProjectMember
                {
                    UserId = userId,
                    Role = "owner",
                    Status = "active",
                    JoinedAt = DateTime.UtcNow
                });

            idea.IsSelected = true;

            db.Projects.Add(project);

            await db.SaveChangesAsync();
            await transaction.CommitAsync();

            TempData["Success"] =
                $"The project “{project.Title}” was created successfully. " +
                "You are now its owner.";

            return RedirectToPage();
        }
        catch (DbUpdateException exception)
        {
            await transaction.RollbackAsync();

            logger.LogError(
                exception,
                "Failed to create a project from idea {IdeaId} for user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "The project could not be created. Please try again.";

            return RedirectToPage();
        }
        catch (Exception exception)
        {
            await transaction.RollbackAsync();

            logger.LogError(
                exception,
                "Unexpected error while selecting idea {IdeaId} for user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "An unexpected error occurred while creating the project.";

            return RedirectToPage();
        }
    }

    /// <summary>
    /// Market Insight — Regional Demand Footprint. Runs only when the
    /// student explicitly clicks "Analyze Regional Demand" (never on page
    /// load), and reopens the same idea's accordion item after redirect.
    /// </summary>
    public async Task<IActionResult> OnPostAnalyzeMarketAsync(
        int ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        var idea = await db.ProjectIdeas
            .FirstOrDefaultAsync(
                i => i.Id == ideaId && i.UserId == userId,
                cancellationToken);

        if (idea == null)
        {
            // Idea does not exist or does not belong to this user — reject
            // without exposing whether the ID exists for someone else.
            TempData["Error"] =
                "The selected idea was not found or does not belong to your account.";

            return RedirectToPage();
        }

        var request = new MarketFootprintRequest(
            ProjectTitle: SafeText(idea.Title, 300),
            ProblemStatement: SafeText(idea.ProblemStatement, 5000),
            TargetUsers: SafeText(idea.TargetUsers, 2000),
            Domain: SafeText(idea.Domain, 200),
            Technologies: SafeText(idea.RequiredTechnologies, 2000),
            UseSearch: true);

        try
        {
            var response = await aiServiceClient.AnalyzeMarketFootprintAsync(
                request,
                cancellationToken);

            if (response == null)
            {
                TempData["Error"] =
                    "Regional evidence could not be verified right now. The " +
                    "preliminary idea-generation score is still available, but " +
                    "it is not a grounded regional analysis.";

                return RedirectToPage(new { openIdeaId = ideaId });
            }

            if (!string.Equals(response.Status, "ready", StringComparison.OrdinalIgnoreCase)
                || response.OverallOpportunityScore == null)
            {
                // Do not persist an empty/unavailable snapshot — leave any
                // previous valid snapshot in place and just report the issue.
                TempData["Error"] =
                    "Regional evidence could not be verified right now. The " +
                    "preliminary idea-generation score is still available, but " +
                    "it is not a grounded regional analysis.";

                return RedirectToPage(new { openIdeaId = ideaId });
            }

            var snapshot = BuildSnapshot(response, userId, ideaId);

            await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);

            db.MarketOpportunitySnapshots.Add(snapshot);
            await db.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);

            TempData["Success"] =
                $"Regional demand footprint updated — {snapshot.Regions.Count} region(s) analyzed " +
                $"with {response.Sources.Count} source(s).";

            return RedirectToPage(new { openIdeaId = ideaId });
        }
        catch (HttpRequestException exception)
        {
            logger.LogError(
                exception,
                "Market footprint request failed for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "Regional evidence could not be verified right now. The " +
                "preliminary idea-generation score is still available, but " +
                "it is not a grounded regional analysis.";
        }
        catch (InvalidOperationException exception)
        {
            logger.LogError(
                exception,
                "Market footprint response could not be processed for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] =
                "Regional evidence could not be verified right now. The " +
                "preliminary idea-generation score is still available, but " +
                "it is not a grounded regional analysis.";
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return new EmptyResult();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected market footprint error for idea {IdeaId}, user {UserId}.",
                ideaId,
                userId);

            TempData["Error"] = "An unexpected error occurred while analyzing regional demand.";
        }

        return RedirectToPage(new { openIdeaId = ideaId });
    }

    private static MarketOpportunitySnapshot BuildSnapshot(
        MarketFootprintResponse response,
        int userId,
        int ideaId)
    {
        var snapshot = new MarketOpportunitySnapshot
        {
            UserId = userId,
            ProjectIdeaId = ideaId,
            Status = SafeText(response.Status, 40),
            OverallOpportunityScore = response.OverallOpportunityScore.HasValue
                ? Math.Clamp(response.OverallOpportunityScore.Value, 0, 100)
                : null,
            OverallConfidenceScore = Math.Clamp(response.OverallConfidenceScore, 0, 100),
            OverallDemandLevel = SafeText(response.OverallDemandLevel, 30),
            BestLaunchMarket = SafeText(response.BestLaunchMarket, 120),
            BestLaunchReason = SafeText(response.BestLaunchReason, 2000),
            ExpansionPathJson = JsonSerializer.Serialize(CleanList(response.ExpansionPath, 3), JsonOpts),
            WhyDemandedJson = JsonSerializer.Serialize(CleanList(response.WhyDemanded, 3), JsonOpts),
            StrategicRecommendation = SafeText(response.StrategicRecommendation, 4000),
            LimitationsJson = JsonSerializer.Serialize(CleanList(response.Limitations, 4), JsonOpts),
            SourcesJson = JsonSerializer.Serialize(
                (response.Sources ?? [])
                    .Where(s => IsSafeHttpUrl(s.Url) && !string.IsNullOrWhiteSpace(s.Title))
                    .Take(20)
                    .Select(s => new
                    {
                        title = SafeText(s.Title, 300),
                        url = SafeText(s.Url, 2000),
                        publisher = SafeText(s.Publisher, 250),
                        relevance = SafeText(s.Relevance, 800),
                        relevanceScore = Math.Clamp(s.RelevanceScore, 0, 100),
                        isVerified = s.IsVerified,
                        regions = s.Regions ?? []
                    }),
                JsonOpts),
            GroundedInLiveData = response.GroundedInLiveData,
            Provider = SafeText(response.Provider, 120),
            ModelUsed = string.IsNullOrWhiteSpace(response.ModelUsed) ? null : SafeText(response.ModelUsed, 200),
            AnalyzedAt = response.AnalyzedAt == default ? DateTime.UtcNow : response.AnalyzedAt.ToUniversalTime(),
            CreatedAt = DateTime.UtcNow
        };

        foreach (var region in response.Regions ?? [])
        {
            snapshot.Regions.Add(new MarketOpportunityRegion
            {
                RegionKey = SafeText(region.RegionKey, 20),
                RegionName = SafeText(region.RegionName, 50),
                OpportunityScore = region.OpportunityScore.HasValue
                    ? Math.Clamp(region.OpportunityScore.Value, 0, 100)
                    : null,
                ConfidenceScore = Math.Clamp(region.ConfidenceScore, 0, 100),
                DemandLevel = SafeText(region.DemandLevel, 30),
                CompetitionPressure = NormalizeCompetitionPressure(region.CompetitionPressure),
                EvidenceSummary = SafeText(region.EvidenceSummary, 800),
                ScoreBreakdownJson = region.ScoreBreakdown == null
                    ? "{}"
                    : JsonSerializer.Serialize(
                        new
                        {
                            problemUrgency = Math.Clamp(region.ScoreBreakdown.ProblemUrgency, 0, 100),
                            geographicFit = Math.Clamp(region.ScoreBreakdown.GeographicFit, 0, 100),
                            adoptionReadiness = Math.Clamp(region.ScoreBreakdown.AdoptionReadiness, 0, 100),
                            competitionGap = Math.Clamp(region.ScoreBreakdown.CompetitionGap, 0, 100),
                            targetUserReachability = Math.Clamp(region.ScoreBreakdown.TargetUserReachability, 0, 100),
                            technologyMomentum = Math.Clamp(region.ScoreBreakdown.TechnologyMomentum, 0, 100),
                            evidenceStrength = Math.Clamp(region.ScoreBreakdown.EvidenceStrength, 0, 100)
                        },
                        JsonOpts),
                SourceUrlsJson = JsonSerializer.Serialize(
                    (region.SourceUrls ?? []).Where(IsSafeHttpUrl).Take(4),
                    JsonOpts)
            });
        }

        return snapshot;
    }

    private static string NormalizeCompetitionPressure(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "low" => "low",
        "high" => "high",
        _ => "medium"
    };

    private static List<string> CleanList(IEnumerable<string>? values, int maximum) =>
        (values ?? [])
        .Where(value => !string.IsNullOrWhiteSpace(value))
        .Select(value => SafeText(value, 500))
        .Take(maximum)
        .ToList();

    private static bool IsSafeHttpUrl(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

    private static string SafeText(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var clean = value.Trim();
        return clean.Length <= maximumLength ? clean : clean[..maximumLength];
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

        await LoadLatestMarketInsightsAsync(userId);
    }

    /// <summary>
    /// Loads only the latest Regional Demand Footprint snapshot per visible
    /// idea, in a single query (no N+1), so the Idea Generator never runs
    /// live research automatically.
    /// </summary>
    private async Task LoadLatestMarketInsightsAsync(int userId)
    {
        LatestMarketInsights = [];

        var ideaIds = GeneratedIdeas.Select(i => i.Id).ToList();

        if (ideaIds.Count == 0)
        {
            return;
        }

        var snapshots = await db.MarketOpportunitySnapshots
            .AsNoTracking()
            .Include(s => s.Regions)
            .Where(s => s.UserId == userId && ideaIds.Contains(s.ProjectIdeaId))
            .OrderByDescending(s => s.AnalyzedAt)
            .ThenByDescending(s => s.Id)
            .ToListAsync();

        LatestMarketInsights = snapshots
            .GroupBy(s => s.ProjectIdeaId)
            .ToDictionary(group => group.Key, group => group.First());
    }

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}