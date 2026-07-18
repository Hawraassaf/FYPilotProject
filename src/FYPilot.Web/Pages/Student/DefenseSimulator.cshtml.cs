using System.Security.Claims;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DefenseSimulatorModel(
    ApplicationDbContext db,
    IAiServiceClient aiService
) : PageModel
{
    public ProjectIdea? Idea { get; private set; }

    public List<(string q, string a, string category)> QnA { get; private set; } = [];

    public string? ErrorMessage { get; private set; }

    public async Task OnGetAsync()
    {
        await LoadContextAsync();

        if (Idea != null)
        {
            await GenerateQuestionsAsync();
        }
    }

    private async Task LoadContextAsync()
    {
        var userId = UserId();

        Idea = await db.ProjectIdeas
            .FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
            ?? await db.ProjectIdeas
                .Where(i => i.UserId == userId)
                .OrderByDescending(i => i.CreatedAt)
                .FirstOrDefaultAsync();
    }

    private async Task GenerateQuestionsAsync()
    {
        if (Idea == null)
        {
            QnA = [];
            return;
        }

        var skills = await LoadSkillNamesAsync();
        var roadmapPhases = await LoadRoadmapPhaseNamesAsync(Idea.Id);

        var request = new DefenseGenerateQuestionsRequest(
            ProjectTitle: Idea.Title,
            ProblemStatement: Idea.ProblemStatement,
            TargetUsers: Idea.TargetUsers,
            Technologies: Idea.RequiredTechnologies,
            Domain: Idea.Domain,
            DifficultyLevel: Idea.DifficultyLevel,
            Skills: skills,
            RoadmapPhases: roadmapPhases,
            DocumentationSummary: BuildDocumentationSummary(Idea),
            NumberOfQuestions: 6
        );

        try
        {
            var response = await aiService.GenerateDefenseQuestionsAsync(request);

            if (response?.Questions != null && response.Questions.Any())
            {
                QnA = response.Questions
                    .Select(q => (
                        q: q.Question,
                        a: BuildSuggestedAnswer(q, Idea),
                        category: string.IsNullOrWhiteSpace(q.Category)
                            ? "General"
                            : q.Category
                    ))
                    .ToList();

                return;
            }

            ErrorMessage = response?.Error ?? "Defense Simulator returned no questions.";
            QnA = BuildFallbackQuestions(Idea);
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Defense Simulator backend error: {ex.Message}";
            QnA = BuildFallbackQuestions(Idea);
        }
    }

    private async Task<List<string>> LoadSkillNamesAsync()
    {
        var userId = UserId();

        return await db.StudentSkills
            .Where(s => s.UserId == userId)
            .OrderByDescending(s => s.Rating)
            .Select(s => s.SkillName)
            .ToListAsync();
    }

    private async Task<List<string>> LoadRoadmapPhaseNamesAsync(int ideaId)
    {
        var roadmap = await db.ProjectRoadmaps
            .FirstOrDefaultAsync(r => r.IdeaId == ideaId);

        if (roadmap == null)
        {
            return [];
        }

        return await db.RoadmapPhases
            .Where(p => p.RoadmapId == roadmap.Id)
            .OrderBy(p => p.PhaseNumber)
            .Select(p => p.Name)
            .ToListAsync();
    }

    private static string BuildDocumentationSummary(ProjectIdea idea)
    {
        return $"""
        Project title: {idea.Title}
        Problem statement: {idea.ProblemStatement}
        Target users: {idea.TargetUsers}
        Domain: {idea.Domain}
        Required technologies: {idea.RequiredTechnologies}
        Required skills: {idea.RequiredSkills}
        Missing skills: {idea.MissingSkills}
        Difficulty: {idea.DifficultyLevel}
        Final deliverables: {idea.FinalDeliverables}
        """;
    }

    private static string BuildSuggestedAnswer(
        DefenseQuestionDto question,
        ProjectIdea idea
    )
    {
        var points = question.ExpectedPoints
            .Where(p => !string.IsNullOrWhiteSpace(p))
            .ToList();

        if (points.Any())
        {
            return "A strong answer should mention: "
                   + string.Join("; ", points)
                   + ". Then connect the answer directly to the project problem, target users, and chosen technologies.";
        }

        return $"Start by linking the answer to {idea.Title}. Explain the problem, justify your technical choice, mention the expected benefit, and finish with one limitation or future improvement.";
    }

    private static List<(string q, string a, string category)> BuildFallbackQuestions(ProjectIdea idea)
    {
        return
        [
            (
                $"What problem does {idea.Title} solve?",
                $"Explain the real problem first, then mention who is affected by it. Connect the problem to the target users: {idea.TargetUsers}.",
                "Problem clarity"
            ),
            (
                "Why did you choose this project idea?",
                "Mention the usefulness of the idea, its academic value, and how it matches your skills and final year project goals.",
                "Motivation"
            ),
            (
                $"Why are these technologies suitable: {idea.RequiredTechnologies}?",
                "Justify each main technology by explaining what role it plays in the system, not only by listing tool names.",
                "Technical choices"
            ),
            (
                "What are the main risks or limitations of your project?",
                "Mention technical difficulty, time constraints, data limitations, testing challenges, and what you would improve later.",
                "Limitations"
            ),
            (
                "How will you prove that your project works?",
                "Explain your testing plan, success criteria, user feedback, and how you will compare expected behavior with actual results.",
                "Evaluation"
            ),
            (
                "What makes your project different from existing solutions?",
                "Discuss the project context, target users, AI support if applicable, and the specific value your system adds.",
                "Originality"
            )
        ];
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}