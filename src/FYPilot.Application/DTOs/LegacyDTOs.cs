namespace FYPilot.Application.DTOs;

// ── Auth ──────────────────────────────────────────────────────────────────────
public record LoginRequest(string Email, string Password);
public record RegisterRequest(string Email, string Password, string FullName, string Role);
public record UserDto(int Id, string Email, string FullName, string Role);
public record AuthResponse(string Token, UserDto User);

// ── Projects ──────────────────────────────────────────────────────────────────
public record CreateProjectRequest(
    string Title,
    string Description,
    string Technologies,
    string? StartDate,
    string? EndDate,
    int? SupervisorId
);
public record UpdateProjectRequest(
    string? Title,
    string? Description,
    string? Technologies,
    string? Status,
    string? StartDate,
    string? EndDate,
    int? ProgressPercentage,
    int? SupervisorId
);
public record ProjectResponse(
    int Id, string Title, string Description, string Technologies, string Status,
    string? StartDate, string? EndDate, int ProgressPercentage,
    int StudentId, int? SupervisorId, string CreatedAt, string StudentName
);

// ── Tasks ─────────────────────────────────────────────────────────────────────
public record CreateTaskRequest(
    string Title, string? Description, string? Status,
    string? Priority, string? Deadline, int ProjectId
);
public record UpdateTaskRequest(
    string? Title, string? Description, string? Status, string? Priority, string? Deadline
);
public record TaskResponse(
    int Id, string Title, string Description, string Status,
    string Priority, string? Deadline, int ProjectId, string CreatedAt
);

// ── Milestones ────────────────────────────────────────────────────────────────
public record CreateMilestoneRequest(
    string Title, string? Description, string? DueDate, int? CompletionPercentage, int ProjectId
);
public record UpdateMilestoneRequest(
    string? Title, string? Description, string? DueDate, int? CompletionPercentage
);
public record MilestoneResponse(
    int Id, string Title, string Description, string? DueDate,
    int CompletionPercentage, int ProjectId, string CreatedAt
);

// ── Feedback ──────────────────────────────────────────────────────────────────
public record CreateFeedbackRequest(string Content, int Rating, int ProjectId);
public record FeedbackResponse(
    int Id, string Content, int Rating, int ProjectId,
    int SupervisorId, string SupervisorName, string CreatedAt
);

// ── Challenges ────────────────────────────────────────────────────────────────
public record CreateChallengeRequest(
    string Title, string Description, string? RequiredSkills, string? DifficultyLevel
);
public record UpdateChallengeRequest(
    string? Title, string? Description, string? RequiredSkills, string? DifficultyLevel
);
public record ChallengeResponse(
    int Id, string Title, string Description, string RequiredSkills, string DifficultyLevel,
    int CompanyId, string CompanyName, string Industry, string CreatedAt
);

// ── User Profiles ─────────────────────────────────────────────────────────────
public record StudentProfileResponse(
    int Id, int UserId, string University, string Major, string Year,
    string ExperienceLevel, string PreferredDomain, string PreferredStack,
    int AvailableHoursPerWeek, int TeamMembers, string TargetDifficulty,
    string ProjectGoals, string Interests, string Skills
);
public record UpdateStudentProfileRequest(
    string? University, string? Major, string? Year, string? Skills,
    string? Interests, string? ExperienceLevel, string? PreferredDomain,
    string? PreferredStack, int? AvailableHoursPerWeek, int? TeamMembers,
    string? TargetDifficulty, string? ProjectGoals
);
public record SupervisorProfileResponse(
    int Id, int UserId, string FullName, string Email,
    string Department, string Specialization, string Bio
);
public record CompanyProfileResponse(
    int Id, int UserId, string FullName, string Email,
    string CompanyName, string Industry, string Description, string Website
);
public record UpdateCompanyProfileRequest(
    string? CompanyName, string? Industry, string? Description, string? Website
);
public record SupervisorListItem(int Id, string FullName, string? Department);
