namespace FYPilot.Application.DTOs;

public sealed record AnalyzeMarketNeedsRequest(
    string ProjectTitle,
    string ProblemStatement,
    string TargetUsers,
    string Domain,
    string Technologies,
    string CountryContext,
    int HistoryYears,
    int ForecastYears,
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

public sealed record MarketNeedsTrendSignalDto(
    string Topic,
    string Direction,
    string Evidence,
    string? SourceUrl
);

public sealed record MarketNeedsScoreBreakdownDto(
    int ProblemEvidence,
    int MarketFit,
    int UniversityValue,
    int CompetitionOpportunity,
    int TechnologyMomentum
);

public sealed record MarketNeedsYearlyPointDto(
    int Year,
    int ProblemSignal,
    int AdoptionSignal,
    int JobDemandSignal,
    int TechnologyMomentumSignal,
    decimal DemandIndex,
    int ConfidenceScore,
    string EvidenceSummary,
    List<string> SourceUrls
);

public sealed record MarketNeedsAnnualForecastPointDto(
    int Year,
    decimal PredictedScore,
    decimal LowerBound,
    decimal UpperBound
);

public sealed record MarketNeedsAnnualTrendDto(
    string Direction,
    string Strength,
    decimal SlopePerYear,
    decimal TotalChange,
    decimal Volatility,
    decimal RSquared,
    string Summary
);

public sealed record MarketNeedsAnnualForecastDto(
    string Status,
    bool ForecastReady,
    bool ForecastReliable,
    string? ModelUsed,
    decimal? ModelMae,
    decimal? NaiveMae,
    decimal AverageYearlyConfidence,
    int? HistoricalStartYear,
    int? HistoricalEndYear,
    int ForecastHorizonYears,
    MarketNeedsAnnualTrendDto Trend,
    List<MarketNeedsAnnualForecastPointDto> ForecastPoints,
    string? Warning
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
    List<MarketNeedsTrendSignalDto> TrendSignals,
    List<MarketNeedsYearlyPointDto> YearlyPoints,
    MarketNeedsAnnualForecastDto AnnualForecast,
    string HistoricalDataNote,
    string LebaneseMarketFit,
    string UniversityValue,
    List<string> Risks,
    string Recommendation,
    List<string> NextSteps,
    DateTime AnalyzedAt
);
