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
    IAiServiceClient aiService,
    IProjectAccessService projectAccessService,
    IActiveProjectService activeProjectService,
    ILogger<MentorChatModel> logger
) : PageModel
{
    [BindProperty(SupportsGet = true)]
    public int ProjectId { get; set; }

    public Project? CurrentProject { get; private set; }

    public ProjectAccessResult? ProjectAccess { get; private set; }

    public List<ChatMessage> Messages { get; private set; } = [];

    public List<MentorChatSession> ChatSessions { get; private set; } = [];

    public MentorChatSession? CurrentChat { get; private set; }

    public int? CurrentChatId => CurrentChat?.Id;

    public List<ProjectIdea> Ideas { get; private set; } = [];

    public ProjectIdea? SelectedIdea { get; private set; }

    public int? SelectedIdeaId { get; private set; }

    public StudentProfile? Profile { get; private set; }

    public List<StudentSkill> Skills { get; private set; } = [];

    public FypMentorServiceResponse? MentorResponse { get; private set; }

    public FypMentorAnswerDto? MentorAnswer => MentorResponse?.Answer;

    /// <summary>
    /// The AI Quality Passport for the most recent assistant reply in this
    /// chat session, loaded from the database so it survives the
    /// Post/Redirect/Get cycle (MentorResponse itself does not). Null for
    /// sessions where the latest reply was a trivial short-circuit answer
    /// (greeting, empty message) that never reached the review pipeline.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe answer"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    [TempData]
    public string? ErrorMessage { get; set; }

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

    public async Task<IActionResult> OnGetAsync(
        int? ideaId,
        int? chatId,
        CancellationToken cancellationToken)
    {
        if (!await LoadProjectContextAsync(
                cancellationToken))
        {
            TempData["Error"] =
                "Choose a project before opening "
                + "Mentor Chat.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadAsync(
            ideaId,
            chatId,
            ensureChatExists: true);

        return Page();
    }

    public async Task<IActionResult> OnPostAsync(
        string? message,
        int? ideaId,
        int? chatId,
        CancellationToken cancellationToken)
    {
        return await HandleSendAsync(
            message,
            ideaId,
            chatId,
            cancellationToken);
    }

    public async Task<IActionResult> OnPostSendAsync(
        string? message,
        int? ideaId,
        int? chatId,
        CancellationToken cancellationToken)
    {
        return await HandleSendAsync(
            message,
            ideaId,
            chatId,
            cancellationToken);
    }

    public async Task<IActionResult> OnPostNewChatAsync(
        int? ideaId,
        CancellationToken cancellationToken)
    {
        if (!await LoadProjectContextAsync(
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadAsync(
            ideaId,
            chatId: null,
            ensureChatExists: false);

        if (!SelectedIdeaId.HasValue)
        {
            ErrorMessage =
                "Select an official project idea before "
                + "starting a mentor conversation.";

            return RedirectToPage(
                new
                {
                    projectId = ProjectId
                });
        }

        var userId = UserId();

        var chat = new MentorChatSession
        {
            UserId = userId,
            IdeaId = SelectedIdeaId,
            Title = "New chat",
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };

        db.MentorChatSessions.Add(chat);
        await db.SaveChangesAsync();

        return RedirectToPage(new
        {
            projectId = ProjectId,
            ideaId = SelectedIdeaId,
            chatId = chat.Id
        });
    }

    public async Task<IActionResult> OnPostDeleteChatAsync(
        int chatId,
        int? ideaId,
        CancellationToken cancellationToken)
    {
        if (!await LoadProjectContextAsync(
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadAsync(
            ideaId,
            chatId: null,
            ensureChatExists: false);

        var userId = UserId();

        var chat = await db.MentorChatSessions
            .FirstOrDefaultAsync(
                session =>
                    session.Id == chatId &&
                    session.UserId == userId &&
                    session.IdeaId == SelectedIdeaId &&
                    session.DeletedAt == null,
                cancellationToken);

        if (chat == null)
        {
            ErrorMessage = "The selected conversation could not be found.";

            return RedirectToPage(new
            {
                projectId = ProjectId,
                ideaId = SelectedIdeaId
            });
        }

        // Soft-delete keeps the history safely in the database.
        chat.DeletedAt = DateTime.UtcNow;
        chat.UpdatedAt = DateTime.UtcNow;

        await db.SaveChangesAsync(cancellationToken);

        var nextChatId = await db.MentorChatSessions
            .AsNoTracking()
            .Where(s =>
                s.UserId == userId &&
                s.IdeaId == chat.IdeaId &&
                s.DeletedAt == null)
            .OrderByDescending(s => s.UpdatedAt)
            .Select(s => (int?)s.Id)
            .FirstOrDefaultAsync(
                cancellationToken);

        return RedirectToPage(new
        {
            projectId = ProjectId,
            ideaId = chat.IdeaId ?? ideaId,
            chatId = nextChatId
        });
    }

    private async Task<IActionResult> HandleSendAsync(
        string? message,
        int? ideaId,
        int? chatId,
        CancellationToken cancellationToken)
    {
        if (!await LoadProjectContextAsync(
                cancellationToken))
        {
            TempData["Error"] =
                "You do not have access to that project.";

            return RedirectToPage(
                "/Student/MyProjects");
        }

        await LoadAsync(
            ideaId,
            chatId,
            ensureChatExists: true);

        var finalMessage = !string.IsNullOrWhiteSpace(MessageText)
            ? MessageText.Trim()
            : (message ?? "").Trim();

        if (string.IsNullOrWhiteSpace(finalMessage))
        {
            ErrorMessage = "Please write a message before sending.";

            return RedirectToPage(new
            {
                projectId = ProjectId,
                ideaId = SelectedIdeaId,
                chatId = CurrentChatId
            });
        }

        if (CurrentChat == null)
        {
            ErrorMessage = "A chat session could not be created.";

            return RedirectToPage(new
            {
                projectId = ProjectId,
                ideaId = SelectedIdeaId
            });
        }

        var userId = UserId();

        var recentMessages = Messages
            .Where(m =>
                (m.Role == "user" || m.Role == "assistant") &&
                !string.IsNullOrWhiteSpace(m.Content))
            .OrderBy(m => m.CreatedAt)
            .ThenBy(m => m.Id)
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
            MentorChatSessionId = CurrentChat.Id,
            Role = "user",
            Content = finalMessage,
            CreatedAt = DateTime.UtcNow
        });

        if (string.Equals(
                CurrentChat.Title,
                "New chat",
                StringComparison.OrdinalIgnoreCase))
        {
            CurrentChat.Title = BuildChatTitle(finalMessage);
        }

        CurrentChat.UpdatedAt = DateTime.UtcNow;

        // Save the student's message before calling Python so it is never lost.
        await db.SaveChangesAsync();

        var request = new FypMentorRequest(
            Message: finalMessage,
            StudentProfile: BuildStudentProfileDto(),
            SelectedIdea: BuildSelectedIdeaDto(),
            DnaSummary: null,
            Roadmap: await LoadRoadmapAsync(SelectedIdeaId),
            RecentMessages: recentMessages,
            CodeContext: BuildCodeContextDto()
        );

        try
        {
            MentorResponse = await aiService.AskFypMentorAsync(request);
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Mentor chat failed for user {UserId}, idea {IdeaId}, chat {ChatId}.",
                userId,
                SelectedIdeaId,
                CurrentChat.Id);

            MentorResponse = null;
            ErrorMessage =
                "The FYP Mentor could not respond. Make sure the Python AI service is running and the internal API key matches.";
        }

        string assistantContent;

        if (MentorResponse == null)
        {
            assistantContent =
                ErrorMessage ??
                "The FYP Mentor could not respond at the moment.";
        }
        else
        {
            assistantContent =
                BuildAssistantMessageContent(MentorResponse.Answer);
        }

        db.ChatMessages.Add(new ChatMessage
        {
            UserId = userId,
            IdeaId = SelectedIdeaId,
            MentorChatSessionId = CurrentChat.Id,
            Role = "assistant",
            Content = assistantContent,
            // Structured sources -- assistant messages only, never the user
            // message above. Null (not the raw response) whenever nothing
            // valid survives BuildSourcesJson's validation.
            SourcesJson = BuildSourcesJson(MentorResponse?.Sources),
            CreatedAt = DateTime.UtcNow
        });

        CurrentChat.UpdatedAt = DateTime.UtcNow;

        // Trivial short-circuit exchanges (greetings, empty messages) report
        // an empty ReviewRunId and never reached the review pipeline, so
        // there is nothing meaningful to audit for them.
        var review = MentorResponse?.Review;

        if (review != null && !string.IsNullOrWhiteSpace(review.ReviewRunId))
        {
            db.AiOutputReviews.Add(new AiOutputReview
            {
                ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                    ? reviewRunId
                    : Guid.NewGuid(),
                UserId = userId,
                ProjectIdeaId = SelectedIdeaId,
                MentorChatSessionId = CurrentChat.Id,
                AgentName = "FypMentorAgent",
                Status = review.Status,
                Usable = review.Usable,
                WasRewritten = review.Attempts > 1,
                Attempts = review.Attempts,
                QualityScore = review.QualityScore,
                DecisionReason = review.DecisionReason,
                GeneratorProvider = MentorResponse!.Provider,
                GeneratorModel = MentorResponse!.ModelUsed,
                ReviewerProvider = review.ReviewerProvider,
                ReviewerModel = review.ReviewerModel,
                FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
                FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
                FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
                IssuesJson = JsonSerializer.Serialize(review.Issues),
                StrengthsJson = JsonSerializer.Serialize(review.Strengths),
                AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
                ReviewerVersion = review.ReviewerVersion,
                CreatedAt = DateTime.UtcNow,
                CompletedAt = DateTime.UtcNow
            });
        }

        await db.SaveChangesAsync();

        ClearMessageInput();

        // Post/Redirect/Get prevents duplicate messages after browser refresh.
        return RedirectToPage(new
        {
            projectId = ProjectId,
            ideaId = SelectedIdeaId,
            chatId = CurrentChat.Id
        });
    }

    private async Task<bool> LoadProjectContextAsync(
        CancellationToken cancellationToken)
    {
        var userId = UserId();

        if (ProjectId <= 0)
        {
            var activeProjectId =
                await activeProjectService
                    .GetActiveProjectIdAsync(
                        userId,
                        cancellationToken);

            if (!activeProjectId.HasValue)
            {
                return false;
            }

            ProjectId = activeProjectId.Value;
        }

        ProjectAccess =
            await projectAccessService.GetAccessAsync(
                ProjectId,
                userId,
                "student",
                cancellationToken);

        if (ProjectAccess == null)
        {
            return false;
        }

        CurrentProject = await db.Projects
            .AsNoTracking()
            .Include(item => item.ProjectIdea)
            .FirstOrDefaultAsync(
                item => item.Id == ProjectId,
                cancellationToken);

        if (CurrentProject == null)
        {
            return false;
        }

        await activeProjectService.RememberPageAsync(
            userId,
            ProjectId,
            "/Student/MentorChat",
            cancellationToken);

        return true;
    }

    private async Task LoadAsync(
        int? ideaId,
        int? chatId,
        bool ensureChatExists)
    {
        var userId = UserId();

        Profile = await db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.UserId == userId);

        Skills = await db.StudentSkills
            .AsNoTracking()
            .Where(s => s.UserId == userId)
            .ToListAsync();

        /*
         * Mentor Chat follows only the official idea selected
         * for the active project. It must never fall back to
         * an unrelated idea from the student's account.
         */
        SelectedIdea =
            CurrentProject?.ProjectIdea;

        SelectedIdeaId =
            SelectedIdea?.Id;

        Ideas =
            SelectedIdea == null
                ? []
                : [SelectedIdea];

        if (!SelectedIdeaId.HasValue)
        {
            ChatSessions = [];
            CurrentChat = null;
            Messages = [];
            LatestReview = null;

            return;
        }

        ChatSessions = await db.MentorChatSessions
            .AsNoTracking()
            .Where(s =>
                s.UserId == userId &&
                s.IdeaId == SelectedIdeaId &&
                s.DeletedAt == null)
            .OrderByDescending(s => s.UpdatedAt)
            .ThenByDescending(s => s.Id)
            .Take(30)
            .ToListAsync();

        CurrentChat = chatId.HasValue
            ? ChatSessions.FirstOrDefault(s => s.Id == chatId.Value)
            : ChatSessions.FirstOrDefault();

        if (CurrentChat == null && ensureChatExists)
        {
            var hadNoSessions = ChatSessions.Count == 0;

            var newChat = new MentorChatSession
            {
                UserId = userId,
                IdeaId = SelectedIdeaId,
                Title = "New chat",
                CreatedAt = DateTime.UtcNow,
                UpdatedAt = DateTime.UtcNow
            };

            db.MentorChatSessions.Add(newChat);
            await db.SaveChangesAsync();

            // Preserve messages created before chat sessions were introduced.
            if (hadNoSessions)
            {
                var legacyMessages = await db.ChatMessages
                    .Where(m =>
                        m.UserId == userId &&
                        m.IdeaId == SelectedIdeaId &&
                        m.MentorChatSessionId == null)
                    .OrderBy(m => m.CreatedAt)
                    .ThenBy(m => m.Id)
                    .ToListAsync();

                if (legacyMessages.Count > 0)
                {
                    foreach (var legacyMessage in legacyMessages)
                    {
                        legacyMessage.MentorChatSessionId = newChat.Id;
                    }

                    var firstUserMessage = legacyMessages
                        .FirstOrDefault(m =>
                            m.Role == "user" &&
                            !string.IsNullOrWhiteSpace(m.Content));

                    if (firstUserMessage != null)
                    {
                        newChat.Title =
                            BuildChatTitle(firstUserMessage.Content);
                    }

                    newChat.UpdatedAt =
                        legacyMessages.Max(m => m.CreatedAt);

                    await db.SaveChangesAsync();
                }
            }

            CurrentChat = newChat;

            ChatSessions = await db.MentorChatSessions
                .AsNoTracking()
                .Where(s =>
                    s.UserId == userId &&
                    s.IdeaId == SelectedIdeaId &&
                    s.DeletedAt == null)
                .OrderByDescending(s => s.UpdatedAt)
                .ThenByDescending(s => s.Id)
                .Take(30)
                .ToListAsync();
        }

        if (CurrentChat == null)
        {
            Messages = [];
            return;
        }

        var newestMessages = await db.ChatMessages
            .AsNoTracking()
            .Where(m =>
                m.UserId == userId &&
                m.MentorChatSessionId == CurrentChat.Id)
            .OrderByDescending(m => m.CreatedAt)
            .ThenByDescending(m => m.Id)
            .Take(50)
            .ToListAsync();

        Messages = newestMessages
            .OrderBy(m => m.CreatedAt)
            .ThenBy(m => m.Id)
            .ToList();

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.MentorChatSessionId == CurrentChat.Id)
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync();
    }

    private MentorStudentProfileDto BuildStudentProfileDto()
    {
        var studentSkills = Skills
            .Select(s => GetString(s, "SkillName", "Name", "Title"))
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct()
            .ToList();

        var skillRatings = new Dictionary<string, int>();

        foreach (var skill in Skills)
        {
            var skillName = GetString(
                skill,
                "SkillName",
                "Name",
                "Title");

            if (string.IsNullOrWhiteSpace(skillName))
            {
                continue;
            }

            var rating = GetInt(
                skill,
                3,
                "Rating",
                "Level",
                "SkillLevel",
                "Score");

            skillRatings[skillName] = rating;
        }

        var major = GetString(Profile, "Major");
        var experienceLevel =
            GetString(Profile, "ExperienceLevel");

        if (string.IsNullOrWhiteSpace(major))
        {
            major = "Computer Science";
        }

        if (string.IsNullOrWhiteSpace(experienceLevel))
        {
            experienceLevel = "intermediate";
        }

        return new MentorStudentProfileDto(
            Major: major,
            ExperienceLevel: experienceLevel,
            TeamSize: GetInt(
                Profile,
                1,
                "TeamMembers",
                "TeamSize"),
            AvailableHoursPerWeek: GetInt(
                Profile,
                10,
                "AvailableHoursPerWeek"),
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
            Title: GetString(
                SelectedIdea,
                "Title",
                "IdeaTitle",
                "Name"),
            ProblemStatement: GetString(
                SelectedIdea,
                "ProblemStatement",
                "Problem",
                "Description"),
            TargetUsers: GetString(
                SelectedIdea,
                "TargetUsers",
                "Users"),
            WhyUseful: GetString(
                SelectedIdea,
                "WhyUseful",
                "Usefulness",
                "Value"),
            RequiredTechnologies: GetString(
                SelectedIdea,
                "RequiredTechnologies",
                "Technologies",
                "TechStack"),
            RequiredSkills: GetString(
                SelectedIdea,
                "RequiredSkills"),
            MissingSkills: GetString(
                SelectedIdea,
                "MissingSkills"),
            DifficultyLevel: GetString(
                SelectedIdea,
                "DifficultyLevel",
                "Difficulty"),
            ExpectedDurationWeeks: GetInt(
                SelectedIdea,
                10,
                "ExpectedDurationWeeks",
                "DurationWeeks"),
            Domain: GetString(
                SelectedIdea,
                "Domain",
                "ProjectDomain"),
            FinalDeliverables: GetString(
                SelectedIdea,
                "FinalDeliverables",
                "Deliverables")
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
            .Split(
                ["\r\n", "\n"],
                StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.Trim())
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .ToList();

        return new MentorCodeContextDto(
            TargetFile: TargetFile.Trim(),
            Language:
                string.IsNullOrWhiteSpace(CodeLanguage)
                    ? "csharp"
                    : CodeLanguage.Trim(),
            ExistingCode: ExistingCode,
            RequestedChange: RequestedChange.Trim(),
            Constraints: constraints
        );
    }

    private async Task<List<MentorRoadmapPhaseDto>>
        LoadRoadmapAsync(int? ideaId)
    {
        if (!ideaId.HasValue)
        {
            return [];
        }

        var userId = UserId();

        try
        {
            var roadmap = await db.ProjectRoadmaps
                .AsNoTracking()
                .Include(r => r.Phases)
                .Where(r =>
                    r.IdeaId == ideaId.Value &&
                    r.UserId == userId)
                .OrderByDescending(r => r.CreatedAt)
                .FirstOrDefaultAsync();

            if (roadmap == null)
            {
                return [];
            }

            return roadmap.Phases
                .OrderBy(p => p.PhaseNumber)
                .Select(p => new MentorRoadmapPhaseDto(
                    PhaseNumber: p.PhaseNumber,
                    Name: p.Name,
                    Objective: p.Objective,
                    Tasks: GetStringList(
                        p,
                        nameof(RoadmapPhase.TasksJson)),
                    ExpectedOutput: p.ExpectedOutput,
                    SuccessCriteria: p.SuccessCriteria,
                    IsCompleted: p.IsCompleted
                ))
                .ToList();
        }
        catch (Exception ex)
        {
            logger.LogError(
                ex,
                "Failed to load roadmap context for student {UserId} and idea {IdeaId}.",
                userId,
                ideaId.Value);

            return [];
        }
    }

    private static string BuildAssistantMessageContent(
        FypMentorAnswerDto answer)
    {
        var content = answer.Reply;

        // Sources are now rendered separately as a structured block (see
        // ParseSources/SourcesJson below) -- the prompt no longer asks the
        // model to write "Read: <title> - <url>" actions, but this filter
        // stays as defense-in-depth so a stray citation-shaped action never
        // duplicates what the structured Sources disclosure already shows.
        var plainActions = answer.SuggestedNextActions
            .Where(a => !LooksLikeSourceCitation(a))
            .ToList();

        if (plainActions.Count > 0)
        {
            content += "\n\nSuggested next actions:\n";
            content += string.Join(
                "\n",
                plainActions.Select(a => $"- {a}"));
        }

        if (!string.IsNullOrWhiteSpace(answer.Warning))
        {
            content +=
                $"\n\nWarning: {answer.Warning}";
        }

        if (answer.CodeBlocks.Any())
        {
            content += "\n\nGenerated code:\n";

            foreach (var block in answer.CodeBlocks)
            {
                content +=
                    $"\nTarget file: {block.TargetFile}\n";

                content += $"{block.Content}\n";
            }
        }

        return content;
    }

    private static bool LooksLikeSourceCitation(string action) =>
        action.StartsWith("Read:", StringComparison.OrdinalIgnoreCase) ||
        action.Contains("http://", StringComparison.OrdinalIgnoreCase) ||
        action.Contains("https://", StringComparison.OrdinalIgnoreCase);

    private static bool IsSafeHttpUrl(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

    private static string SafeText(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var clean = value.Trim();
        return clean.Length <= maximumLength ? clean : clean[..maximumLength];
    }

    private const int MaximumStoredSources = 6;

    /// <summary>
    /// Validates, deduplicates, and caps sources before persisting them --
    /// defense-in-depth even though the Python side already applies the
    /// same rules (see app/routers/fyp_chat.py's _build_response_sources).
    /// Returns null (not "[]") when nothing valid survives, so ChatMessage.
    /// SourcesJson stays null for ordinary messages.
    /// </summary>
    private static string? BuildSourcesJson(List<IdeaEvidenceSourceDto>? sources)
    {
        var safeSources = (sources ?? [])
            .Where(s => IsSafeHttpUrl(s.Url) && !string.IsNullOrWhiteSpace(s.Title))
            .GroupBy(s => s.Url, StringComparer.OrdinalIgnoreCase)
            .Select(g => g.First())
            .Take(MaximumStoredSources)
            .Select(s => new { title = SafeText(s.Title, 200), url = s.Url })
            .ToList();

        return safeSources.Count > 0
            ? JsonSerializer.Serialize(safeSources)
            : null;
    }

    /// <summary>
    /// Deserializes a stored ChatMessage.SourcesJson value back into
    /// IdeaEvidenceSourceDto entries for Razor rendering. Never throws --
    /// malformed/corrupt JSON safely yields an empty list instead of
    /// breaking page rendering. Re-validates and re-deduplicates at read
    /// time as well (defense-in-depth, matches BuildSourcesJson above).
    /// </summary>
    public static List<IdeaEvidenceSourceDto> ParseSources(string? sourcesJson)
    {
        if (string.IsNullOrWhiteSpace(sourcesJson))
        {
            return [];
        }

        try
        {
            using var document = JsonDocument.Parse(sourcesJson);
            var results = new List<IdeaEvidenceSourceDto>();

            foreach (var item in document.RootElement.EnumerateArray())
            {
                var title = item.TryGetProperty("title", out var titleProp)
                    ? titleProp.GetString() ?? ""
                    : "";
                var url = item.TryGetProperty("url", out var urlProp)
                    ? urlProp.GetString() ?? ""
                    : "";

                if (!string.IsNullOrWhiteSpace(title) && IsSafeHttpUrl(url))
                {
                    results.Add(new IdeaEvidenceSourceDto(title, url));
                }
            }

            return results
                .GroupBy(s => s.Url, StringComparer.OrdinalIgnoreCase)
                .Select(g => g.First())
                .Take(MaximumStoredSources)
                .ToList();
        }
        catch (JsonException)
        {
            return [];
        }
    }

    private static string BuildChatTitle(string message)
    {
        var clean = string.Join(
            " ",
            message
                .Split(
                    [' ', '\r', '\n', '\t'],
                    StringSplitOptions.RemoveEmptyEntries));

        if (string.IsNullOrWhiteSpace(clean))
        {
            return "New chat";
        }

        return clean.Length <= 45
            ? clean
            : clean[..45].TrimEnd() + "...";
    }

    private void ClearMessageInput()
    {
        MessageText = "";
        ModelState.Remove(nameof(MessageText));
    }

    private int UserId()
        => int.Parse(
            User.FindFirst(ClaimTypes.NameIdentifier)!.Value);

    private static string GetString(
        object? obj,
        params string[] propertyNames)
    {
        if (obj == null)
        {
            return "";
        }

        foreach (var propertyName in propertyNames)
        {
            var property =
                obj.GetType().GetProperty(propertyName);

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

    private static int GetInt(
        object? obj,
        int defaultValue,
        params string[] propertyNames)
    {
        if (obj == null)
        {
            return defaultValue;
        }

        foreach (var propertyName in propertyNames)
        {
            var property =
                obj.GetType().GetProperty(propertyName);

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

            if (int.TryParse(
                    value.ToString(),
                    out var parsed))
            {
                return parsed;
            }
        }

        return defaultValue;
    }

    private static List<string> GetStringList(
        object? obj,
        params string[] propertyNames)
    {
        var value = GetString(obj, propertyNames);

        if (string.IsNullOrWhiteSpace(value))
        {
            return [];
        }

        value = value.Trim();

        if (value.StartsWith("[") &&
            value.EndsWith("]"))
        {
            try
            {
                var list =
                    JsonSerializer.Deserialize<List<string>>(value);

                if (list != null)
                {
                    return list;
                }
            }
            catch
            {
                // Fall back to normal text splitting.
            }
        }

        return value
            .Split(
                ["\r\n", "\n", ";", "|"],
                StringSplitOptions.RemoveEmptyEntries)
            .Select(x => x.Trim())
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .ToList();
    }
}