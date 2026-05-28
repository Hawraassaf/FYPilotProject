using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class ProjectsModel(ApplicationDbContext db) : PageModel
{
    public List<IdeaRow> Ideas { get; private set; } = [];

    public record IdeaRow(int Id, int UserId, string Title, string StudentName, string Domain, string DifficultyLevel,
        int FeasibilityScore, int InnovationScore, bool IsSelected, DateTime CreatedAt);

    public async Task OnGetAsync()
    {
        var ideaRows = await db.ProjectIdeas
            .AsNoTracking()
            .OrderByDescending(i => i.CreatedAt)
            .Select(i => new
            {
                i.Id,
                i.UserId,
                i.Title,
                StudentName = i.User != null ? i.User.FullName : "Unknown Student",
                i.Domain,
                i.DifficultyLevel,
                i.FeasibilityScore,
                i.InnovationScore,
                i.IsSelected,
                i.CreatedAt
            })
            .ToListAsync();

        Ideas = ideaRows
            .Select(i => new IdeaRow(
                i.Id,
                i.UserId,
                i.Title,
                i.StudentName,
                i.Domain,
                i.DifficultyLevel,
                i.FeasibilityScore,
                i.InnovationScore,
                i.IsSelected,
                i.CreatedAt
            ))
            .ToList();
    }
}
