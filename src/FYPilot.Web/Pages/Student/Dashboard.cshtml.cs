using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DashboardModel(ApplicationDbContext db) : PageModel
{
    public string StudentName { get; private set; } = "Student";

    public List<ProjectIdea> Ideas { get; private set; } = [];
    public List<StatCard> Stats { get; private set; } = [];
    public List<RiskItem> RiskAlarms { get; private set; } = [];

    public ProjectIdea? SelectedIdea { get; private set; }

    public bool HasSkills { get; private set; }
    public bool HasIdeas { get; private set; }
    public bool HasSelectedProject { get; private set; }
    public bool HasRoadmaps { get; private set; }

    public record StatCard(string Label, string Value, string Icon);
    public record RiskItem(string Title, string Message, string Severity);

    public async Task OnGetAsync()
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

        StudentName = User.FindFirst(ClaimTypes.Name)?.Value ?? "Student";

        var totalIdeas = await db.ProjectIdeas
            .CountAsync(i => i.UserId == userId);

        var totalSkills = await db.StudentSkills
            .CountAsync(s => s.UserId == userId);

        var totalRoadmaps = await db.ProjectRoadmaps
            .CountAsync(r => r.UserId == userId);

        SelectedIdea = await db.ProjectIdeas
            .AsNoTracking()
            .Where(i => i.UserId == userId && i.IsSelected)
            .OrderByDescending(i => i.CreatedAt)
            .FirstOrDefaultAsync();

        Ideas = await db.ProjectIdeas
            .AsNoTracking()
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .Take(10)
            .ToListAsync();

        HasSkills = totalSkills > 0;
        HasIdeas = totalIdeas > 0;
        HasSelectedProject = SelectedIdea != null;
        HasRoadmaps = totalRoadmaps > 0;

        Stats =
        [
            new(
                "Ideas Generated",
                totalIdeas.ToString(),
                "lightbulb"
            ),

            new(
                "Skills Assessed",
                totalSkills.ToString(),
                "bar-chart-line"
            ),

            new(
                "Roadmaps Created",
                totalRoadmaps.ToString(),
                "map"
            ),

            new(
                "Selected Project",
                HasSelectedProject ? "1" : "0",
                "bookmark-check"
            )
        ];

        if (!HasSkills)
        {
            RiskAlarms.Add(new(
                "No Skills Assessed",
                "Complete your skill assessment so FYPilot can suggest suitable project ideas.",
                "high"
            ));
        }

        if (!HasIdeas)
        {
            RiskAlarms.Add(new(
                "No Ideas Yet",
                HasSkills
                    ? "Generate your first idea using your saved skills and profile."
                    : "Complete your skill assessment first, then generate your first project idea.",
                "medium"
            ));
        }

        if (!HasSelectedProject)
        {
            RiskAlarms.Add(new(
                "No Selected Project",
                HasIdeas
                    ? "Select one project idea as your final FYP direction."
                    : "Generate project ideas before selecting a final project.",
                "medium"
            ));
        }

        if (HasSelectedProject && !HasRoadmaps)
        {
            RiskAlarms.Add(new(
                "No Roadmap Created",
                "Generate a roadmap for your selected project to start tracking your progress.",
                "medium"
            ));
        }

        if (SelectedIdea != null && SelectedIdea.FeasibilityScore < 50)
        {
            RiskAlarms.Add(new(
                "Feasibility Risk",
                "Your selected project has a low feasibility score. Consider reducing scope or improving missing skills.",
                "high"
            ));
        }
    }
}