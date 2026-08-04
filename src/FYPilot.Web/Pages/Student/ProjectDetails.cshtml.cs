using System.Security.Claims;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProjectDetailsModel(
    ApplicationDbContext db,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService)
    : PageModel
{
    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public ProjectIdea? Idea { get; private set; }

    public ProjectAccessResult? ProjectAccess
    {
        get;
        private set;
    }

    public List<string> StudentSkillNames
    {
        get;
        private set;
    } = [];

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    public bool IsReadOnly =>
        ProjectAccess?.CanEdit != true;

    public bool IsOfficialProjectIdea =>
        Idea != null &&
        ProjectAccess?.ProjectIdeaId == Idea.Id;

    public async Task<IActionResult> OnGetAsync(
        int? id,
        int? ideaId,
        CancellationToken cancellationToken)
    {
        var userId = UserId();
        var selectedIdeaId = id ?? ideaId;

        if (!await ResolveProjectIdAsync(
                userId,
                selectedIdeaId,
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a project before opening "
                + "project details.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        ProjectAccess =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (ProjectAccess?.CanView != true)
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadStudentSkillsAsync(
            userId,
            cancellationToken);

        if (selectedIdeaId.HasValue)
        {
            Idea = await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    idea =>
                        idea.Id == selectedIdeaId.Value &&
                        (
                            idea.GeneratedForProjectId == ProjectId ||
                            idea.Id == ProjectAccess.ProjectIdeaId
                        ),
                    cancellationToken);
        }
        else if (ProjectAccess.ProjectIdeaId.HasValue)
        {
            Idea = await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(
                    idea =>
                        idea.Id ==
                        ProjectAccess.ProjectIdeaId.Value,
                    cancellationToken);
        }
        else
        {
            Idea = await db.ProjectIdeas
                .AsNoTracking()
                .Where(idea =>
                    idea.GeneratedForProjectId == ProjectId)
                .OrderByDescending(idea => idea.CreatedAt)
                .ThenByDescending(idea => idea.Id)
                .FirstOrDefaultAsync(
                    cancellationToken);
        }

        return Page();
    }

    private async Task<bool> ResolveProjectIdAsync(
        int userId,
        int? selectedIdeaId,
        CancellationToken cancellationToken)
    {
        if (ProjectId > 0)
        {
            return true;
        }

        if (selectedIdeaId.HasValue)
        {
            var generatedForProjectId =
                await db.ProjectIdeas
                    .AsNoTracking()
                    .Where(idea =>
                        idea.Id == selectedIdeaId.Value)
                    .Select(idea =>
                        idea.GeneratedForProjectId)
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (generatedForProjectId.HasValue &&
                generatedForProjectId.Value > 0)
            {
                ProjectId =
                    generatedForProjectId.Value;

                return true;
            }

            var officialProjectId =
                await db.Projects
                    .AsNoTracking()
                    .Where(project =>
                        project.ProjectIdeaId ==
                            selectedIdeaId.Value &&
                        !project.IsDeleted)
                    .Select(project => project.Id)
                    .FirstOrDefaultAsync(
                        cancellationToken);

            if (officialProjectId > 0)
            {
                ProjectId = officialProjectId;
                return true;
            }
        }

        var activeProjectId =
            await activeProjectService
                .GetActiveProjectIdAsync(
                    userId,
                    cancellationToken);

        if (!activeProjectId.HasValue)
        {
            return false;
        }

        ProjectId = activeProjectId.Value;
        return true;
    }

    private async Task LoadStudentSkillsAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        var assessedSkills = await db.StudentSkills
            .AsNoTracking()
            .Where(skill =>
                skill.UserId == userId)
            .Select(skill =>
                skill.SkillName)
            .ToListAsync(
                cancellationToken);

        var profileSkills = await db.StudentProfiles
            .AsNoTracking()
            .Where(profile =>
                profile.UserId == userId)
            .Select(profile =>
                profile.Skills)
            .FirstOrDefaultAsync(
                cancellationToken);

        StudentSkillNames = assessedSkills
            .Concat(SplitSkillText(profileSkills))
            .Where(skill =>
                !string.IsNullOrWhiteSpace(skill))
            .Select(skill => skill.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private static List<string> SplitSkillText(
        string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return [];
        }

        return value
            .Split(
                [',', ';', '\n', '\r'],
                StringSplitOptions.RemoveEmptyEntries)
            .Select(skill => skill.Trim())
            .Where(skill => skill.Length > 0)
            .ToList();
    }

    private int UserId()
    {
        return int.Parse(
            User.FindFirst(
                ClaimTypes.NameIdentifier)!.Value);
    }
}