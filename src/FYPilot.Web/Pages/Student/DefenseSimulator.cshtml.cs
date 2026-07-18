using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class DefenseSimulatorModel(
    ApplicationDbContext db,
    IAiServiceClient aiService
) : PageModel
{
    private static readonly JsonSerializerOptions SessionJsonOptions =
        new(JsonSerializerDefaults.Web);

    public ProjectIdea? Idea { get; private set; }

    public DefensePracticeState State { get; private set; } = new();

    public string? ErrorMessage { get; private set; }

    public string? SuccessMessage { get; private set; }

    [BindProperty]
    public int NumberOfQuestions { get; set; } = 3;

    [BindProperty]
    public List<string> FocusAreas { get; set; } = [];

    [BindProperty]
    public int QuestionIndex { get; set; }

    [BindProperty]
    public string StudentAnswer { get; set; } = "";

    public IReadOnlyList<DefenseFocusAreaOption> AvailableFocusAreas { get; } =
    [
        new("Database Design", "Database Design", "Entities, relationships, normalization, indexing, integrity.", "bi-database"),
        new("Backend Development", "Backend Development", "Services, validation, authorization, errors, performance.", "bi-braces"),
        new("Frontend and UX", "Frontend & UX", "Razor Pages, usability, accessibility, responsive behavior.", "bi-window"),
        new("API and Integration", "API & Integration", "REST contracts, Python/.NET communication, external services.", "bi-diagram-3"),
        new("Business Logic", "Business Logic", "Rules, workflows, algorithms, edge cases, consistency.", "bi-bezier2"),
        new("System Architecture", "System Architecture", "Layers, boundaries, scalability, maintainability.", "bi-boxes"),
        new("Security", "Security", "Authentication, authorization, data protection, threat handling.", "bi-shield-lock"),
        new("AI and Machine Learning", "AI & Machine Learning", "Models, prompts, evaluation, data, limitations.", "bi-cpu"),
        new("Testing and Validation", "Testing & Validation", "Unit, integration, acceptance, AI output validation.", "bi-clipboard-check"),
        new("Deployment and DevOps", "Deployment & DevOps", "Configuration, secrets, monitoring, reliability.", "bi-cloud-arrow-up"),
        new("Requirements and Problem", "Requirements & Problem", "Problem validation, users, scope, requirements.", "bi-bullseye"),
        new("Feasibility and Limitations", "Feasibility & Limits", "Risks, constraints, trade-offs, future work.", "bi-exclamation-diamond")
    ];

    public int EvaluatedCount =>
        State.Questions.Count(question => question.Evaluation != null);

    public int CompletionPercent =>
        State.Questions.Count == 0
            ? 0
            : (int)Math.Round(
                EvaluatedCount * 100d / State.Questions.Count
            );

    public string ProviderLabel =>
        State.Source switch
        {
            var value when value.Contains("groq", StringComparison.OrdinalIgnoreCase)
                => "Groq cloud",
            var value when value.Contains("gemini", StringComparison.OrdinalIgnoreCase)
                => "Gemini fallback",
            var value when value.Contains("ollama", StringComparison.OrdinalIgnoreCase)
                => "Ollama local fallback",
            var value when value.Contains("fallback", StringComparison.OrdinalIgnoreCase)
                => "Built-in fallback",
            _ => "AI provider"
        };

    public async Task OnGetAsync()
    {
        await LoadContextAsync();
        LoadPracticeState();
    }

    public async Task<IActionResult> OnPostGenerateAsync()
    {
        await LoadContextAsync();
        LoadPracticeState();

        if (Idea == null)
        {
            ErrorMessage = "Select a project idea before starting a defense session.";
            return Page();
        }

        NumberOfQuestions = Math.Clamp(NumberOfQuestions, 1, 5);

        FocusAreas = FocusAreas
            .Where(area => !string.IsNullOrWhiteSpace(area))
            .Select(area => area.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        if (FocusAreas.Count == 0)
        {
            ErrorMessage = "Choose at least one defense field.";
            return Page();
        }

        await GenerateQuestionSetAsync(previousQuestions: []);

        return Page();
    }

    public async Task<IActionResult> OnPostRegenerateAsync()
    {
        await LoadContextAsync();
        LoadPracticeState();

        if (Idea == null || State.Questions.Count == 0)
        {
            return RedirectToPage();
        }

        NumberOfQuestions = Math.Clamp(State.NumberOfQuestions, 1, 5);
        FocusAreas = [.. State.FocusAreas];

        var previousQuestions = State.Questions
            .Select(item => item.Question.Question)
            .Where(question => !string.IsNullOrWhiteSpace(question))
            .ToList();

        await GenerateQuestionSetAsync(previousQuestions);

        return Page();
    }

    public async Task<IActionResult> OnPostEvaluateAsync()
    {
        await LoadContextAsync();
        LoadPracticeState();

        if (Idea == null)
        {
            ErrorMessage = "The selected project could not be loaded.";
            return Page();
        }

        if (QuestionIndex < 0 || QuestionIndex >= State.Questions.Count)
        {
            ErrorMessage = "The selected defense question is no longer available.";
            return Page();
        }

        StudentAnswer = StudentAnswer?.Trim() ?? "";

        if (StudentAnswer.Length < 20)
        {
            ErrorMessage =
                "Write a more complete answer before evaluation (at least 20 characters).";
            return Page();
        }

        var context = await BuildAiContextAsync();
        var questionState = State.Questions[QuestionIndex];

        try
        {
            var response = await aiService.EvaluateDefenseAnswerAsync(
                new DefenseEvaluateAnswerRequest(
                    Question: questionState.Question,
                    StudentAnswer: StudentAnswer,
                    StudentProfile: context.StudentProfile,
                    SelectedIdea: context.SelectedIdea,
                    Mode: "mixed",
                    Model: ""
                )
            );

            if (response == null)
            {
                ErrorMessage = "The evaluator returned no result.";
                return Page();
            }

            questionState.StudentAnswer = StudentAnswer;
            questionState.Evaluation = new DefenseEvaluationViewModel
            {
                Score = Math.Clamp(response.Score, 0, 100),
                SimilarityScore = response.SimilarityScore is null
                    ? null
                    : Math.Clamp(response.SimilarityScore.Value, 0, 100),
                ConfidenceScore = response.ConfidenceScore is null
                    ? null
                    : Math.Clamp(response.ConfidenceScore.Value, 0, 100),
                Level = string.IsNullOrWhiteSpace(response.Level)
                    ? "Evaluated"
                    : response.Level,
                Strengths = response.Strengths ?? [],
                MissingPoints = response.MissingPoints ?? [],
                ImprovedAnswer = response.ImprovedAnswer ?? "",
                FollowUpQuestion = response.FollowUpQuestion ?? "",
                FeedbackSummary = response.FeedbackSummary ?? "",
                Source = response.Source ?? "",
                ModelUsed = response.ModelUsed ?? ""
            };

            SavePracticeState();

            SuccessMessage =
                $"Question {QuestionIndex + 1} was evaluated successfully.";
        }
        catch (Exception ex)
        {
            ErrorMessage = $"Answer evaluation failed: {ex.Message}";
        }

        return Page();
    }

    public IActionResult OnPostReset()
    {
        HttpContext.Session.Remove(SessionKey());
        return RedirectToPage();
    }

    private async Task GenerateQuestionSetAsync(
        List<string> previousQuestions
    )
    {
        if (Idea == null)
        {
            return;
        }

        var context = await BuildAiContextAsync();

        var request = new DefenseGenerateQuestionsRequest(
            StudentProfile: context.StudentProfile,
            SelectedIdea: context.SelectedIdea,
            Roadmap: context.Roadmap,
            SeDocumentation: new Dictionary<string, object>
            {
                ["summary"] = BuildDocumentationSummary(Idea)
            },
            FocusAreas: FocusAreas,
            PreviousQuestions: previousQuestions,
            Mode: "mixed",
            NumberOfQuestions: NumberOfQuestions,
            Model: ""
        );

        try
        {
            var response =
                await aiService.GenerateDefenseQuestionsAsync(request);

            var questions = response?.Questions?
                .Where(question =>
                    !string.IsNullOrWhiteSpace(question.Question)
                )
                .Take(NumberOfQuestions)
                .ToList()
                ?? [];

            if (questions.Count == 0)
            {
                ErrorMessage =
                    response?.Error
                    ?? response?.ProviderError
                    ?? response?.OllamaError
                    ?? response?.Message
                    ?? "The Defense Simulator returned no questions.";

                return;
            }

            State = new DefensePracticeState
            {
                NumberOfQuestions = NumberOfQuestions,
                FocusAreas = [.. FocusAreas],
                Questions = questions
                    .Select(question => new DefensePracticeQuestionState
                    {
                        Question = question
                    })
                    .ToList(),
                Source = response?.Source ?? "",
                ModelUsed = response?.ModelUsed ?? "",
                GeneratedAt = DateTimeOffset.UtcNow
            };

            SavePracticeState();

            SuccessMessage =
                $"{questions.Count} new defense question(s) generated.";
        }
        catch (Exception ex)
        {
            ErrorMessage =
                $"Defense Simulator backend error: {ex.Message}";
        }
    }

    private async Task<DefenseAiContext> BuildAiContextAsync()
    {
        if (Idea == null)
        {
            throw new InvalidOperationException(
                "A project idea is required."
            );
        }

        var userId = UserId();

        var profile = await db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(item => item.UserId == userId);

        var skillRows = await db.StudentSkills
            .AsNoTracking()
            .Where(item => item.UserId == userId)
            .OrderByDescending(item => item.Rating)
            .ThenBy(item => item.SkillName)
            .ToListAsync();

        var skills = skillRows
            .Where(item => !string.IsNullOrWhiteSpace(item.SkillName))
            .Select(item => item.SkillName.Trim())
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var ratings = skillRows
            .Where(item => !string.IsNullOrWhiteSpace(item.SkillName))
            .GroupBy(
                item => item.SkillName.Trim(),
                StringComparer.OrdinalIgnoreCase
            )
            .ToDictionary(
                group => group.Key,
                group => group.Max(item => item.Rating),
                StringComparer.OrdinalIgnoreCase
            );

        var roadmap = await LoadRoadmapAsync(Idea.Id);

        var availableHours =
            profile?.AvailableHoursPerWeek ?? 10;

        return new DefenseAiContext(
            StudentProfile: new DefenseStudentProfileRequest(
                Major: string.IsNullOrWhiteSpace(profile?.Major)
                    ? "Computer Science"
                    : profile.Major,
                ExperienceLevel:
                    string.IsNullOrWhiteSpace(profile?.ExperienceLevel)
                        ? "intermediate"
                        : profile.ExperienceLevel.ToLowerInvariant(),
                TeamSize: 1,
                AvailableHoursPerWeek:
                    availableHours > 0 ? availableHours : 10,
                Skills: skills,
                SkillRatings: ratings
            ),
            SelectedIdea: new DefenseSelectedIdeaRequest(
                Id: Idea.Id,
                Title: Idea.Title ?? "",
                ProblemStatement: Idea.ProblemStatement ?? "",
                TargetUsers: Idea.TargetUsers ?? "",
                WhyUseful: Idea.WhyUseful ?? "",
                RequiredTechnologies:
                    Idea.RequiredTechnologies ?? "",
                RequiredSkills: Idea.RequiredSkills ?? "",
                MissingSkills: Idea.MissingSkills ?? "",
                DifficultyLevel: Idea.DifficultyLevel ?? "",
                ExpectedDurationWeeks:
                    Idea.ExpectedDurationWeeks > 0
                        ? Idea.ExpectedDurationWeeks
                        : 10,
                Domain: Idea.Domain ?? "",
                FinalDeliverables: Idea.FinalDeliverables ?? ""
            ),
            Roadmap: roadmap
        );
    }

    private async Task<List<DefenseRoadmapPhaseRequest>>
        LoadRoadmapAsync(int ideaId)
    {
        var roadmap = await db.ProjectRoadmaps
            .AsNoTracking()
            .FirstOrDefaultAsync(item => item.IdeaId == ideaId);

        if (roadmap == null)
        {
            return [];
        }

        var phases = await db.RoadmapPhases
            .AsNoTracking()
            .Where(item => item.RoadmapId == roadmap.Id)
            .OrderBy(item => item.PhaseNumber)
            .Select(item => new
            {
                item.PhaseNumber,
                item.Name
            })
            .ToListAsync();

        return phases
            .Select(item => new DefenseRoadmapPhaseRequest(
                PhaseNumber: item.PhaseNumber,
                Name: item.Name ?? "",
                Objective: "",
                Tasks: [],
                ExpectedOutput: "",
                SuccessCriteria: "",
                IsCompleted: false
            ))
            .ToList();
    }

    private async Task LoadContextAsync()
    {
        var userId = UserId();

        Idea = await db.ProjectIdeas
            .AsNoTracking()
            .FirstOrDefaultAsync(item =>
                item.UserId == userId &&
                item.IsSelected
            )
            ?? await db.ProjectIdeas
                .AsNoTracking()
                .Where(item => item.UserId == userId)
                .OrderByDescending(item => item.CreatedAt)
                .ThenByDescending(item => item.Id)
                .FirstOrDefaultAsync();
    }

    private static string BuildDocumentationSummary(
        ProjectIdea idea
    )
    {
        return $"""
        Project title: {idea.Title}
        Problem statement: {idea.ProblemStatement}
        Target users: {idea.TargetUsers}
        Why useful: {idea.WhyUseful}
        Domain: {idea.Domain}
        Required technologies: {idea.RequiredTechnologies}
        Required skills: {idea.RequiredSkills}
        Missing skills: {idea.MissingSkills}
        Difficulty: {idea.DifficultyLevel}
        Expected duration: {idea.ExpectedDurationWeeks} weeks
        Final deliverables: {idea.FinalDeliverables}
        """;
    }

    private void LoadPracticeState()
    {
        var json = HttpContext.Session.GetString(SessionKey());

        if (string.IsNullOrWhiteSpace(json))
        {
            State = new DefensePracticeState();
            return;
        }

        try
        {
            State =
                JsonSerializer.Deserialize<DefensePracticeState>(
                    json,
                    SessionJsonOptions
                )
                ?? new DefensePracticeState();
        }
        catch (JsonException)
        {
            State = new DefensePracticeState();
            HttpContext.Session.Remove(SessionKey());
        }

        NumberOfQuestions =
            State.NumberOfQuestions is >= 1 and <= 5
                ? State.NumberOfQuestions
                : 3;

        FocusAreas = [.. State.FocusAreas];
    }

    private void SavePracticeState()
    {
        var json = JsonSerializer.Serialize(
            State,
            SessionJsonOptions
        );

        HttpContext.Session.SetString(SessionKey(), json);
    }

    private string SessionKey() =>
        $"DefenseSimulator:{UserId()}";

    private int UserId() =>
        int.Parse(
            User.FindFirst(ClaimTypes.NameIdentifier)!.Value
        );
}

public sealed record DefenseFocusAreaOption(
    string Value,
    string Title,
    string Description,
    string Icon
);

public sealed record DefenseAiContext(
    DefenseStudentProfileRequest StudentProfile,
    DefenseSelectedIdeaRequest SelectedIdea,
    List<DefenseRoadmapPhaseRequest> Roadmap
);

public sealed class DefensePracticeState
{
    public int NumberOfQuestions { get; set; } = 3;

    public List<string> FocusAreas { get; set; } = [];

    public List<DefensePracticeQuestionState> Questions { get; set; } = [];

    public string Source { get; set; } = "";

    public string ModelUsed { get; set; } = "";

    public DateTimeOffset GeneratedAt { get; set; }
}

public sealed class DefensePracticeQuestionState
{
    public DefenseQuestionDto Question { get; set; } =
        new("", "", "", [], "");

    public string StudentAnswer { get; set; } = "";

    public DefenseEvaluationViewModel? Evaluation { get; set; }
}

public sealed class DefenseEvaluationViewModel
{
    public int Score { get; set; }

    public int? SimilarityScore { get; set; }

    public int? ConfidenceScore { get; set; }

    public string Level { get; set; } = "";

    public List<string> Strengths { get; set; } = [];

    public List<string> MissingPoints { get; set; } = [];

    public string ImprovedAnswer { get; set; } = "";

    public string FollowUpQuestion { get; set; } = "";

    public string FeedbackSummary { get; set; } = "";

    public string Source { get; set; } = "";

    public string ModelUsed { get; set; } = "";
}