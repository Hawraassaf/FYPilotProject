namespace FYPilot.Application.DTOs;

// ── Skills ────────────────────────────────────────────────────────────────────
public record SkillRatingRequest(string SkillName, int Rating, int ProficiencyLevel = 0)
{
    public int EffectiveRating => ProficiencyLevel > 0 ? ProficiencyLevel : Rating;
}

public record SkillAssessmentRequest(List<SkillRatingRequest> Skills);

public record SkillAssessmentResult(
    List<SkillRatingRequest> Skills,
    int TotalScore,
    string StrongestDomain,
    string WeakestDomain,
    List<string> MissingSkills,
    string RecommendedComplexity
);

// ── AI Service Contracts ──────────────────────────────────────────────────────
public record SkillAnalysisRequest(List<string> Skills, string Level = "intermediate");

public record SkillAnalysisResponse(
    int SkillScore,
    string RecommendedLevel,
    string Message
);

public record AiHealthResponse(
    string Status,
    string? Version = null,
    string? Message = null
);

public record RiskPredictionRequest(
    int ProjectId,
    List<string> Risks
);

public record RiskPredictionResponse(
    int RiskScore,
    string RiskLevel,
    string Recommendation
);

// ── Profile ───────────────────────────────────────────────────────────────────
public record StudentProfileRequest(
    string? University,
    string? Major,
    string? Year,
    string? ExperienceLevel,
    string? PreferredDomain,
    string? PreferredStack,
    int? AvailableHoursPerWeek,
    int? TeamMembers,
    string? TargetDifficulty,
    string? ProjectGoals,
    string? Interests
);

// ── Idea Generation ───────────────────────────────────────────────────────────
public record GenerateIdeasRequest(
    string Major,
    string ExperienceLevel,
    string PreferredDomain,
    string TargetDifficulty,
    string PreferredStack,
    int AvailableHoursPerWeek,
    int TeamMembers,
    string ProjectGoals,
    bool Regenerate,
    List<string> PreviousIdeaTitles,
    List<GenerateIdeaSkillDto> Skills
);

public record GenerateIdeaSkillDto(
    string SkillName,
    int Rating,
    int ProficiencyLevel
);

public record GenerateIdeasResponse(
    List<GeneratedIdeaDto> Ideas,
    string Agent,
    bool LlmUsed,
    string Source,
    string? OllamaError,
    string? OllamaRawPreview,
    string? AgentFile,
    DateTime? GeneratedAt,
    string Message
);

public record GeneratedIdeaDto(
    string Title,
    string ProblemStatement,
    string TargetUsers,
    string WhyUseful,
    string LebaneseMarketRelevance,
    string RequiredTechnologies,
    string RequiredSkills,
    string MissingSkills,
    int DifficultyLevel,
    double InnovationScore,
    double FeasibilityScore,
    double MarketDemandScore,
    int ExpectedDurationWeeks,
    string SupervisorCategory,
    string DatasetNeeded,
    string FinalDeliverables,
    string Domain,
    string LebaneseSector
);

public record ProjectIdeaResponse(
    int Id,
    string Title,
    string ProblemStatement,
    string TargetUsers,
    string WhyUseful,
    string LebaneseMarketRelevance,
    string RequiredTechnologies,
    string RequiredSkills,
    string MissingSkills,
    string DifficultyLevel,
    int InnovationScore,
    int FeasibilityScore,
    int MarketDemandScore,
    int ExpectedDurationWeeks,
    string SupervisorCategory,
    string DatasetNeeded,
    string FinalDeliverables,
    string Domain,
    string LebanesesSector,
    bool IsSelected,
    DateTime CreatedAt
);

// ── Feasibility ───────────────────────────────────────────────────────────────
public record FeasibilityReportResponse(
    int Id,
    int IdeaId,
    int SkillMatchScore,
    int DifficultyMatchScore,
    int TimelineFitScore,
    int MarketUsefulnessScore,
    int InnovationScore,
    int RiskScore,
    int FinalFeasibilityScore,
    string Explanation,
    List<RiskItem> Risks
);

public record RiskItem(
    string Level,
    string Category,
    string Description,
    string Mitigation
);

// ── Roadmap ───────────────────────────────────────────────────────────────────
public record RoadmapPhaseResponse(
    int Id,
    int PhaseNumber,
    string Name,
    string Objective,
    List<string> Tasks,
    string ExpectedOutput,
    string ToolsNeeded,
    int EstimatedWeeks,
    string Dependencies,
    string Risks,
    string SuccessCriteria,
    bool IsCompleted
);

public record RoadmapResponse(
    int Id,
    int IdeaId,
    List<RoadmapPhaseResponse> Phases,
    DateTime CreatedAt
);

// ── Chat ──────────────────────────────────────────────────────────────────────
public record ChatRequest(
    string Message,
    int? IdeaId
);

public record ChatResponse(
    string Message,
    string Role,
    DateTime CreatedAt
);

public record ChatHistoryResponse(
    List<ChatMessageDto> Messages
);

public record ChatMessageDto(
    int Id,
    string Role,
    string Content,
    DateTime CreatedAt
);

// ── Similarity ────────────────────────────────────────────────────────────────
public record SimilarityResult(
    string Title,
    int SimilarityPercentage,
    int OriginalityScore,
    List<SimilarProject> SimilarProjects,
    List<string> Improvements
);

public record SimilarProject(
    string Title,
    string Domain,
    int Year,
    int SimilarityPct
);

// ── Market Need ───────────────────────────────────────────────────────────────
public record MarketNeedResponse(
    int Id,
    string Sector,
    string Problem,
    string PossibleSolution,
    string BusinessValue,
    int DemandScore
);

// ── Supervisor Evaluation ─────────────────────────────────────────────────────
public record SupervisorEvalRequest(
    string Status,
    string Comment,
    string ImprovementSuggestions
);

public record SupervisorEvalResponse(
    int Id,
    int IdeaId,
    string IdeaTitle,
    string StudentName,
    string Status,
    string Comment,
    string ImprovementSuggestions,
    int SimilarityScore,
    int OriginalityScore,
    DateTime CreatedAt
);

// ── Documentation ─────────────────────────────────────────────────────────────
public record DocumentationResponse(
    string ProjectTitle,
    string Abstract,
    string ProblemStatement,
    string Objectives,
    string Scope,
    string Methodology,
    string ExpectedOutcomes,
    string TechnologiesUsed,
    string Timeline,
    string ReferencesPlaceholder
);

// ── Presentation ──────────────────────────────────────────────────────────────
public record PresentationResponse(
    string ProjectTitle,
    List<SlideOutline> Slides,
    List<string> DemoFlow,
    List<QnA> PossibleQuestions
);

public record SlideOutline(
    int SlideNumber,
    string Title,
    List<string> SpeakingPoints
);

public record QnA(
    string Question,
    string SuggestedAnswer
);

// ── Implementation Plans ──────────────────────────────────────────────────────
public record ImplementationPlanResponse(
    string ProjectTitle,
    string Architecture,
    string FolderStructure,
    List<string> Controllers,
    List<string> Services,
    List<string> Models,
    List<string> ApiEndpoints,
    string AuthApproach,
    string ValidationApproach,
    string ErrorHandling,
    string TestingApproach,
    string DeploymentSteps,
    string DbContext
);

public record DataSciencePlanResponse(
    string ProjectTitle,
    List<string> DataSources,
    string DatasetStructure,
    List<string> PreprocessingSteps,
    List<string> FeatureEngineering,
    string ModelSelection,
    List<string> EvaluationMetrics,
    string TrainingPipeline,
    string PredictionPipeline,
    string Integration,
    string FolderStructure
);

// ── Admin ─────────────────────────────────────────────────────────────────────
public record AdminStatsResponse(
    int TotalUsers,
    int TotalStudents,
    int TotalSupervisors,
    int TotalAdmins,
    int TotalIdeas,
    int TotalSelectedProjects,
    int TotalRoadmaps
);
public record ProjectDnaRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("ideaTitle")]
    string IdeaTitle,

    [property: System.Text.Json.Serialization.JsonPropertyName("problemStatement")]
    string ProblemStatement,

    [property: System.Text.Json.Serialization.JsonPropertyName("targetUsers")]
    string TargetUsers,

    [property: System.Text.Json.Serialization.JsonPropertyName("whyUseful")]
    string WhyUseful,

    [property: System.Text.Json.Serialization.JsonPropertyName("lebaneseMarketRelevance")]
    string LebaneseMarketRelevance,

    [property: System.Text.Json.Serialization.JsonPropertyName("requiredTechnologies")]
    string RequiredTechnologies,

    [property: System.Text.Json.Serialization.JsonPropertyName("requiredSkills")]
    string RequiredSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("missingSkills")]
    string MissingSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("difficultyLevel")]
    string DifficultyLevel,

    [property: System.Text.Json.Serialization.JsonPropertyName("datasetNeeded")]
    string DatasetNeeded,

    [property: System.Text.Json.Serialization.JsonPropertyName("finalDeliverables")]
    string FinalDeliverables,

    [property: System.Text.Json.Serialization.JsonPropertyName("domain")]
    string Domain,

    [property: System.Text.Json.Serialization.JsonPropertyName("lebaneseSector")]
    string LebaneseSector,

    [property: System.Text.Json.Serialization.JsonPropertyName("studentMajor")]
    string StudentMajor,

    [property: System.Text.Json.Serialization.JsonPropertyName("experienceLevel")]
    string ExperienceLevel,

    [property: System.Text.Json.Serialization.JsonPropertyName("availableHoursPerWeek")]
    int AvailableHoursPerWeek,

    [property: System.Text.Json.Serialization.JsonPropertyName("teamSize")]
    int TeamSize,

    [property: System.Text.Json.Serialization.JsonPropertyName("studentSkills")]
    List<string> StudentSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("skillRatings")]
    Dictionary<string, int> SkillRatings
);
// ── AI Project Roadmap Agent ─────────────────────────────────────────────────

public record ProjectRoadmapRequest(
    [property: System.Text.Json.Serialization.JsonPropertyName("ideaTitle")]
    string IdeaTitle,

    [property: System.Text.Json.Serialization.JsonPropertyName("problemStatement")]
    string ProblemStatement,

    [property: System.Text.Json.Serialization.JsonPropertyName("requiredTechnologies")]
    string RequiredTechnologies,

    [property: System.Text.Json.Serialization.JsonPropertyName("requiredSkills")]
    string RequiredSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("missingSkills")]
    string MissingSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("difficultyLevel")]
    string DifficultyLevel,

    [property: System.Text.Json.Serialization.JsonPropertyName("expectedDurationWeeks")]
    int ExpectedDurationWeeks,

    [property: System.Text.Json.Serialization.JsonPropertyName("domain")]
    string Domain,

    [property: System.Text.Json.Serialization.JsonPropertyName("finalDeliverables")]
    string FinalDeliverables,

    [property: System.Text.Json.Serialization.JsonPropertyName("teamSize")]
    int TeamSize,

    [property: System.Text.Json.Serialization.JsonPropertyName("availableHoursPerWeek")]
    int AvailableHoursPerWeek,

    [property: System.Text.Json.Serialization.JsonPropertyName("studentSkills")]
    List<string> StudentSkills,

    [property: System.Text.Json.Serialization.JsonPropertyName("skillRatings")]
    Dictionary<string, int> SkillRatings
);

public record ProjectRoadmapServiceResponse(
    ProjectRoadmapDto Roadmap,
    string Agent,
    bool LlmUsed,
    string Source,
    string? OllamaError,
    string? OllamaRawPreview,
    DateTime? GeneratedAt,
    string Message
);

public record ProjectRoadmapDto(
    string RoadmapTitle,
    int TotalWeeks,
    string DifficultyLevel,
    string TeamStrategy,
    List<ProjectRoadmapWeekDto> Weeks,
    string FinalAdvice
);

public record ProjectRoadmapWeekDto(
    int WeekNumber,
    string PhaseTitle,
    string MainGoal,
    List<string> Tasks,
    List<string> Deliverables,
    List<string> TeamResponsibilities,
    List<string> SkillsToLearn,
    string RiskWarning,
    string Checkpoint
);
public record ProjectDnaServiceResponse(
    ProjectDnaAnalysisDto Analysis,
    string Agent,
    bool LlmUsed,
    string Source,
    string? OllamaError,
    string? OllamaRawPreview,
    DateTime? GeneratedAt,
    string Message
);

public record ProjectDnaAnalysisDto(
    string ProjectDNAType,
    int OverallScore,
    int TechnicalFitScore,
    int SkillMatchScore,
    int InnovationScore,
    int FeasibilityScore,
    int MarketRelevanceScore,
    int DataReadinessScore,
    int ScopeClarityScore,
    int SupervisorFitScore,
    string RiskLevel,
    List<string> Strengths,
    List<string> Weaknesses,
    List<ProjectDnaRiskDto> RiskProfile,
    List<ProjectDnaSkillDto> RequiredSkillsAnalysis,
    List<string> RecommendedImprovements,
    string Summary
);

public record ProjectDnaRiskDto(
    string Title,
    string Level,
    string Explanation,
    string Mitigation
);

public record ProjectDnaSkillDto(
    string SkillName,
    string Status,
    string Explanation
);
public record IdeaComparisonRequest(
    string StudentMajor,
    string ExperienceLevel,
    int TeamSize,
    int AvailableHoursPerWeek,
    List<string> StudentSkills,
    Dictionary<string, int> SkillRatings,
    List<IdeaComparisonInputDto> Ideas
);

public record IdeaComparisonInputDto(
    int Id,
    string Title,
    string ProblemStatement,
    string RequiredTechnologies,
    string RequiredSkills,
    string MissingSkills,
    string DifficultyLevel,
    int ExpectedDurationWeeks,
    string DatasetNeeded,
    string Domain,
    string LebaneseMarketRelevance,
    double InnovationScore,
    double FeasibilityScore,
    double MarketDemandScore,
    string CreatedAt
);

public record IdeaComparisonServiceResponse(
    IdeaComparisonDto Comparison,
    string Agent,
    bool LlmUsed,
    string Source,
    string? OllamaError,
    string? OllamaRawPreview,
    DateTime? GeneratedAt,
    string Message
);

public record IdeaComparisonDto(
    string ComparisonTitle,
    int TotalIdeasCompared,
    int BestIdeaId,
    string BestIdeaTitle,
    string Summary,
    List<ComparedIdeaDto> Ideas,
    string FinalRecommendation
);

public record ComparedIdeaDto(
    int IdeaId,
    int Rank,
    string Title,
    int OverallScore,
    int SkillFitScore,
    int FeasibilityScore,
    int InnovationScore,
    int MarketRelevanceScore,
    string RiskLevel,
    string BestFor,
    List<string> Strengths,
    List<string> Weaknesses,
    string Recommendation
);
public record FypMentorRequest(
    string Message,
    MentorStudentProfileDto? StudentProfile,
    MentorSelectedIdeaDto? SelectedIdea,
    MentorDnaSummaryDto? DnaSummary,
    List<MentorRoadmapPhaseDto> Roadmap,
    List<MentorRecentMessageDto> RecentMessages,
    MentorCodeContextDto? CodeContext
);

public record MentorStudentProfileDto(
    string Major,
    string ExperienceLevel,
    int TeamSize,
    int AvailableHoursPerWeek,
    List<string> Skills,
    Dictionary<string, int> SkillRatings
);

public record MentorSelectedIdeaDto(
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

public record MentorDnaSummaryDto(
    int? OverallScore,
    string RiskLevel,
    List<string> Strengths,
    List<string> Weaknesses,
    List<string> RecommendedImprovements
);

public record MentorRoadmapPhaseDto(
    int PhaseNumber,
    string Name,
    string Objective,
    List<string> Tasks,
    string ExpectedOutput,
    string SuccessCriteria,
    bool IsCompleted
);

public record MentorRecentMessageDto(
    string Role,
    string Content
);

public record MentorCodeContextDto(
    string TargetFile,
    string Language,
    string ExistingCode,
    string RequestedChange,
    List<string> Constraints
);

public record FypMentorServiceResponse(
    FypMentorAnswerDto Answer,
    string Agent,
    bool LlmUsed,
    string Source,
    string? OllamaError,
    string? OllamaRawPreview,
    DateTime? GeneratedAt,
    string Message,
    AiQualityPassportDto? Review = null,
    string? Provider = null,
    string? ModelUsed = null
);

public record FypMentorAnswerDto(
    string Reply,
    string Intent,
    List<string> UsedContext,
    List<string> SuggestedNextActions,
    string Warning,
    int Confidence,
    List<string> Assumptions,
    List<MentorCodeBlockDto> CodeBlocks
);

public record MentorCodeBlockDto(
    string Title,
    string Language,
    string TargetFile,
    string Content,
    List<string> Notes
);