using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class IdeaComparisonModel(
    ApplicationDbContext db,
    IAiServiceClient aiService
) : PageModel
{
    public List<ProjectIdea> Ideas { get; private set; } = [];

    public StudentProfile? Profile { get; private set; }

    public List<StudentSkill> Skills { get; private set; } = [];

    public IdeaComparisonServiceResponse? ComparisonResponse { get; private set; }

    public IdeaComparisonDto? Comparison => ComparisonResponse?.Comparison;

    public string? ErrorMessage { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated comparison.
    /// Unlike Project DNA (one existing idea), one comparison covers a
    /// batch of the student's own already-generated ideas at once (there is
    /// no single ProjectIdeaId to key off), so this is scoped by UserId +
    /// AgentName only -- reuses the same AiOutputReview entity, no new
    /// column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe comparison"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    public async Task OnGetAsync()
    {
        await LoadPageDataAsync();
        await LoadLatestReviewAsync();
    }

    public async Task<IActionResult> OnPostCompareAsync()
    {
        await LoadPageDataAsync();
        await LoadLatestReviewAsync();

        if (Ideas.Count < 2)
        {
            ErrorMessage = "You need at least two generated ideas before comparing.";
            return Page();
        }

        var request = BuildComparisonRequest();

        ComparisonResponse = await aiService.CompareGeneratedIdeasAsync(request);

        if (ComparisonResponse == null)
        {
            ErrorMessage = "Idea comparison could not be generated. Make sure the Python AI service is running.";
            return Page();
        }

        await PersistReviewAsync(ComparisonResponse);

        return Page();
    }

    private async Task PersistReviewAsync(IdeaComparisonServiceResponse response)
    {
        var review = response.Review;

        if (review == null)
        {
            return;
        }

        var userId = UserId();

        db.AiOutputReviews.Add(new AiOutputReview
        {
            ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                ? reviewRunId
                : Guid.NewGuid(),
            UserId = userId,
            ProjectIdeaId = null,
            MentorChatSessionId = null,
            AgentName = "IdeaComparisonAgent",
            Status = review.Status,
            Usable = review.Usable,
            WasRewritten = review.Attempts > 1,
            Attempts = review.Attempts,
            QualityScore = review.QualityScore,
            DecisionReason = review.DecisionReason,
            GeneratorProvider = response.Provider,
            GeneratorModel = response.ModelUsed,
            ReviewerProvider = review.ReviewerProvider,
            ReviewerModel = review.ReviewerModel,
            FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
            FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
            FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
            IssuesJson = JsonSerializer.Serialize(review.Issues),
            StrengthsJson = JsonSerializer.Serialize(review.Strengths),
            AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
            ReviewerVersion = review.ReviewerVersion,
            CreatedAt = DateTime.UtcNow,
            CompletedAt = DateTime.UtcNow
        });

        await db.SaveChangesAsync();
        await LoadLatestReviewAsync();
    }

    private async Task LoadLatestReviewAsync()
    {
        var userId = UserId();

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.UserId == userId && r.AgentName == "IdeaComparisonAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();
    }

    public async Task<IActionResult> OnPostSelectAsync(int ideaId)
    {
        var userId = UserId();

        var ideaExists = await db.ProjectIdeas
            .AnyAsync(i =>
                i.Id == ideaId &&
                i.UserId == userId);

        if (!ideaExists)
        {
            TempData["Error"] =
                "The selected idea was not found or does not belong to your account.";

            return RedirectToPage();
        }

        await using var transaction =
            await db.Database.BeginTransactionAsync();

        await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .ExecuteUpdateAsync(update =>
                update.SetProperty(
                    idea => idea.IsSelected,
                    false));

        var updatedRows = await db.ProjectIdeas
            .Where(i =>
                i.Id == ideaId &&
                i.UserId == userId)
            .ExecuteUpdateAsync(update =>
                update.SetProperty(
                    idea => idea.IsSelected,
                    true));

        if (updatedRows != 1)
        {
            await transaction.RollbackAsync();

            TempData["Error"] =
                "The project idea could not be selected. Please try again.";

            return RedirectToPage();
        }

        await transaction.CommitAsync();

        TempData["Success"] =
            "Project idea selected successfully.";

        return RedirectToPage();
    }

    private async Task LoadPageDataAsync()
    {
        var userId = UserId();

        Profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        Skills = await db.StudentSkills
            .Where(s => s.UserId == userId)
            .ToListAsync();

        Ideas = await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .Take(12)
            .ToListAsync();
    }

    private IdeaComparisonRequest BuildComparisonRequest()
    {
        var studentSkills = Skills
            .Select(s => GetString(s, "SkillName", "Name", "Title"))
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct()
            .ToList();

        var skillRatings = new Dictionary<string, int>();

        foreach (var skill in Skills)
        {
            var skillName = GetString(skill, "SkillName", "Name", "Title");

            if (string.IsNullOrWhiteSpace(skillName))
            {
                continue;
            }

            var rating = GetInt(skill, 3, "Rating", "Level", "SkillLevel", "Score");
            skillRatings[skillName] = rating;
        }

        var ideaDtos = Ideas
            .Select(i => new IdeaComparisonInputDto(
                Id: i.Id,
                Title: GetString(i, "Title", "IdeaTitle", "Name"),
                ProblemStatement: GetString(i, "ProblemStatement", "Problem", "Description"),
                RequiredTechnologies: GetString(i, "RequiredTechnologies", "Technologies", "TechStack"),
                RequiredSkills: GetString(i, "RequiredSkills"),
                MissingSkills: GetString(i, "MissingSkills"),
                DifficultyLevel: GetString(i, "DifficultyLevel", "Difficulty"),
                ExpectedDurationWeeks: GetInt(i, 10, "ExpectedDurationWeeks", "DurationWeeks"),
                DatasetNeeded: GetString(i, "DatasetNeeded", "DatasetRequirement", "Dataset"),
                Domain: GetString(i, "Domain", "ProjectDomain"),
                LebaneseMarketRelevance: GetString(i, "LebaneseMarketRelevance", "LebanesesMarketRelevance", "LocalMarketRelevance"),
                InnovationScore: GetDouble(i, 70, "InnovationScore"),
                FeasibilityScore: GetDouble(i, 70, "FeasibilityScore"),
                MarketDemandScore: GetDouble(i, 70, "MarketDemandScore", "MarketRelevanceScore"),
                CreatedAt: GetDateString(i, "CreatedAt")
            ))
            .ToList();

        return new IdeaComparisonRequest(
            StudentMajor: GetString(Profile, "Major"),
            ExperienceLevel: GetString(Profile, "ExperienceLevel"),
            TeamSize: GetInt(Profile, 1, "TeamMembers", "TeamSize"),
            AvailableHoursPerWeek: GetInt(Profile, 10, "AvailableHoursPerWeek"),
            StudentSkills: studentSkills,
            SkillRatings: skillRatings,
            Ideas: ideaDtos
        );
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

    private static string GetString(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            return value.ToString() ?? "";
        }

        return "";
    }

    private static int GetInt(object? obj, int defaultValue, params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (value is double doubleValue)
            {
                return Convert.ToInt32(doubleValue);
            }

            if (value is decimal decimalValue)
            {
                return Convert.ToInt32(decimalValue);
            }

            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static double GetDouble(object? obj, double defaultValue, params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is double doubleValue)
            {
                return doubleValue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (value is decimal decimalValue)
            {
                return Convert.ToDouble(decimalValue);
            }

            if (double.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static string GetDateString(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is DateTime dateTime)
            {
                return dateTime.ToString("O");
            }

            return value.ToString() ?? "";
        }

        return "";
    }
}