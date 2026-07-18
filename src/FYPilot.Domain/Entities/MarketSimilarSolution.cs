namespace FYPilot.Domain.Entities;

public class MarketSimilarSolution
{
    public int Id { get; set; }
    public int MarketDemandAnalysisId { get; set; }
    public string Name { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;
    public string Similarity { get; set; } = "medium";
    public MarketDemandAnalysis MarketDemandAnalysis { get; set; } = null!;
}
