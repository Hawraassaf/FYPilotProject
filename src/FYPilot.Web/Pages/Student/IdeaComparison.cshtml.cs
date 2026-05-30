using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class IdeaComparisonModel(ApplicationDbContext db) : PageModel
{
    public List<ProjectIdea> Ideas { get; private set; } = [];

    public async Task OnGetAsync()
    {
        Ideas = await db.ProjectIdeas.Where(i => i.UserId == UserId())
            .OrderByDescending(i => i.CreatedAt).Take(6).ToListAsync();
    }

    public async Task<IActionResult> OnPostAsync(int ideaId)
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

        TempData["Success"] = "Project idea selected.";
        return RedirectToPage();
    }

    private int UserId() => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
