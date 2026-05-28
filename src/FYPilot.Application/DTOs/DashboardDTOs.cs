namespace FYPilot.Application.DTOs;

public record StudentDashboardResponse(
    int TotalProjects,
    int ActiveProjects,
    int CompletedProjects,
    int PendingTasks,
    int CompletedTasks,
    int OverallProgress,
    IEnumerable<MilestoneResponse> UpcomingMilestones,
    IEnumerable<string> RiskWarnings,
    IEnumerable<string> AiSuggestions
);

public record SupervisorDashboardResponse(
    int TotalStudents,
    int ActiveProjects,
    int PendingReviews,
    IEnumerable<ProjectResponse> RecentProjects,
    IEnumerable<ProgressBucket> ProgressBreakdown
);

public record ProgressBucket(string Range, int Count);

public record CompanyDashboardResponse(
    int TotalChallenges,
    int ActiveChallenges,
    IEnumerable<ChallengeResponse> RecentChallenges,
    IEnumerable<DifficultyCounts> ChallengesByDifficulty
);

public record DifficultyCounts(string Difficulty, int Count);

public record ActivityResponse(
    int Id,
    string Type,
    string Message,
    string Timestamp,
    int UserId,
    string UserName
);
