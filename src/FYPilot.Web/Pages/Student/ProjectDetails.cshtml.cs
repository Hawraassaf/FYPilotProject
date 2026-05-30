using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class ProjectDetailsModel(ApplicationDbContext db) : PageModel
{
    public ProjectIdea? Idea { get; private set; }

    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    public async Task<IActionResult> OnGetAsync(int? id)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;

        var userId = UserId();

        if (id.HasValue)
        {
            Idea = await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(i => i.Id == id.Value && i.UserId == userId);
        }
        else
        {
            Idea = await db.ProjectIdeas
                .AsNoTracking()
                .FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected);
        }

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
            .ExecuteUpdateAsync(s => s.SetProperty(i => i.IsSelected, false));

        await db.ProjectIdeas
            .Where(i => i.Id == ideaId && i.UserId == userId)
            .ExecuteUpdateAsync(s => s.SetProperty(i => i.IsSelected, true));

        TempData["Success"] = "Project idea selected successfully.";
        return RedirectToPage(new { id = ideaId });
    }

    private int UserId()
    {
        return int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
    }
}