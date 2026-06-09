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
public class MentorChatModel(
    ApplicationDbContext db,
    IAiServiceClient aiService
) : PageModel
{
    public List<ChatMessage> Messages { get; private set; } = [];

    public List<ProjectIdea> Ideas { get; private set; } = [];

    public ProjectIdea? SelectedIdea { get; private set; }

    public int? SelectedIdeaId { get; private set; }

    public StudentProfile? Profile { get; private set; }

    public List<StudentSkill> Skills { get; private set; } = [];

    public FypMentorServiceResponse? MentorResponse { get; private set; }

    public FypMentorAnswerDto? MentorAnswer => MentorResponse?.Answer;

    public string? ErrorMessage { get; private set; }

    [BindProperty]
    public string MessageText { get; set; } = "";

    [BindProperty]
    public string TargetFile { get; set; } = "";

    [BindProperty]
    public string CodeLanguage { get; set; } = "";

    [BindProperty]
    public string ExistingCode { get; set; } = "";

    [BindProperty]
    public string RequestedChange { get; set; } = "";

    [BindProperty]
    public string ConstraintsText { get; set; } = "";

    public async Task OnGetAsync(int? ideaId)
    {
        await LoadAsync(ideaId);
    }

    public async Task<IActionResult> OnPostAsync(string? message, int? ideaId)
    {
        return await HandleSendAsync(message, ideaId);
    }

    public async Task<IActionResult> OnPostSendAsync(string? message, int? ideaId)
    {
        return await HandleSendAsync(message, ideaId);
    }

    private async Task<IActionResult> HandleSendAsync(string? message, int? ideaId)
    {
        await LoadAsync(ideaId);

        var finalMessage = !string.IsNullOrWhiteSpace(MessageText)
            ? MessageText.Trim()
            : (message ?? "").Trim();

        if (string.IsNullOrWhiteSpace(finalMessage))
        {
            ErrorMessage = "Please write a message before sending.";
            return Page();
        }

        var userId = UserId();

        var recentMessages = Messages
            .OrderBy(m => m.CreatedAt)
            .TakeLast(8)
            .Select(m => new MentorRecentMessageDto(
                Role: m.Role,
                Content: m.Content
            ))
            .ToList();

        db.ChatMessages.Add(new ChatMessage
        {
            UserId = userId,
            IdeaId = SelectedIdeaId,
            Role = "user",
            Content = finalMessage
        });

        var request = new FypMentorRequest(
            Message: finalMessage,
            StudentProfile: BuildStudentProfileDto(),
            SelectedIdea: BuildSelectedIdeaDto(),
            DnaSummary: null,
            Roadmap: await LoadRoadmapAsync(SelectedIdeaId),
            RecentMessages: recentMessages,
            CodeContext: BuildCodeContextDto()
        );

        MentorResponse = await aiService.AskFypMentorAsync(request);

        if (MentorResponse == null)
        {
            ErrorMessage = "The FYP Mentor Chat could not respond. Make sure the Python AI service is running.";

            db.ChatMessages.Add(new ChatMessage
            {
                UserId = userId,
                IdeaId = SelectedIdeaId,
                Role = "assistant",
                Content = ErrorMessage
            });

            await db.SaveChangesAsync();
            await LoadAsync(SelectedIdeaId);

            return Page();
        }

        var assistantContent = BuildAssistantMessageContent(MentorResponse.Answer);

        db.ChatMessages.Add(new ChatMessage
        {
            UserId = userId,
            IdeaId = SelectedIdeaId,
            Role = "assistant",
            Content = assistantContent
        });

        await db.SaveChangesAsync();
        await LoadAsync(SelectedIdeaId);

        return Page();
    }

    private async Task LoadAsync(int? ideaId)
    {
        var userId = UserId();

        Profile = await db.StudentProfiles
            .FirstOrDefaultAsync(p => p.UserId == userId);

        Skills = await db.StudentSkills
            .Where(s => s.UserId == userId)
            .ToListAsync();

        Ideas = await db.ProjectIdeas
            .Where(i => i.UserId == userId)
            .OrderByDescending(i => i.CreatedAt)
            .Take(12)
            .ToListAsync();

        SelectedIdea = ideaId.HasValue
            ? await db.ProjectIdeas.FirstOrDefaultAsync(i => i.Id == ideaId.Value && i.UserId == userId)
            : await db.ProjectIdeas.FirstOrDefaultAsync(i => i.UserId == userId && i.IsSelected)
              ?? await db.ProjectIdeas
                  .Where(i => i.UserId == userId)
                  .OrderByDescending(i => i.CreatedAt)
                  .FirstOrDefaultAsync();

        SelectedIdeaId = SelectedIdea?.Id;

        Messages = await db.ChatMessages
            .Where(m => m.UserId == userId && m.IdeaId == SelectedIdeaId)
            .OrderBy(m => m.CreatedAt)
            .Take(50)
            .ToListAsync();
    }

    private MentorStudentProfileDto? BuildStudentProfileDto()
    {
        if (Profile == null && Skills.Count == 0)
        {
            return null;
        }

        var studentSkills = Skills
            .Select(s => GetString(s, "SkillName", "Name", "Title"))
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct()
            .ToList();

        var skillRatings = new Dictionary<string, int>();

        foreach (var skill in Skills)
        {
            var skillName = GetString(skill, "SkillName", "Name", "Title");

            if (string.IsNullOrWhiteSpace(skillName))
            {
                continue;
            }

            var rating = GetInt(skill, 3, "Rating", "Level", "SkillLevel", "Score");
            skillRatings[skillName] = rating;
        }

        return new MentorStudentProfileDto(
            Major: GetString(Profile, "Major"),
            ExperienceLevel: GetString(Profile, "ExperienceLevel"),
            TeamSize: GetInt(Profile, 1, "TeamMembers", "TeamSize"),
            AvailableHoursPerWeek: GetInt(Profile, 10, "AvailableHoursPerWeek"),
            Skills: studentSkills,
            SkillRatings: skillRatings
        );
    }

    private MentorSelectedIdeaDto? BuildSelectedIdeaDto()
    {
        if (SelectedIdea == null)
        {
            return null;
        }

        return new MentorSelectedIdeaDto(
            Id: SelectedIdea.Id,
            Title: GetString(SelectedIdea, "Title", "IdeaTitle", "Name"),
            ProblemStatement: GetString(SelectedIdea, "ProblemStatement", "Problem", "Description"),
            TargetUsers: GetString(SelectedIdea, "TargetUsers", "Users"),
            WhyUseful: GetString(SelectedIdea, "WhyUseful", "Usefulness", "Value"),
            RequiredTechnologies: GetString(SelectedIdea, "RequiredTechnologies", "Technologies", "TechStack"),
            RequiredSkills: GetString(SelectedIdea, "RequiredSkills"),
            MissingSkills: GetString(SelectedIdea, "MissingSkills"),
            DifficultyLevel: GetString(SelectedIdea, "DifficultyLevel", "Difficulty"),
            ExpectedDurationWeeks: GetInt(SelectedIdea, 10, "ExpectedDurationWeeks", "DurationWeeks"),
            Domain: GetString(SelectedIdea, "Domain", "ProjectDomain"),
            FinalDeliverables: GetString(SelectedIdea, "FinalDeliverables", "Deliverables")
        );
    }

    private MentorCodeContextDto? BuildCodeContextDto()
    {
        var hasCodeContext =
            !string.IsNullOrWhiteSpace(TargetFile)
            || !string.IsNullOrWhiteSpace(CodeLanguage)
            || !string.IsNullOrWhiteSpace(ExistingCode)
            || !string.IsNullOrWhiteSpace(RequestedChange)
            || !string.IsNullOrWhiteSpace(ConstraintsText);

        if (!hasCodeContext)
        {
            return null;
        }

        var constraints = ConstraintsText
            .Split(["\r\n", "\n"], StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.Trim())
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .ToList();

        return new MentorCodeContextDto(
            TargetFile: TargetFile.Trim(),
            Language: string.IsNullOrWhiteSpace(CodeLanguage) ? "csharp" : CodeLanguage.Trim(),
            ExistingCode: ExistingCode,
            RequestedChange: RequestedChange.Trim(),
            Constraints: constraints
        );
    }

    private async Task<List<MentorRoadmapPhaseDto>> LoadRoadmapAsync(int? ideaId)
    {
        if (!ideaId.HasValue)
        {
            return [];
        }

        try
        {
            var roadmap = await db.Set<ProjectRoadmap>()
                .FirstOrDefaultAsync(r => EF.Property<int>(r, "ProjectIdeaId") == ideaId.Value);

            if (roadmap == null)
            {
                return [];
            }

            var roadmapId = GetInt(roadmap, 0, "Id");

            var phases = await db.Set<RoadmapPhase>()
                .ToListAsync();

            return phases
                .Where(p => GetInt(p, 0, "ProjectRoadmapId", "RoadmapId") == roadmapId)
                .OrderBy(p => GetInt(p, 0, "WeekNumber", "PhaseNumber", "Order"))
                .Select((p, index) => new MentorRoadmapPhaseDto(
                    PhaseNumber: GetInt(p, index + 1, "WeekNumber", "PhaseNumber", "Order"),
                    Name: GetString(p, "PhaseTitle", "Title", "Name"),
                    Objective: GetString(p, "MainGoal", "Objective", "Goal", "Description"),
                    Tasks: GetStringList(p, "Tasks", "TasksJson"),
                    ExpectedOutput: GetString(p, "ExpectedOutput", "Deliverable", "Deliverables"),
                    SuccessCriteria: GetString(p, "Checkpoint", "SuccessCriteria", "Criteria"),
                    IsCompleted: GetBool(p, "IsCompleted", "Completed")
                ))
                .ToList();
        }
        catch
        {
            return [];
        }
    }

    private static string BuildAssistantMessageContent(FypMentorAnswerDto answer)
    {
        var content = answer.Reply;

        if (answer.SuggestedNextActions.Any())
        {
            content += "\n\nSuggested next actions:\n";
            content += string.Join("\n", answer.SuggestedNextActions.Select(a => $"- {a}"));
        }

        if (!string.IsNullOrWhiteSpace(answer.Warning))
        {
            content += $"\n\nWarning: {answer.Warning}";
        }

        if (answer.CodeBlocks.Any())
        {
            content += "\n\nGenerated code:\n";

            foreach (var block in answer.CodeBlocks)
            {
                content += $"\nTarget file: {block.TargetFile}\n";
                content += $"{block.Content}\n";
            }
        }

        return content;
    }

    private int UserId()
        => int.Parse(User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

    private static string GetString(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            return value.ToString() ?? "";
        }

        return "";
    }

    private static int GetInt(object? obj, int defaultValue, params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is int intValue)
            {
                return intValue;
            }

            if (value is double doubleValue)
            {
                return Convert.ToInt32(doubleValue);
            }

            if (value is decimal decimalValue)
            {
                return Convert.ToInt32(decimalValue);
            }

            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static bool GetBool(object? obj, params string[] propertyNames)
    {
        if (obj == null)
        {
            return false;
        }

        foreach (var propertyName in propertyNames)
        {
            var property = obj.GetType().GetProperty(propertyName);

            if (property == null)
            {
                continue;
            }

            var value = property.GetValue(obj);

            if (value == null)
            {
                continue;
            }

            if (value is bool boolValue)
            {
                return boolValue;
            }

            if (bool.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }

        return false;
    }

    private static List<string> GetStringList(object? obj, params string[] propertyNames)
    {
        var value = GetString(obj, propertyNames);

        if (string.IsNullOrWhiteSpace(value))
        {
            return [];
        }

        value = value.Trim();

        if (value.StartsWith("[") && value.EndsWith("]"))
        {
            try
            {
                var list = JsonSerializer.Deserialize<List<string>>(value);

                if (list != null)
                {
                    return list;
                }
            }
            catch
            {
                // If JSON parsing fails, fall back to text splitting.
            }
        }

        return value
            .Split(["\r\n", "\n", ";", "|"], StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.Trim())
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .ToList();
    }
}