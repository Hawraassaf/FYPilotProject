using System.Reflection;
using System.Security.Claims;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.AspNetCore.Mvc.Rendering;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DocumentationGeneratorModel : PageModel
{
    private readonly IDocumentationGeneratorService _documentationGeneratorService;
    private readonly ApplicationDbContext _db;

    public DocumentationGeneratorModel(
        IDocumentationGeneratorService documentationGeneratorService,
        ApplicationDbContext db)
    {
        _documentationGeneratorService = documentationGeneratorService;
        _db = db;
    }

    [BindProperty]
    public new GenerateDocumentationRequest Request { get; set; } = new();

    [BindProperty]
    public int SelectedProjectIdeaId { get; set; }

    public List<SelectListItem> ProjectIdeaOptions { get; private set; } = [];

    public List<ProjectIdea> ProjectIdeas { get; private set; } = [];

    public ProjectIdea? SelectedIdea { get; private set; }

    public GeneratedDocumentationDto? GeneratedDocumentation { get; private set; }

    public string Message { get; private set; } = string.Empty;

    public string? ErrorMessage { get; private set; }

    public bool HasSelectedIdea => SelectedIdea != null;

    public bool HasGeneratedDocumentation => GeneratedDocumentation != null;

    /// <summary>
    /// The AI Quality Passport for the most recently generated SE
    /// documentation for this idea, loaded from the database so it survives
    /// the Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as Mentor Chat and Roadmap -- linked via
    /// ProjectIdeaId, no new column or migration needed.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe documentation"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    private async Task LoadLatestReviewAsync(int ideaId, int userId)
    {
        LatestReview = await _db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.ProjectIdeaId == ideaId &&
                r.UserId == userId &&
                r.AgentName == "SEDocumentationAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();
    }

    public async Task OnGetAsync(int? projectIdeaId)
    {
        var userId = GetCurrentUserId();

        await LoadProjectIdeasAsync(userId);

        var ideaIdToLoad = projectIdeaId;

        if (!ideaIdToLoad.HasValue || ideaIdToLoad.Value <= 0)
        {
            var selectedIdea = ProjectIdeas.FirstOrDefault(idea =>
                GetBoolValue(idea, "IsSelected", "Selected"));

            if (selectedIdea != null)
            {
                ideaIdToLoad = GetIntValue(selectedIdea, "Id");
            }
        }

        if (ideaIdToLoad.HasValue && ideaIdToLoad.Value > 0)
        {
            await LoadSelectedIdeaIntoFormAsync(ideaIdToLoad.Value, userId);
        }
        else
        {
            Request = new GenerateDocumentationRequest
            {
                UserId = userId
            };
        }

        BuildProjectIdeaOptions();
    }

    public async Task<IActionResult> OnPostAsync()
    {
        return await OnPostGenerateAsync();
    }

    public async Task<IActionResult> OnPostGenerateAsync()
    {
        var userId = GetCurrentUserId();

        await LoadProjectIdeasAsync(userId);

        if (SelectedProjectIdeaId <= 0 && Request.ProjectIdeaId > 0)
        {
            SelectedProjectIdeaId = Request.ProjectIdeaId;
        }

        if (SelectedProjectIdeaId <= 0)
        {
            ErrorMessage = "Please select a saved project idea before generating software documentation.";
            ModelState.AddModelError(nameof(SelectedProjectIdeaId), ErrorMessage);
            BuildProjectIdeaOptions();
            return Page();
        }

        var idea = await GetUserProjectIdeaAsync(SelectedProjectIdeaId, userId);

        if (idea == null)
        {
            ErrorMessage = "The selected project idea was not found or does not belong to your account.";
            ModelState.AddModelError(nameof(SelectedProjectIdeaId), ErrorMessage);
            BuildProjectIdeaOptions();
            return Page();
        }

        SelectedIdea = idea;
        Request = BuildRequestFromIdea(idea);
        Request.UserId = userId;
        Request.ProjectIdeaId = SelectedProjectIdeaId;

        ModelState.Clear();

        if (string.IsNullOrWhiteSpace(Request.ProjectTitle))
        {
            ModelState.AddModelError("Request.ProjectTitle", "Project title is required.");
        }

        if (string.IsNullOrWhiteSpace(Request.ProjectDescription))
        {
            ModelState.AddModelError("Request.ProjectDescription", "Project description is required.");
        }

        if (!ModelState.IsValid)
        {
            ErrorMessage = "The selected project idea does not contain enough information to generate documentation.";
            BuildProjectIdeaOptions();
            return Page();
        }

        try
        {
            GeneratedDocumentation = await _documentationGeneratorService.GenerateAsync(Request);
            await LoadLatestReviewAsync(SelectedProjectIdeaId, userId);

            Message = "AI software engineering documentation generated successfully for the selected project idea.";
        }
        catch (Exception ex)
        {
            var detail = ex.InnerException?.Message ?? ex.Message;
            ErrorMessage = $"Software documentation generation failed. Backend error: {ex.Message} | Detail: {detail}";
        }

        BuildProjectIdeaOptions();

        return Page();
    }

    private async Task LoadProjectIdeasAsync(int userId)
    {
        var allIdeas = await _db.Set<ProjectIdea>()
            .AsNoTracking()
            .ToListAsync();

        ProjectIdeas = allIdeas
            .Where(idea =>
            {
                var ideaUserId = GetIntValue(idea, "UserId", "StudentId", "CreatedByUserId", "OwnerId");
                return ideaUserId.HasValue && ideaUserId.Value == userId;
            })
            .OrderByDescending(idea => GetDateTimeValue(idea, "CreatedAt", "CreatedOn", "DateCreated") ?? DateTime.MinValue)
            .ToList();

        BuildProjectIdeaOptions();
    }

    private void BuildProjectIdeaOptions()
    {
        ProjectIdeaOptions = new List<SelectListItem>
        {
            new SelectListItem
            {
                Value = "",
                Text = "-- Select saved project idea --",
                Selected = SelectedProjectIdeaId <= 0
            }
        };

        foreach (var idea in ProjectIdeas)
        {
            var ideaId = GetIntValue(idea, "Id") ?? 0;

            var title = GetStringValue(idea, "Title", "ProjectTitle", "Name", "IdeaTitle");

            if (string.IsNullOrWhiteSpace(title))
            {
                title = $"Project Idea #{ideaId}";
            }

            ProjectIdeaOptions.Add(new SelectListItem
            {
                Value = ideaId.ToString(),
                Text = title,
                Selected = SelectedProjectIdeaId == ideaId
            });
        }
    }

    private async Task LoadSelectedIdeaIntoFormAsync(int ideaId, int userId)
    {
        var idea = await GetUserProjectIdeaAsync(ideaId, userId);

        if (idea == null)
        {
            ErrorMessage = "The selected project idea was not found.";
            Request = new GenerateDocumentationRequest
            {
                UserId = userId
            };
            return;
        }

        SelectedIdea = idea;
        SelectedProjectIdeaId = ideaId;
        Request = BuildRequestFromIdea(idea);
        Request.UserId = userId;
        Request.ProjectIdeaId = ideaId;

        await LoadLatestReviewAsync(ideaId, userId);
    }

    private async Task<ProjectIdea?> GetUserProjectIdeaAsync(int ideaId, int userId)
    {
        var idea = await _db.Set<ProjectIdea>()
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == ideaId);

        if (idea == null)
        {
            return null;
        }

        var ideaUserId = GetIntValue(idea, "UserId", "StudentId", "CreatedByUserId", "OwnerId");

        if (!ideaUserId.HasValue || ideaUserId.Value != userId)
        {
            return null;
        }

        return idea;
    }

    private GenerateDocumentationRequest BuildRequestFromIdea(ProjectIdea idea)
    {
        var title = GetStringValue(
            idea,
            "Title",
            "ProjectTitle",
            "Name",
            "IdeaTitle"
        );

        var description = GetStringValue(
            idea,
            "ProjectDescription",
            "Description",
            "IdeaDescription",
            "Summary",
            "ProblemStatement",
            "Problem"
        );

        var problemStatement = GetStringValue(
            idea,
            "ProblemStatement",
            "Problem"
        );

        var targetUsers = GetStringValue(
            idea,
            "TargetUsers",
            "Users"
        );

        var whyUseful = GetStringValue(
            idea,
            "WhyUseful",
            "Usefulness",
            "Value"
        );

        if (string.IsNullOrWhiteSpace(description))
        {
            var descriptionParts = new List<string>();

            if (!string.IsNullOrWhiteSpace(problemStatement))
            {
                descriptionParts.Add($"Problem: {problemStatement}");
            }

            if (!string.IsNullOrWhiteSpace(targetUsers))
            {
                descriptionParts.Add($"Target users: {targetUsers}");
            }

            if (!string.IsNullOrWhiteSpace(whyUseful))
            {
                descriptionParts.Add($"Usefulness: {whyUseful}");
            }

            description = string.Join("\n", descriptionParts);
        }

        var domain = GetStringValue(
            idea,
            "Domain",
            "ProjectDomain",
            "Category",
            "Sector"
        );

        var techStack = GetStringValue(
            idea,
            "TechStack",
            "Technologies",
            "SuggestedTechnologies",
            "TechnologyStack",
            "RequiredTechnologies"
        );

        var combinedText = $"{title} {description} {domain} {techStack}".ToLowerInvariant();

        var containsAi =
            combinedText.Contains("ai") ||
            combinedText.Contains("artificial intelligence") ||
            combinedText.Contains("machine learning") ||
            combinedText.Contains("ml") ||
            combinedText.Contains("prediction") ||
            combinedText.Contains("recommendation") ||
            combinedText.Contains("nlp") ||
            combinedText.Contains("data science") ||
            combinedText.Contains("llm") ||
            combinedText.Contains("chatbot");

        return new GenerateDocumentationRequest
        {
            UserId = GetCurrentUserId(),
            ProjectIdeaId = GetIntValue(idea, "Id") ?? 0,
            ProjectTitle = title,
            ProjectDescription = description,
            Domain = domain,
            TechStack = techStack,
            ContainsAi = containsAi
        };
    }

    private int GetCurrentUserId()
    {
        var userIdClaim = User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdClaim, out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException("Unable to identify the current logged-in user.");
    }

    private static string GetStringValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source)?.ToString();

            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        return string.Empty;
    }

    private static int? GetIntValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return null;
    }

    private static bool GetBoolValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is bool boolValue)
            {
                return boolValue;
            }

            if (bool.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return false;
    }

    private static DateTime? GetDateTimeValue(object source, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var property = source.GetType().GetProperty(
                propertyName,
                BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);

            var value = property?.GetValue(source);

            if (value == null)
            {
                continue;
            }

            if (value is DateTime dateTimeValue)
            {
                return dateTimeValue;
            }

            if (DateTime.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return null;
    }
}
