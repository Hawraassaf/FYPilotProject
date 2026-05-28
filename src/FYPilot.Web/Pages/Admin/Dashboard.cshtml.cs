using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class DashboardModel(ApplicationDbContext db) : PageModel
{
    public List<StatCard>  Stats       { get; private set; } = [];
    public List<UserRow>   RecentUsers { get; private set; } = [];
    public List<ProjectIdea> RecentIdeas { get; private set; } = [];

    public record StatCard(string Label, string Value, string Icon, string Color, string BgColor);
    public record UserRow(string Name, string Role, DateTime CreatedAt);

    public async Task OnGetAsync()
    {
        var totalUsers    = await db.Users.CountAsync();
        var totalStudents = await db.Users.CountAsync(u => u.Role == "student");
        var totalSups     = await db.Users.CountAsync(u => u.Role == "supervisor");
        var totalIdeas    = await db.ProjectIdeas.CountAsync();

        Stats =
        [
            new("Total Users",       totalUsers.ToString(),    "people",         "#3b82f6", "#eff6ff"),
            new("Students",          totalStudents.ToString(), "person-badge",   "#8b5cf6", "#f5f3ff"),
            new("Supervisors",       totalSups.ToString(),     "person-check",   "#10b981", "#f0fdf4"),
            new("Project Ideas",     totalIdeas.ToString(),    "stars",          "#f59e0b", "#fffbeb"),
        ];

        RecentUsers = await db.Users.OrderByDescending(u => u.CreatedAt).Take(8)
            .Select(u => new UserRow(u.FullName, u.Role, u.CreatedAt)).ToListAsync();

        RecentIdeas = await db.ProjectIdeas.OrderByDescending(i => i.CreatedAt).Take(8).ToListAsync();
    }
}
