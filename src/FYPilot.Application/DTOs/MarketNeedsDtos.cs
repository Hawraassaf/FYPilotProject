namespace FYPilot.Application.DTOs;

public sealed record AnalyzeMarketNeedsRequest(
    string ProjectTitle,
    string ProblemStatement,
    string TargetUsers,
    string Domain,
    string Technologies,
    string CountryContext,
    bool UseSearch
);

public sealed record MarketNeedsSourceDto(
    string Title,
    string Url,
    string Publisher,
    string Relevance,
    int RelevanceScore,
    string SourceType,
    bool IsVerified
);

public sealed record MarketNeedsSimilarSolutionDto(
    string Name,
    string Description,
    string Similarity
);

public sealed record MarketNeedsScoreBreakdownDto(
    int ProblemEvidence,
    int MarketFit,
    int UniversityValue,
    int CompetitionOpportunity,
    int TechnologyMomentum
);

public sealed record AnalyzeMarketNeedsResponse(
    string Source,
    string Provider,
    string? ModelUsed,
    bool SearchUsed,
    string? SearchProvider,
    bool GroundedInLiveData,
    string ConfidenceLevel,
    int ConfidenceScore,
    string? CloudError,
    string MarketDemand,
    int DemandScore,
    MarketNeedsScoreBreakdownDto ScoreBreakdown,
    string TargetSector,
    List<string> ProblemEvidence,
    List<MarketNeedsSimilarSolutionDto> SimilarSolutions,
    List<MarketNeedsSourceDto> Sources,
    string LebaneseMarketFit,
    string UniversityValue,
    List<string> Risks,
    string Recommendation,
    List<string> NextSteps,
    DateTime AnalyzedAt,
    AiQualityPassportDto? Review = null
);
