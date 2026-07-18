namespace FYPilot.Domain.Entities;

public class MarketDemandSource
{
    public int Id { get; set; }
    public int MarketDemandAnalysisId { get; set; }
    public string Title { get; set; } = string.Empty;
    public string Url { get; set; } = string.Empty;
    public string Publisher { get; set; } = string.Empty;
    public string Relevance { get; set; } = string.Empty;
    public int RelevanceScore { get; set; }
    public string SourceType { get; set; } = string.Empty;
    public bool IsVerified { get; set; }
    public DateTime AccessedAt { get; set; } = DateTime.UtcNow;
    public MarketDemandAnalysis MarketDemandAnalysis { get; set; } = null!;
}

