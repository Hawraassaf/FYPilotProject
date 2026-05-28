using System.Security.Claims;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Supervisor;

[Authorize(Roles = "supervisor")]
public class DashboardModel(ApplicationDbContext db) : PageModel
{
    public List<StatCard>    Stats       { get; private set; } = [];
    public List<PendingItem> PendingIdeas{ get; private set; } = [];
    public List<EvalItem>    RecentEvals { get; private set; } = [];

    public record StatCard(string Label, string Value, string Icon, string Color, string BgColor);
    public record PendingItem(int IdeaId, string StudentName, string IdeaTitle, string Domain, int Feasibility, int Innovation, string Status);
    public record EvalItem(string StudentName, string IdeaTitle, string Status);

    public async Task OnGetAsync()
    {
        var supervisorId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        var evals   = await db.SupervisorEvaluations.Include(e => e.Idea).ThenInclude(i => i!.User)
                          .Where(e => e.SupervisorId == supervisorId).ToListAsync();
        var pending = evals.Count(e => e.Status == "pending");
        var approved= evals.Count(e => e.Status == "approved");

        Stats =
        [
            new("Pending Reviews", pending.ToString(),  "card-checklist", "#f59e0b", "#fffbeb"),
            new("Approved Ideas",  approved.ToString(), "check-circle",   "#10b981", "#f0fdf4"),
            new("Total Reviews",   evals.Count.ToString(),"star-half",    "#3b82f6", "#eff6ff"),
        ];

        PendingIdeas = evals.Where(e => e.Status == "pending").Select(e => new PendingItem(
            e.IdeaId,
            e.Idea?.User?.FullName ?? "Student",
            e.Idea?.Title ?? "Untitled",
            e.Idea?.Domain ?? "",
            e.Idea?.FeasibilityScore ?? 0,
            e.Idea?.InnovationScore ?? 0,
            e.Status)).ToList();

        RecentEvals = evals.Where(e => e.Status != "pending").OrderByDescending(e => e.UpdatedAt).Take(5)
            .Select(e => new EvalItem(
                e.Idea?.User?.FullName ?? "Student",
                e.Idea?.Title ?? "Untitled",
                e.Status)).ToList();
    }
}
