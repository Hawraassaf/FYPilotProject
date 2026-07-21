namespace FYPilot.Application.DTOs;

// ── Outgoing request to POST /analyze-market-footprint ──────────────────────
// Serialized with AiServiceClient.CamelCaseJsonOpts, so plain PascalCase
// properties here already map to the Python service's camelCase field names.

public sealed record MarketFootprintRequest(
    string ProjectTitle,
    string ProblemStatement,
    string TargetUsers,
    string Domain,
    string Technologies,
    bool UseSearch
);

// ── Incoming response ─────────────────────────────────────────────────────────
// Deserialized case-insensitively (AiServiceClient.JsonOpts), so plain
// PascalCase properties here match the Python service's camelCase JSON.
// Score fields are nullable: a region (or the whole analysis) may be
// unavailable when evidence could not be verified.

public sealed record MarketFootprintRegionScoreBreakdown(
    int ProblemUrgency,
    int GeographicFit,
    int AdoptionReadiness,
    int CompetitionGap,
    int TargetUserReachability,
    int TechnologyMomentum,
    int EvidenceStrength
);

public sealed record MarketFootprintRegionResult(
    string RegionKey,
    string RegionName,
    int? OpportunityScore,
    int ConfidenceScore,
    string DemandLevel,
    string CompetitionPressure,
    string EvidenceSummary,
    MarketFootprintRegionScoreBreakdown? ScoreBreakdown,
    List<string> SourceTitles,
    List<string> SourceUrls
);

public sealed record MarketFootprintSourceItem(
    string Title,
    string Url,
    string Publisher,
    string Relevance,
    int RelevanceScore,
    bool IsVerified,
    List<string> Regions
);

public sealed record MarketFootprintResponse(
    string Status,
    string Provider,
    string? ModelUsed,
    bool GroundedInLiveData,
    int? OverallOpportunityScore,
    int OverallConfidenceScore,
    string OverallDemandLevel,
    string BestLaunchMarket,
    string BestLaunchReason,
    List<string> ExpansionPath,
    List<string> WhyDemanded,
    string StrategicRecommendation,
    List<string> Limitations,
    List<MarketFootprintRegionResult> Regions,
    List<MarketFootprintSourceItem> Sources,
    DateTime AnalyzedAt,
    AiQualityPassportDto? Review = null
);
