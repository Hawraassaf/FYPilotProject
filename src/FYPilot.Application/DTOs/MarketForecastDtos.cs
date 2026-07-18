namespace FYPilot.Application.DTOs;

public sealed record MarketForecastHistoryPointDto(
    DateTime Timestamp,
    int DemandScore,
    int? ConfidenceScore
);

public sealed record ForecastMarketDemandRequest(
    List<MarketForecastHistoryPointDto> Points,
    int HorizonWeeks
);

public sealed record MarketForecastPointDto(
    DateTime Period,
    int HorizonWeek,
    decimal PredictedScore,
    decimal LowerBound,
    decimal UpperBound
);

public sealed record MarketTrendAnalysisDto(
    string Direction,
    string Strength,
    decimal SlopePerWeek,
    decimal TotalChange,
    decimal Volatility,
    decimal RSquared,
    string Summary
);

public sealed record ForecastMarketDemandResponse(
    string Status,
    bool ForecastReady,
    bool ForecastReliable,
    int ObservedPoints,
    int MinimumPoints,
    int RecommendedPoints,
    string? ModelUsed,
    decimal? ModelMae,
    decimal? NaiveMae,
    MarketTrendAnalysisDto Trend,
    List<MarketForecastPointDto> ForecastPoints,
    string? Warning,
    DateTime GeneratedAt
);