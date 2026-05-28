using System.Security.Claims;
using System.Reflection;
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
    public GenerateDocumentationRequest Request { get; set; } = new();

    [BindProperty]
    public int SelectedProjectIdeaId { get; set; }

    public List<SelectListItem> ProjectIdeaOptions { get; set; } = new();

    public GeneratedDocumentationDto? GeneratedDocumentation { get; set; }

    public string Message { get; set; } = string.Empty;

    public async Task OnGetAsync(int? projectIdeaId)
    {
        var userId = GetCurrentUserId();

        await LoadProjectIdeasAsync(userId);

        if (projectIdeaId.HasValue && projectIdeaId.Value > 0)
        {
            SelectedProjectIdeaId = projectIdeaId.Value;
            await LoadSelectedIdeaIntoFormAsync(projectIdeaId.Value);
            return;
        }

        Request = new GenerateDocumentationRequest
        {
            ProjectTitle = "AI Clinic Appointment and Triage System",
            ProjectDescription = "A system that helps clinics manage appointments and prioritize patients using AI-based triage support.",
            Domain = "Healthcare",
            TechStack = "ASP.NET Core, PostgreSQL, Python AI",
            ContainsAi = true
        };
    }

    public async Task<IActionResult> OnPostAsync()
    {
        var userId = GetCurrentUserId();

        await LoadProjectIdeasAsync(userId);

        Request.UserId = userId;

        if (SelectedProjectIdeaId > 0)
        {
            var idea = await _db.Set<ProjectIdea>()
                .AsNoTracking()
                .FirstOrDefaultAsync(x => x.Id == SelectedProjectIdeaId);

            if (idea != null)
            {
                var ideaRequest = BuildRequestFromIdea(idea);

                Request.ProjectIdeaId = SelectedProjectIdeaId;

                if (!string.IsNullOrWhiteSpace(ideaRequest.ProjectTitle))
                {
                    Request.ProjectTitle = ideaRequest.ProjectTitle;
                }

                if (!string.IsNullOrWhiteSpace(ideaRequest.ProjectDescription))
                {
                    Request.ProjectDescription = ideaRequest.ProjectDescription;
                }

                if (!string.IsNullOrWhiteSpace(ideaRequest.Domain))
                {
                    Request.Domain = ideaRequest.Domain;
                }

                if (!string.IsNullOrWhiteSpace(ideaRequest.TechStack))
                {
                    Request.TechStack = ideaRequest.TechStack;
                }

                Request.ContainsAi = Request.ContainsAi || ideaRequest.ContainsAi;
            }
        }
        else
        {
            Request.ProjectIdeaId = Request.ProjectIdeaId == 0 ? 1 : Request.ProjectIdeaId;
        }

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
            return Page();
        }

        GeneratedDocumentation = await _documentationGeneratorService.GenerateAsync(Request);

        Message = SelectedProjectIdeaId > 0
            ? "Software engineering documentation generated successfully for the selected project idea."
            : "Software engineering documentation generated successfully.";

        return Page();
    }

    private async Task LoadProjectIdeasAsync(int userId)
    {
        ProjectIdeaOptions = new List<SelectListItem>
        {
            new SelectListItem
            {
                Value = "",
                Text = "-- Select saved project idea --"
            }
        };

        var ideas = await _db.Set<ProjectIdea>()
            .AsNoTracking()
            .ToListAsync();

        foreach (var idea in ideas)
        {
            var ideaUserId = GetIntValue(idea, "UserId", "StudentId", "CreatedByUserId", "OwnerId");

            if (ideaUserId.HasValue && ideaUserId.Value != userId)
            {
                continue;
            }

            var title = GetStringValue(idea, "Title", "ProjectTitle", "Name", "IdeaTitle");

            if (string.IsNullOrWhiteSpace(title))
            {
                title = $"Project Idea #{GetIntValue(idea, "Id") ?? 0}";
            }

            ProjectIdeaOptions.Add(new SelectListItem
            {
                Value = (GetIntValue(idea, "Id") ?? 0).ToString(),
                Text = title
            });
        }
    }

    private async Task LoadSelectedIdeaIntoFormAsync(int ideaId)
    {
        var idea = await _db.Set<ProjectIdea>()
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == ideaId);

        if (idea == null)
        {
            return;
        }

        Request = BuildRequestFromIdea(idea);
    }

    private async Task LoadSelectedIdeaIntoRequestAsync(int ideaId)
    {
        var idea = await _db.Set<ProjectIdea>()
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == ideaId);

        if (idea == null)
        {
            return;
        }

        Request = BuildRequestFromIdea(idea);
        Request.UserId = GetCurrentUserId();
        Request.ProjectIdeaId = ideaId;
    }

    private GenerateDocumentationRequest BuildRequestFromIdea(ProjectIdea idea)
    {
        var title = GetStringValue(idea, "Title", "ProjectTitle", "Name", "IdeaTitle");
        var description = GetStringValue(idea, "Description", "ProjectDescription", "IdeaDescription", "Summary");
        var domain = GetStringValue(idea, "Domain", "Category", "Sector");
        var techStack = GetStringValue(idea, "TechStack", "Technologies", "SuggestedTechnologies", "TechnologyStack");

        var combinedText = $"{title} {description} {domain} {techStack}".ToLower();

        var containsAi =
            combinedText.Contains("ai") ||
            combinedText.Contains("artificial intelligence") ||
            combinedText.Contains("machine learning") ||
            combinedText.Contains("ml") ||
            combinedText.Contains("prediction") ||
            combinedText.Contains("recommendation") ||
            combinedText.Contains("nlp") ||
            combinedText.Contains("data science");

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

        return 1;
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
}