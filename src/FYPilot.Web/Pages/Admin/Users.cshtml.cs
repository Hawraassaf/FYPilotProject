using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class UsersModel(ApplicationDbContext db) : PageModel
{
    public List<UserRow> Users { get; private set; } = [];

    public record UserRow(int Id, string FullName, string Email, string Role, int IdeaCount, DateTime CreatedAt);

    public async Task OnGetAsync()
    {
        var users = await db.Users.ToListAsync();
        var ideaCounts = await db.ProjectIdeas.GroupBy(i => i.UserId)
            .Select(g => new { UserId = g.Key, Count = g.Count() }).ToListAsync();

        Users = users.Select(u => new UserRow(
            u.Id, u.FullName, u.Email, u.Role,
            ideaCounts.FirstOrDefault(ic => ic.UserId == u.Id)?.Count ?? 0,
            u.CreatedAt)).ToList();
    }
}
