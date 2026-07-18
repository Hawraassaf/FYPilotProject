namespace FYPilot.Application.DTOs;

public sealed record DefenseStudentProfileRequest(
    string Major,
    string ExperienceLevel,
    int TeamSize,
    int AvailableHoursPerWeek,
    List<string> Skills,
    Dictionary<string, int> SkillRatings
);

public sealed record DefenseSelectedIdeaRequest(
    int? Id,
    string Title,
    string ProblemStatement,
    string TargetUsers,
    string WhyUseful,
    string RequiredTechnologies,
    string RequiredSkills,
    string MissingSkills,
    string DifficultyLevel,
    int ExpectedDurationWeeks,
    string Domain,
    string FinalDeliverables
);

public sealed record DefenseRoadmapPhaseRequest(
    int PhaseNumber,
    string Name,
    string Objective,
    List<string> Tasks,
    string ExpectedOutput,
    string SuccessCriteria,
    bool IsCompleted
);

public sealed record DefenseGenerateQuestionsRequest(
    DefenseStudentProfileRequest StudentProfile,
    DefenseSelectedIdeaRequest SelectedIdea,
    List<DefenseRoadmapPhaseRequest> Roadmap,
    Dictionary<string, object>? SeDocumentation,
    List<string> FocusAreas,
    List<string> PreviousQuestions,
    string Mode,
    int NumberOfQuestions,
    string Model
);

public sealed record DefenseQuestionDto(
    string Question,
    string Category,
    string Difficulty,
    List<string> ExpectedPoints,
    string FollowUpQuestion
);

public sealed record DefenseGenerateQuestionsResponse(
    List<DefenseQuestionDto> Questions,
    bool LlmUsed,
    string Source,
    string? OllamaError = null,
    string ModelUsed = "",
    List<string>? ConsistencyWarnings = null,
    string Message = "",
    string? Provider = null,
    string? ProviderError = null,
    string? Error = null
);

public sealed record DefenseEvaluateAnswerRequest(
    DefenseQuestionDto Question,
    string StudentAnswer,
    DefenseStudentProfileRequest? StudentProfile,
    DefenseSelectedIdeaRequest? SelectedIdea,
    string Mode,
    string Model
);

public sealed record DefenseEvaluateAnswerResponse(
    int Score,
    string Level,
    List<string> Strengths,
    List<string> MissingPoints,
    string ImprovedAnswer,
    string FollowUpQuestion,
    string FeedbackSummary,
    bool LlmUsed,
    string Source,
    int? SimilarityScore = null,
    int? ConfidenceScore = null,
    string? OllamaError = null,
    string ModelUsed = "",
    string? Provider = null,
    string? ProviderError = null
);