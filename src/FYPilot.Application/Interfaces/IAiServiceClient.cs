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

    /// <summary>POST /defense-simulator/generate-questions — Generate defense simulator questions.</summary>
    Task<DefenseGenerateQuestionsResponse?> GenerateDefenseQuestionsAsync(
    DefenseGenerateQuestionsRequest request
);

    Task<DefenseEvaluateAnswerResponse?> EvaluateDefenseAnswerAsync(
        DefenseEvaluateAnswerRequest request
    );

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

    /// <summary>POST /analyze-project-dna — Analyze a project idea's "DNA" (strengths/weaknesses/risk).</summary>
    Task<ProjectDnaServiceResponse?> AnalyzeProjectDnaAsync(ProjectDnaRequest request);

    /// <summary>POST /generate-project-roadmap — Generate a phased implementation roadmap.</summary>
    Task<ProjectRoadmapServiceResponse?> GenerateProjectRoadmapAsync(ProjectRoadmapRequest request);

    /// <summary>POST /analyze-market-needs — Real-time market demand analysis for a project idea.</summary>
    Task<AnalyzeMarketNeedsResponse?> AnalyzeMarketNeedsAsync(
        AnalyzeMarketNeedsRequest request,
        CancellationToken cancellationToken = default
    );

    /// <summary>POST /compare-generated-ideas — Compare and rank generated ideas.</summary>
    Task<IdeaComparisonServiceResponse?> CompareGeneratedIdeasAsync(IdeaComparisonRequest request);

    /// <summary>POST /fyp-chat — Ask the FYP mentor a question.</summary>
    Task<FypMentorServiceResponse?> AskFypMentorAsync(FypMentorRequest request);
}
