namespace FYPilot.Domain.Entities;

public class MarketTrendSignal
{
    public int Id { get; set; }
    public int MarketDemandAnalysisId { get; set; }
    public string Topic { get; set; } = string.Empty;
    public string Direction { get; set; } = "stable";
    public string Evidence { get; set; } = string.Empty;
    public string? SourceUrl { get; set; }
    public MarketDemandAnalysis MarketDemandAnalysis { get; set; } = null!;
}
