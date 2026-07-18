namespace FYPilot.Domain.Entities;

public class MarketDemandYearlyPoint
{
    public int Id { get; set; }
    public int MarketDemandAnalysisId { get; set; }

    public int Year { get; set; }
    public int ProblemSignal { get; set; }
    public int AdoptionSignal { get; set; }
    public int JobDemandSignal { get; set; }
    public int TechnologyMomentumSignal { get; set; }
    public decimal DemandIndex { get; set; }
    public int ConfidenceScore { get; set; }
    public string EvidenceSummary { get; set; } = string.Empty;
    public string SourceUrlsJson { get; set; } = "[]";

    public MarketDemandAnalysis MarketDemandAnalysis { get; set; } = null!;
}
