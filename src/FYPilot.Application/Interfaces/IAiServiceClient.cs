using FYPilot.Application.DTOs;

namespace FYPilot.Application.Interfaces;

/// <summary>
/// Contract for calling the Python AI / Data Science service.
/// The .NET app always goes through this interface — never calls Python directly from pages or controllers.
/// </summary>
public interface IAiServiceClient
{
    /// <summary>GET /health — Check whether the Python service is up.</summary>
    Task<AiHealthResponse?> GetHealthAsync();

    /// <summary>POST /analyze-skills — Score a student's skill set.</summary>
    Task<SkillAnalysisResponse?> AnalyzeSkillsAsync(SkillAnalysisRequest request);

    /// <summary>POST /predict-feasibility — ML-based project feasibility prediction.</summary>
    Task<FeasibilityPredictionResponse?> PredictFeasibilityAsync(FeasibilityPredictionRequest request);

    /// <summary>POST /check-similarity — Originality check against previous FYP dataset.</summary>
    Task<SimilarityCheckResponse?> CheckSimilarityAsync(SimilarityCheckRequest request);

    /// <summary>POST /match-market — Match idea to Lebanese market needs dataset.</summary>
    Task<MarketMatchResponse?> MatchMarketAsync(MarketMatchRequest request);

    /// <summary>POST /risk-alarms — Generate risk alarms for a project profile.</summary>
    Task<RiskAlarmResponse?> GetRiskAlarmsAsync(RiskAlarmRequest request);
    /// <summary>POST /generate-ideas — Generate AI-based FYP ideas from student profile and skills.</summary>
    Task<GenerateIdeasResponse?> GenerateIdeasAsync(GenerateIdeasRequest request);
}
