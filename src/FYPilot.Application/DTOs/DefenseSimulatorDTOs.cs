namespace FYPilot.Application.DTOs;

public record DefenseGenerateQuestionsRequest(
    string ProjectTitle,
    string ProblemStatement,
    string TargetUsers,
    string Technologies,
    string Domain,
    string DifficultyLevel,
    List<string> Skills,
    List<string> RoadmapPhases,
    string DocumentationSummary,
    int NumberOfQuestions = 6
);

public record DefenseQuestionDto(
    string Question,
    string Category,
    string Difficulty,
    List<string> ExpectedPoints,
    string FollowUpQuestion
);

public record DefenseGenerateQuestionsResponse(
    List<DefenseQuestionDto> Questions,
    bool LlmUsed,
    string Source,
    string? Error
);