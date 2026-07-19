namespace FYPilot.Domain.Entities;

/// <summary>
/// A single Regional Demand Footprint analysis run for one project idea.
/// Historical snapshots are kept (never overwritten) so the Idea Generator
/// can always show the latest one without re-running research on every
/// page load.
/// </summary>
public class MarketOpportunitySnapshot
{
    public int Id { get; set; }

    public int ProjectIdeaId { get; set; }
    public int UserId { get; set; }

    /// <summary>ready | insufficient_evidence | provider_unavailable</summary>
    public string Status { get; set; } = "insufficient_evidence";

    public int? OverallOpportunityScore { get; set; }
    public int OverallConfidenceScore { get; set; }
    public string OverallDemandLevel { get; set; } = "Unavailable";

    public string BestLaunchMarket { get; set; } = string.Empty;
    public string BestLaunchReason { get; set; } = string.Empty;

    public string ExpansionPathJson { get; set; } = "[]";
    public string WhyDemandedJson { get; set; } = "[]";
    public string StrategicRecommendation { get; set; } = string.Empty;
    public string LimitationsJson { get; set; } = "[]";
    public string SourcesJson { get; set; } = "[]";

    public bool GroundedInLiveData { get; set; }
    public string Provider { get; set; } = string.Empty;
    public string? ModelUsed { get; set; }

    public DateTime AnalyzedAt { get; set; } = DateTime.UtcNow;
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public ProjectIdea ProjectIdea { get; set; } = null!;
    public User User { get; set; } = null!;
    public ICollection<MarketOpportunityRegion> Regions { get; set; } = [];
}
