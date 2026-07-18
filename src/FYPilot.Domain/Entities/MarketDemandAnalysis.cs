namespace FYPilot.Domain.Entities;

public class MarketDemandAnalysis
{
    public int Id { get; set; }
    public int ProjectIdeaId { get; set; }
    public int UserId { get; set; }

    public int DemandScore { get; set; }
    public int ConfidenceScore { get; set; }

    public int ProblemEvidenceScore { get; set; }
    public int MarketFitScore { get; set; }
    public int UniversityValueScore { get; set; }
    public int CompetitionOpportunityScore { get; set; }
    public int TechnologyMomentumScore { get; set; }

    public string MarketDemand { get; set; } = string.Empty;
    public string TargetSector { get; set; } = string.Empty;
    public string CountryContext { get; set; } = "Lebanon";

    public string Source { get; set; } = string.Empty;
    public string Provider { get; set; } = string.Empty;
    public string? ModelUsed { get; set; }
    public bool SearchUsed { get; set; }
    public string? SearchProvider { get; set; }
    public bool GroundedInLiveData { get; set; }

    public string ConfidenceLevel { get; set; } = string.Empty;
    public string? CloudError { get; set; }

    public string ProblemEvidenceJson { get; set; } = "[]";
    public string LebaneseMarketFit { get; set; } = string.Empty;
    public string UniversityValue { get; set; } = string.Empty;
    public string RisksJson { get; set; } = "[]";
    public string Recommendation { get; set; } = string.Empty;
    public string NextStepsJson { get; set; } = "[]";
    // These existing forecast columns now represent annual forecasting.
    public string ForecastStatus { get; set; } = "insufficient-source-backed-history";
    public bool ForecastReady { get; set; }
    public bool ForecastReliable { get; set; }
    public string? ForecastModel { get; set; }
    public decimal? ForecastMae { get; set; }
    public decimal? NaiveForecastMae { get; set; }
    public string ForecastPointsJson { get; set; } = "[]";
    public string? ForecastWarning { get; set; }
    public DateTime? ForecastGeneratedAt { get; set; }

    public string TrendDirection { get; set; } = "insufficient-data";
    public string TrendStrength { get; set; } = "insufficient-data";

    // ApplicationDbContext maps this to the legacy TrendSlopePerWeek column,
    // so the already-applied database migration remains compatible.
    public decimal? TrendSlopePerYear { get; set; }

    public decimal? TrendTotalChange { get; set; }
    public decimal? TrendVolatility { get; set; }
    public decimal? TrendRSquared { get; set; }
    public string? TrendSummary { get; set; }

    public DateTime AnalyzedAt { get; set; } = DateTime.UtcNow;

    public ProjectIdea ProjectIdea { get; set; } = null!;
    public ICollection<MarketDemandSource> Sources { get; set; } = [];
    public ICollection<MarketSimilarSolution> SimilarSolutions { get; set; } = [];
    public ICollection<MarketTrendSignal> TrendSignals { get; set; } = [];
    public ICollection<MarketDemandYearlyPoint> YearlyPoints { get; set; } = [];
}
