namespace FYPilot.Application.DTOs;

// ── Feasibility Prediction ────────────────────────────────────────────────────
public record FeasibilityPredictionRequest(
    int SkillMatchScore,
    int MissingSkillsCount,
    int TimelineWeeks,
    int ComplexityScore,
    int TeamSize,
    bool AiRequired,
    bool DatasetRequired,
    bool DeploymentRequired,
    int AcademicValue,
    int MarketValue
);

public record FeasibilityPredictionResponse(
    int FeasibilityScore,
    string RiskLevel,
    string Explanation,
    List<string> TopRiskFactors,
    List<string> Suggestions
);

// ── Similarity / Originality ──────────────────────────────────────────────────
public record SimilarityCheckRequest(
    string Title,
    string Description
);

public record SimilarityCheckResponse(
    int SimilarityScore,
    int OriginalityScore,
    List<SimilarProject> SimilarProjects,
    List<string> ImprovementSuggestions
);

// ── Market Matching ───────────────────────────────────────────────────────────
public record MarketMatchRequest(
    string IdeaTitle,
    string IdeaDescription,
    string Domain
);

public record MarketMatchResponse(
    int MarketRelevanceScore,
    string BestMatchSector,
    string BestMatchProblem,
    List<string> RelevantKeywords,
    string MarketInsight
);

// ── Risk Alarms ───────────────────────────────────────────────────────────────
public record RiskAlarmRequest(
    int SkillMatchScore,
    int MissingSkillsCount,
    int TimelineWeeks,
    int ComplexityScore,
    bool DatasetRequired,
    bool AiRequired
);

public record RiskAlarmResponse(
    List<RiskAlarmItem> Alarms,
    string OverallRisk
);

public record RiskAlarmItem(
    string Category,
    string Severity,
    string Reason,
    string SuggestedFix
);
