using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

/// <summary>
/// Admin management page for the Idea Generation Knowledge Base.
/// The stored records are retrieved as bounded institutional context.
/// They do not train or permanently modify the AI model.
/// </summary>
[Authorize(Roles = "admin")]
public class IdeaGenerationKnowledgeBaseModel(
    ApplicationDbContext db,
    ILogger<IdeaGenerationKnowledgeBaseModel> logger) : PageModel
{
    public static readonly string[] GuidanceTypes =
    [
        "General",
        "PreferredTheme",
        "AvoidTheme",
        "InstitutionalRule",
        "TechnicalConstraint",
        "ScopeConstraint",
    ];

    public static readonly string[] ProjectStatuses =
    [
        "Completed",
        "Rejected",
        "Repeated",
        "Archived",
        "InProgress",
        "Unknown",
    ];

    // Keep these values exactly aligned with the Idea Generator.
    public static readonly string[] PreferredDomains =
    [
        "Web Development",
        "AI/Data Science",
        "Mobile Development",
        "Cybersecurity",
        "Healthcare Technology",
        "FinTech",
        "Educational Technology",
        "IOT/Embedded",
        "General Software System",
    ];

    [TempData]
    public string? SuccessMessage { get; set; }

    [TempData]
    public string? ErrorMessage { get; set; }

    public string ActiveTab { get; private set; } = "guidance";

    public List<IdeaGenerationGuidance> GuidanceItems { get; private set; } = [];
    public List<HistoricalFypProject> HistoricalProjects { get; private set; } = [];
    public List<HistoricalFypFutureOpportunity> FutureOpportunities { get; private set; } = [];

    public int ActiveGuidanceCount { get; private set; }
    public int HistoricalProjectsCount { get; private set; }
    public int ExcludedProjectsCount { get; private set; }
    public int ActiveFutureOpportunitiesCount { get; private set; }

    [BindProperty]
    public GuidanceInput NewGuidance { get; set; } = new();

    [BindProperty]
    public HistoricalProjectInput NewProject { get; set; } = new();

    [BindProperty]
    public FutureOpportunityInput NewOpportunity { get; set; } = new();

    public async Task OnGetAsync(
        int? editGuidanceId,
        int? editProjectId,
        int? editOpportunityId,
        string? activeTab)
    {
        ActiveTab = NormalizeTab(activeTab);
        await LoadAsync();

        if (editGuidanceId is int guidanceId)
        {
            ActiveTab = "guidance";
            var guidance = GuidanceItems.FirstOrDefault(item => item.Id == guidanceId);

            if (guidance is not null)
            {
                NewGuidance = new GuidanceInput
                {
                    Id = guidance.Id,
                    Title = guidance.Title,
                    Content = guidance.Content,
                    GuidanceType = guidance.GuidanceType,
                    Domain = guidance.Domain,
                    Priority = guidance.Priority,
                };
            }
        }

        if (editProjectId is int projectId)
        {
            ActiveTab = "projects";
            var project = HistoricalProjects.FirstOrDefault(item => item.Id == projectId);

            if (project is not null)
            {
                NewProject = new HistoricalProjectInput
                {
                    Id = project.Id,
                    Title = project.Title,
                    ProblemStatement = project.ProblemStatement,
                    Domain = project.Domain,
                    CompletionYear = project.CompletionYear,
                    ProjectStatus = project.ProjectStatus,
                    ExcludeSimilarIdeas = project.ExcludeSimilarIdeas,
                    AllowAsInspiration = project.AllowAsInspiration,
                };
            }
        }

        if (editOpportunityId is int opportunityId)
        {
            ActiveTab = "opportunities";
            var opportunity = FutureOpportunities.FirstOrDefault(item => item.Id == opportunityId);

            if (opportunity is not null)
            {
                NewOpportunity = new FutureOpportunityInput
                {
                    Id = opportunity.Id,
                    HistoricalFypProjectId = opportunity.HistoricalFypProjectId,
                    Title = opportunity.Title,
                    Description = opportunity.Description,
                    SuggestedDomain = opportunity.SuggestedDomain,
                    Priority = opportunity.Priority,
                };
            }
        }
    }

    // ------------------------------------------------------------------
    // Generation Guidance
    // ------------------------------------------------------------------

    public async Task<IActionResult> OnPostCreateGuidanceAsync()
    {
        ClearMessages();
        ActiveTab = "guidance";

        ModelState.Clear();
        TryValidateModel(NewGuidance, nameof(NewGuidance));

        if (!GuidanceTypes.Contains(NewGuidance.GuidanceType))
        {
            ModelState.AddModelError(
                $"{nameof(NewGuidance)}.{nameof(NewGuidance.GuidanceType)}",
                "Select a valid guidance type.");
        }

        ValidateDomain(
            NewGuidance.Domain,
            $"{nameof(NewGuidance)}.{nameof(NewGuidance.Domain)}");

        if (!ModelState.IsValid)
        {
            await LoadAsync();
            return Page();
        }

        try
        {
            string successMessage;

            if (NewGuidance.Id is int id and > 0)
            {
                var existing = await db.IdeaGenerationGuidances
                    .FirstOrDefaultAsync(item => item.Id == id);

                if (existing is null)
                {
                    SetError("That guidance item no longer exists.");
                    return RedirectToTab("guidance");
                }

                existing.Title = NewGuidance.Title.Trim();
                existing.Content = NewGuidance.Content.Trim();
                existing.GuidanceType = NewGuidance.GuidanceType;
                existing.Major = null;
                existing.Domain = NormalizeOptional(NewGuidance.Domain);
                existing.Priority = NewGuidance.Priority;
                existing.UpdatedAt = DateTime.UtcNow;

                successMessage = "Guidance item updated.";
            }
            else
            {
                db.IdeaGenerationGuidances.Add(new IdeaGenerationGuidance
                {
                    Title = NewGuidance.Title.Trim(),
                    Content = NewGuidance.Content.Trim(),
                    GuidanceType = NewGuidance.GuidanceType,
                    Major = null,
                    Domain = NormalizeOptional(NewGuidance.Domain),
                    Priority = NewGuidance.Priority,
                    EffectiveFrom = null,
                    EffectiveUntil = null,
                    IsActive = true,
                    CreatedByUserId = GetCurrentUserId(),
                });

                successMessage = "Guidance item added.";
            }

            // Save first. Only show success after the database confirms the save.
            await db.SaveChangesAsync();

            SetSuccess(successMessage);
            return RedirectToTab("guidance");
        }
        catch (DbUpdateException ex)
        {
            logger.LogError(
                ex,
                "Database error while saving guidance {GuidanceId}. Inner error: {InnerError}",
                NewGuidance.Id,
                ex.InnerException?.Message);

            SetError("Could not save that guidance item.");
            return RedirectToTab("guidance");
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Unexpected error while saving guidance {GuidanceId}.",
                NewGuidance.Id);

            SetError("An unexpected error occurred while saving the guidance item.");
            return RedirectToTab("guidance");
        }
    }

    public async Task<IActionResult> OnPostToggleGuidanceActiveAsync(int id)
    {
        ClearMessages();

        try
        {
            var guidance = await db.IdeaGenerationGuidances
                .FirstOrDefaultAsync(item => item.Id == id);

            if (guidance is null)
            {
                SetError("That guidance item no longer exists.");
                return RedirectToTab("guidance");
            }

            guidance.IsActive = !guidance.IsActive;
            guidance.UpdatedAt = DateTime.UtcNow;

            await db.SaveChangesAsync();

            SetSuccess(
                guidance.IsActive
                    ? "Guidance item activated."
                    : "Guidance item deactivated.");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to change guidance status for {GuidanceId}.", id);
            SetError("Could not change the guidance status.");
        }

        return RedirectToTab("guidance");
    }

    // ------------------------------------------------------------------
    // Previous FYP Projects
    // ------------------------------------------------------------------

    public async Task<IActionResult> OnPostCreateProjectAsync()
    {
        ClearMessages();
        ActiveTab = "projects";

        ModelState.Clear();
        TryValidateModel(NewProject, nameof(NewProject));

        if (!ProjectStatuses.Contains(NewProject.ProjectStatus))
        {
            ModelState.AddModelError(
                $"{nameof(NewProject)}.{nameof(NewProject.ProjectStatus)}",
                "Select a valid project status.");
        }

        ValidateDomain(
            NewProject.Domain,
            $"{nameof(NewProject)}.{nameof(NewProject.Domain)}");

        var currentYear = DateTime.UtcNow.Year;

        if (NewProject.CompletionYear is int year
            && (year < 2000 || year > currentYear + 1))
        {
            ModelState.AddModelError(
                $"{nameof(NewProject)}.{nameof(NewProject.CompletionYear)}",
                $"Completion year must be between 2000 and {currentYear + 1}.");
        }

        if (!ModelState.IsValid)
        {
            await LoadAsync();
            return Page();
        }

        try
        {
            string successMessage;

            if (NewProject.Id is int id and > 0)
            {
                var existing = await db.HistoricalFypProjects
                    .FirstOrDefaultAsync(item => item.Id == id);

                if (existing is null)
                {
                    SetError("That project no longer exists.");
                    return RedirectToTab("projects");
                }

                existing.Title = NewProject.Title.Trim();
                existing.ProblemStatement = NewProject.ProblemStatement.Trim();
                existing.Major = null;
                existing.Domain = NormalizeOptional(NewProject.Domain);
                existing.CompletionYear = NewProject.CompletionYear;
                existing.ProjectStatus = NewProject.ProjectStatus;
                existing.ExcludeSimilarIdeas = NewProject.ExcludeSimilarIdeas;
                existing.AllowAsInspiration = NewProject.AllowAsInspiration;
                existing.UpdatedAt = DateTime.UtcNow;

                // Older optional details are intentionally preserved when editing.
                successMessage = "Previous project updated.";
            }
            else
            {
                db.HistoricalFypProjects.Add(new HistoricalFypProject
                {
                    Title = NewProject.Title.Trim(),
                    ProblemStatement = NewProject.ProblemStatement.Trim(),
                    Major = null,
                    Domain = NormalizeOptional(NewProject.Domain),
                    TargetUsers = null,
                    Technologies = string.Empty,
                    CompletionYear = NewProject.CompletionYear,
                    ProjectStatus = NewProject.ProjectStatus,
                    Keywords = string.Empty,
                    ExcludeSimilarIdeas = NewProject.ExcludeSimilarIdeas,
                    AllowAsInspiration = NewProject.AllowAsInspiration,
                    ExclusionReason = null,
                    IsActive = true,
                    CreatedByUserId = GetCurrentUserId(),
                });

                successMessage = "Previous project registered.";
            }

            await db.SaveChangesAsync();

            SetSuccess(successMessage);
            return RedirectToTab("projects");
        }
        catch (DbUpdateException ex)
        {
            logger.LogError(
                ex,
                "Database error while saving historical project {ProjectId}. Inner error: {InnerError}",
                NewProject.Id,
                ex.InnerException?.Message);

            SetError("Could not save that project.");
            return RedirectToTab("projects");
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Unexpected error while saving historical project {ProjectId}.",
                NewProject.Id);

            SetError("An unexpected error occurred while saving the project.");
            return RedirectToTab("projects");
        }
    }

    public async Task<IActionResult> OnPostToggleProjectActiveAsync(int id)
    {
        ClearMessages();

        try
        {
            var project = await db.HistoricalFypProjects
                .FirstOrDefaultAsync(item => item.Id == id);

            if (project is null)
            {
                SetError("That project no longer exists.");
                return RedirectToTab("projects");
            }

            project.IsActive = !project.IsActive;
            project.UpdatedAt = DateTime.UtcNow;

            await db.SaveChangesAsync();

            SetSuccess(
                project.IsActive
                    ? "Project activated."
                    : "Project deactivated.");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to change project status for {ProjectId}.", id);
            SetError("Could not change the project status.");
        }

        return RedirectToTab("projects");
    }

    // ------------------------------------------------------------------
    // Future Opportunities
    // ------------------------------------------------------------------

    public async Task<IActionResult> OnPostCreateOpportunityAsync()
    {
        ClearMessages();
        ActiveTab = "opportunities";

        ModelState.Clear();
        TryValidateModel(NewOpportunity, nameof(NewOpportunity));

        ValidateDomain(
            NewOpportunity.SuggestedDomain,
            $"{nameof(NewOpportunity)}.{nameof(NewOpportunity.SuggestedDomain)}");

        var parentExists = NewOpportunity.HistoricalFypProjectId > 0
            && await db.HistoricalFypProjects.AnyAsync(
                item => item.Id == NewOpportunity.HistoricalFypProjectId);

        if (!parentExists)
        {
            ModelState.AddModelError(
                $"{nameof(NewOpportunity)}.{nameof(NewOpportunity.HistoricalFypProjectId)}",
                "Select a valid parent project.");
        }

        if (!ModelState.IsValid)
        {
            await LoadAsync();
            return Page();
        }

        try
        {
            string successMessage;

            if (NewOpportunity.Id is int id and > 0)
            {
                var existing = await db.HistoricalFypFutureOpportunities
                    .FirstOrDefaultAsync(item => item.Id == id);

                if (existing is null)
                {
                    SetError("That future opportunity no longer exists.");
                    return RedirectToTab("opportunities");
                }

                existing.HistoricalFypProjectId = NewOpportunity.HistoricalFypProjectId;
                existing.Title = NewOpportunity.Title.Trim();
                existing.Description = NewOpportunity.Description.Trim();
                existing.SuggestedDomain = NormalizeOptional(NewOpportunity.SuggestedDomain);
                existing.Priority = NewOpportunity.Priority;
                existing.UpdatedAt = DateTime.UtcNow;

                // Older optional technical details are intentionally preserved.
                successMessage = "Future opportunity updated.";
            }
            else
            {
                db.HistoricalFypFutureOpportunities.Add(
                    new HistoricalFypFutureOpportunity
                    {
                        HistoricalFypProjectId = NewOpportunity.HistoricalFypProjectId,
                        Title = NewOpportunity.Title.Trim(),
                        Description = NewOpportunity.Description.Trim(),
                        SuggestedDomain = NormalizeOptional(NewOpportunity.SuggestedDomain),
                        SuggestedTechnologies = string.Empty,
                        ResearchGap = null,
                        Priority = NewOpportunity.Priority,
                        IsActive = true,
                        CreatedByUserId = GetCurrentUserId(),
                    });

                successMessage = "Future opportunity added.";
            }

            await db.SaveChangesAsync();

            SetSuccess(successMessage);
            return RedirectToTab("opportunities");
        }
        catch (DbUpdateException ex)
        {
            logger.LogError(
                ex,
                "Database error while saving future opportunity {OpportunityId}. Inner error: {InnerError}",
                NewOpportunity.Id,
                ex.InnerException?.Message);

            SetError("Could not save that future opportunity.");
            return RedirectToTab("opportunities");
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Unexpected error while saving future opportunity {OpportunityId}.",
                NewOpportunity.Id);

            SetError("An unexpected error occurred while saving the future opportunity.");
            return RedirectToTab("opportunities");
        }
    }

    public async Task<IActionResult> OnPostToggleOpportunityActiveAsync(int id)
    {
        ClearMessages();

        try
        {
            var opportunity = await db.HistoricalFypFutureOpportunities
                .FirstOrDefaultAsync(item => item.Id == id);

            if (opportunity is null)
            {
                SetError("That future opportunity no longer exists.");
                return RedirectToTab("opportunities");
            }

            opportunity.IsActive = !opportunity.IsActive;
            opportunity.UpdatedAt = DateTime.UtcNow;

            await db.SaveChangesAsync();

            SetSuccess(
                opportunity.IsActive
                    ? "Future opportunity activated."
                    : "Future opportunity deactivated.");
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Failed to change future opportunity status for {OpportunityId}.",
                id);

            SetError("Could not change the future opportunity status.");
        }

        return RedirectToTab("opportunities");
    }

    // ------------------------------------------------------------------
    // Data loading and helpers
    // ------------------------------------------------------------------

    private async Task LoadAsync()
    {
        GuidanceItems = await db.IdeaGenerationGuidances
            .AsNoTracking()
            .OrderByDescending(item => item.UpdatedAt)
            .ToListAsync();

        HistoricalProjects = await db.HistoricalFypProjects
            .AsNoTracking()
            .Include(item => item.FutureOpportunities)
            .OrderByDescending(item => item.UpdatedAt)
            .ToListAsync();

        FutureOpportunities = await db.HistoricalFypFutureOpportunities
            .AsNoTracking()
            .Include(item => item.HistoricalFypProject)
            .OrderByDescending(item => item.UpdatedAt)
            .ToListAsync();

        ActiveGuidanceCount = GuidanceItems.Count(item => item.IsActive);
        HistoricalProjectsCount = HistoricalProjects.Count;
        ExcludedProjectsCount = HistoricalProjects.Count(
            item => item.IsActive && item.ExcludeSimilarIdeas);
        ActiveFutureOpportunitiesCount = FutureOpportunities.Count(
            item => item.IsActive);
    }

    private void ValidateDomain(string? domain, string modelStateKey)
    {
        if (string.IsNullOrWhiteSpace(domain))
        {
            return;
        }

        if (!PreferredDomains.Contains(domain, StringComparer.OrdinalIgnoreCase))
        {
            ModelState.AddModelError(
                modelStateKey,
                "Select a valid preferred domain.");
        }
    }

    private IActionResult RedirectToTab(string tab) =>
        RedirectToPage(
            "./IdeaGenerationKnowledgeBase",
            new { activeTab = NormalizeTab(tab) });

    private void ClearMessages()
    {
        TempData.Remove(nameof(SuccessMessage));
        TempData.Remove(nameof(ErrorMessage));
    }

    private void SetSuccess(string message)
    {
        TempData.Remove(nameof(ErrorMessage));
        SuccessMessage = message;
    }

    private void SetError(string message)
    {
        TempData.Remove(nameof(SuccessMessage));
        ErrorMessage = message;
    }

    private static string NormalizeTab(string? tab) =>
        tab?.Trim().ToLowerInvariant() switch
        {
            "projects" => "projects",
            "opportunities" => "opportunities",
            _ => "guidance",
        };

    private static string? NormalizeOptional(string? value) =>
        string.IsNullOrWhiteSpace(value)
            ? null
            : value.Trim();

    private int GetCurrentUserId()
    {
        var userIdClaim = User.FindFirst(ClaimTypes.NameIdentifier)?.Value;

        if (int.TryParse(userIdClaim, out var userId))
        {
            return userId;
        }

        throw new InvalidOperationException(
            "Unable to identify the current logged-in administrator.");
    }

    public sealed class GuidanceInput
    {
        public int? Id { get; set; }

        [Required(ErrorMessage = "Enter a short guidance title.")]
        [StringLength(200)]
        public string Title { get; set; } = string.Empty;

        [Required(ErrorMessage = "Explain the guidance rule.")]
        [StringLength(4000)]
        public string Content { get; set; } = string.Empty;

        [Required]
        public string GuidanceType { get; set; } = "General";

        [StringLength(100)]
        public string? Domain { get; set; }

        [Range(1, 5, ErrorMessage = "Priority must be between 1 and 5.")]
        public int Priority { get; set; } = 3;
    }

    public sealed class HistoricalProjectInput
    {
        public int? Id { get; set; }

        [Required(ErrorMessage = "Enter the project title.")]
        [StringLength(200)]
        public string Title { get; set; } = string.Empty;

        [Required(ErrorMessage = "Add a short project description.")]
        [StringLength(4000)]
        public string ProblemStatement { get; set; } = string.Empty;

        [StringLength(100)]
        public string? Domain { get; set; }

        public int? CompletionYear { get; set; }

        [Required]
        public string ProjectStatus { get; set; } = "Completed";

        public bool ExcludeSimilarIdeas { get; set; }
        public bool AllowAsInspiration { get; set; }
    }

    public sealed class FutureOpportunityInput
    {
        public int? Id { get; set; }

        [Required(ErrorMessage = "Select the parent project.")]
        public int HistoricalFypProjectId { get; set; }

        [Required(ErrorMessage = "Enter the opportunity title.")]
        [StringLength(200)]
        public string Title { get; set; } = string.Empty;

        [Required(ErrorMessage = "Add a short opportunity description.")]
        [StringLength(4000)]
        public string Description { get; set; } = string.Empty;

        [StringLength(100)]
        public string? SuggestedDomain { get; set; }

        [Range(1, 5, ErrorMessage = "Priority must be between 1 and 5.")]
        public int Priority { get; set; } = 3;
    }
}