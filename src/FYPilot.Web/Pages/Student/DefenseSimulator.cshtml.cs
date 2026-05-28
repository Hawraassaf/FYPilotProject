using System.Security.Claims;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DefenseSimulatorModel(ApplicationDbContext db) : PageModel
{
    public ProjectIdea? Idea { get; private set; }
    public List<(string Question, string Answer, string Category)> QnA { get; private set; } = [];

    public async Task OnGetAsync(int? ideaId)
    {
        var userId = int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
        Idea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas.Where(i => i.UserId == userId).OrderByDescending(i => i.CreatedAt).FirstOrDefaultAsync();
        if (Idea == null) return;

        var domain = Idea.Domain;
        QnA =
        [
            ("Why is this problem worth solving?",
             $"The problem of {Idea.ProblemStatement.Substring(0, Math.Min(Idea.ProblemStatement.Length, 80))} directly impacts {Idea.TargetUsers}. Based on our market research, this gap represents a real unmet need with measurable impact.",
             "Motivation"),

            ("What makes your approach novel compared to existing solutions?",
             $"Our {domain} solution differentiates itself through {Idea.WhyUseful}. Existing tools lack this specific capability or are not adapted to the target context.",
             "Novelty"),

            ("Why did you choose these specific technologies?",
             $"We selected {Idea.RequiredTechnologies} because they best balance performance, developer productivity, and community support. {(Idea.Domain.Contains("AI") ? "The ML components require Python's scikit-learn ecosystem for rapid prototyping." : "The stack is well-suited to our team's existing expertise.")}",
             "Technology"),

            ("How will you evaluate your system's success?",
             $"We will measure success through: (1) functional completion of all MVP features, (2) user acceptance testing with at least 10 real users, (3) performance benchmarks, and (4) academic evaluation criteria from our supervisor.",
             "Evaluation"),

            ("What is your dataset, and how did you validate it?",
             string.IsNullOrEmpty(Idea.DatasetNeeded) || Idea.DatasetNeeded == "None"
                 ? "This project does not require a custom dataset. It integrates with existing public APIs and generates data through user interactions."
                 : $"Our dataset ({Idea.DatasetNeeded}) was collected and validated through data quality checks, outlier detection, and cross-referencing with authoritative sources.",
             "Data"),

            ("What can you realistically deliver in one semester?",
             $"Our MVP targets {string.Join(", ", Idea.FinalDeliverables.Split(',').Take(3).Select(s => s.Trim()))} within {Math.Min(Idea.ExpectedDurationWeeks, 16)} weeks, following an Agile methodology with 2-week sprints.",
             "Scope"),

            ("What are the main risks, and how do you mitigate them?",
             $"Primary risks include: {(string.IsNullOrEmpty(Idea.MissingSkills) ? "timeline slippage and scope creep" : $"skill gap in {Idea.MissingSkills.Split(',').First()}")}. Mitigations: weekly supervisor check-ins, strict MVP scope control, and early technical prototyping.",
             "Risk"),
        ];
    }
}
