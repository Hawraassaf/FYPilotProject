namespace FYPilot.Domain.Entities;

/// <summary>
/// One region's (Lebanon / MENA / Global) result within a
/// <see cref="MarketOpportunitySnapshot"/>.
/// </summary>
public class MarketOpportunityRegion
{
    public int Id { get; set; }

    public int SnapshotId { get; set; }

    /// <summary>lebanon | mena | global</summary>
    public string RegionKey { get; set; } = string.Empty;
    public string RegionName { get; set; } = string.Empty;

    public int? OpportunityScore { get; set; }
    public int ConfidenceScore { get; set; }
    public string DemandLevel { get; set; } = "Unavailable";

    /// <summary>low | medium | high — reported separately from OpportunityScore.</summary>
    public string CompetitionPressure { get; set; } = "medium";

    public string EvidenceSummary { get; set; } = string.Empty;
    public string ScoreBreakdownJson { get; set; } = "{}";
    public string SourceUrlsJson { get; set; } = "[]";

    public MarketOpportunitySnapshot Snapshot { get; set; } = null!;
}
