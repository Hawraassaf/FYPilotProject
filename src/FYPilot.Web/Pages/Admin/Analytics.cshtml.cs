using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Admin;

[Authorize(Roles = "admin")]
public class AnalyticsModel(ApplicationDbContext db) : PageModel
{
    public List<KPICard> KPIs { get; private set; } = [];
    public List<(string Domain, int Count, int Pct)> DomainDist     { get; private set; } = [];
    public List<(string Level, int Count, string Color)> DifficultyDist { get; private set; } = [];
    public List<IdeaAnalytics> TopIdeas { get; private set; } = [];

    public record KPICard(string Label, string Value, string Icon, string Color, string BgColor, int Change);
    public record IdeaAnalytics(string Title, string StudentName, string Domain,
        int InnovationScore, int FeasibilityScore, int MarketDemandScore);

    public async Task OnGetAsync()
    {
        var ideas   = await db.ProjectIdeas.Include(i => i.User).ToListAsync();
        var users   = await db.Users.CountAsync();
        var selected= ideas.Count(i => i.IsSelected);
        var avgFeas = ideas.Any() ? (int)ideas.Average(i => i.FeasibilityScore) : 0;

        KPIs =
        [
            new("Total Users",       users.ToString(),          "people",         "#3b82f6", "#eff6ff",  12),
            new("Total Ideas",       ideas.Count.ToString(),    "stars",          "#8b5cf6", "#f5f3ff",   8),
            new("Selected Projects", selected.ToString(),       "check-circle",   "#10b981", "#f0fdf4",   5),
            new("Avg Feasibility",   $"{avgFeas}%",             "graph-up",       "#f59e0b", "#fffbeb",   3),
        ];

        var byDomain = ideas.Where(i => !string.IsNullOrEmpty(i.Domain))
            .GroupBy(i => i.Domain).OrderByDescending(g => g.Count()).Take(8).ToList();
        var total = ideas.Count > 0 ? ideas.Count : 1;
        DomainDist = byDomain.Select(g => (g.Key, g.Count(), (int)(g.Count() * 100.0 / total))).ToList();

        DifficultyDist =
        [
            ("beginner",     ideas.Count(i => i.DifficultyLevel == "beginner"),     "#10b981"),
            ("intermediate", ideas.Count(i => i.DifficultyLevel == "intermediate"), "#f59e0b"),
            ("advanced",     ideas.Count(i => i.DifficultyLevel == "advanced"),     "#ef4444"),
        ];

        TopIdeas = ideas.Where(i => i.User != null)
            .OrderByDescending(i => (i.InnovationScore + i.FeasibilityScore + i.MarketDemandScore) / 3)
            .Take(10)
            .Select(i => new IdeaAnalytics(i.Title, i.User!.FullName, i.Domain, i.InnovationScore, i.FeasibilityScore, i.MarketDemandScore))
            .ToList();
    }
}
