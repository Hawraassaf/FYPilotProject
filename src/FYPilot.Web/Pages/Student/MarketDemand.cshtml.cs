using System.ComponentModel.DataAnnotations;
using System.Security.Claims;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages.Student;

[Authorize(Roles = "student")]
public class MarketDemandModel(
    ApplicationDbContext db,
    IAiServiceClient aiServiceClient,
    ILogger<MarketDemandModel> logger) : PageModel
{
    private static readonly JsonSerializerOptions JsonOptions =
        new(JsonSerializerDefaults.Web);

    [BindProperty(SupportsGet = true)]
    public int IdeaId { get; set; }

    [BindProperty]
    public string CountryContext { get; set; } = "Lebanon";

    [BindProperty]
    [Range(4, 10)]
    public int HistoryYears { get; set; } = 6;

    [BindProperty]
    [Range(1, 5)]
    public int ForecastYears { get; set; } = 3;

    public ProjectIdea? Idea { get; private set; }
    public MarketDemandAnalysis? Analysis { get; private set; }
    public List<ProjectIdea> UserIdeas { get; private set; } = [];
    public List<string> ProblemEvidence { get; private set; } = [];
    public List<string> Risks { get; private set; } = [];
    public List<string> NextSteps { get; private set; } = [];
    public List<MarketNeedsAnnualForecastPointDto> ForecastPoints { get; private set; } = [];
    public string AnnualChartJson { get; private set; } = "[]";
    public string BreakdownJson { get; private set; } = "[]";
    public string? SuccessMessage { get; private set; }
    public string? ErrorMessage { get; private set; }

    /// <summary>
    /// The AI Quality Passport for the most recently generated Market Needs
    /// analysis for this idea, loaded from the database so it survives the
    /// Post/Redirect/Get cycle. Reuses the same AiOutputReview entity and
    /// AiQualityPassportDto as every other agent, linked via ProjectIdeaId.
    /// </summary>
    public AiOutputReview? LatestReview { get; private set; }

    public (string CssClass, string Label) DescribeReview(AiOutputReview review) => review.Status switch
    {
        "approved" => ("bg-success", "Reviewed"),
        "approved_with_minor_warnings" => ("bg-success", "Reviewed · minor notes"),
        "unresolved" => ("bg-warning text-dark", "Unresolved · shown as-is"),
        "rejected" => ("bg-danger", "Rejected · showing safe analysis"),
        "firewall_blocked" => ("bg-danger", "Blocked by content firewall"),
        "review_unavailable" => ("bg-secondary", "Not semantically reviewed"),
        "provider_unavailable" => ("bg-secondary", "AI service unavailable"),
        "schema_invalid" => ("bg-secondary", "Formatting issue"),
        _ => ("bg-secondary", review.Status),
    };

    public string HistoricalDataNote =>
        "Annual values are normalized evidence indices from 0 to 100. " +
        "They are not revenue, market size, or Google Trends values. " +
        "Only years linked to real research sources are displayed.";

    public int SourceBackedYearCount =>
        Analysis?.YearlyPoints.Count ?? 0;

    public async Task<IActionResult> OnGetAsync(
        CancellationToken cancellationToken)
    {
        SuccessMessage = TempData["Success"] as string;
        ErrorMessage = TempData["Error"] as string;
        return await LoadPageAsync(UserId(), cancellationToken);
    }

    public async Task<IActionResult> OnPostAnalyzeAsync(
        CancellationToken cancellationToken)
    {
        var userId = UserId();
        var idea = await db.ProjectIdeas.FirstOrDefaultAsync(
            item => item.Id == IdeaId && item.UserId == userId,
            cancellationToken);

        if (idea == null)
        {
            return NotFound();
        }

        CountryContext = NormalizeCountry(CountryContext);
        HistoryYears = Math.Clamp(HistoryYears, 4, 10);
        ForecastYears = Math.Clamp(ForecastYears, 1, 5);

        var request = new AnalyzeMarketNeedsRequest(
            ProjectTitle: SafeText(idea.Title, 300),
            ProblemStatement: SafeText(idea.ProblemStatement, 5000),
            TargetUsers: SafeText(idea.TargetUsers, 2000),
            Domain: SafeText(idea.Domain, 200),
            Technologies: SafeText(idea.RequiredTechnologies, 2000),
            CountryContext: CountryContext,
            HistoryYears: HistoryYears,
            ForecastYears: ForecastYears,
            UseSearch: true
        );

        try
        {
            var response = await aiServiceClient.AnalyzeMarketNeedsAsync(
                request,
                cancellationToken);

            if (response == null)
            {
                ErrorMessage = "The AI service returned no market analysis.";
                return await LoadPageAsync(userId, cancellationToken);
            }

            var analysis = MapAnalysis(
                response,
                userId,
                idea.Id,
                CountryContext);

            db.MarketDemandAnalysis.Add(analysis);
            idea.MarketDemandScore = analysis.DemandScore;

            var review = response.Review;

            if (review != null)
            {
                db.AiOutputReviews.Add(new AiOutputReview
                {
                    ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                        ? reviewRunId
                        : Guid.NewGuid(),
                    UserId = userId,
                    ProjectIdeaId = idea.Id,
                    MentorChatSessionId = null,
                    AgentName = "MarketNeedsAgent",
                    Status = review.Status,
                    Usable = review.Usable,
                    WasRewritten = review.Attempts > 1,
                    Attempts = review.Attempts,
                    QualityScore = review.QualityScore,
                    DecisionReason = review.DecisionReason,
                    GeneratorProvider = response.Provider,
                    GeneratorModel = response.ModelUsed,
                    ReviewerProvider = review.ReviewerProvider,
                    ReviewerModel = review.ReviewerModel,
                    FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
                    FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
                    FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
                    IssuesJson = JsonSerializer.Serialize(review.Issues),
                    StrengthsJson = JsonSerializer.Serialize(review.Strengths),
                    AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
                    ReviewerVersion = review.ReviewerVersion,
                    CreatedAt = DateTime.UtcNow,
                    CompletedAt = DateTime.UtcNow
                });
            }

            await db.SaveChangesAsync(cancellationToken);

            TempData["Success"] =
                $"Market intelligence saved with {analysis.Sources.Count} real source(s) " +
                $"and {analysis.YearlyPoints.Count} source-backed year(s).";

            return RedirectToPage(new { ideaId = idea.Id });
        }
        catch (HttpRequestException exception)
        {
            logger.LogError(
                exception,
                "Market-demand request failed for idea {IdeaId}.",
                idea.Id);

            ErrorMessage =
                "The Python market service could not be reached. Confirm that " +
                "FastAPI is running and the internal API keys match.";
        }
        catch (OperationCanceledException)
            when (HttpContext.RequestAborted.IsCancellationRequested)
        {
            return new EmptyResult();
        }
        catch (Exception exception)
        {
            logger.LogError(
                exception,
                "Unexpected market-demand error for idea {IdeaId}.",
                idea.Id);

            ErrorMessage =
                "An unexpected error occurred while analyzing market demand.";
        }

        return await LoadPageAsync(userId, cancellationToken);
    }

    private async Task<IActionResult> LoadPageAsync(
        int userId,
        CancellationToken cancellationToken)
    {
        UserIdeas = await db.ProjectIdeas
            .AsNoTracking()
            .Where(item => item.UserId == userId)
            .OrderByDescending(item => item.IsSelected)
            .ThenByDescending(item => item.CreatedAt)
            .Take(50)
            .ToListAsync(cancellationToken);

        if (UserIdeas.Count == 0)
        {
            return Page();
        }

        if (IdeaId <= 0)
        {
            IdeaId = UserIdeas.FirstOrDefault(item => item.IsSelected)?.Id
                ?? UserIdeas[0].Id;
        }

        Idea = UserIdeas.FirstOrDefault(item => item.Id == IdeaId);
        if (Idea == null)
        {
            return NotFound();
        }

        LatestReview = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r =>
                r.ProjectIdeaId == IdeaId &&
                r.UserId == userId &&
                r.AgentName == "MarketNeedsAgent")
            .OrderByDescending(r => r.CreatedAt)
            .FirstOrDefaultAsync(cancellationToken);

        Analysis = await db.MarketDemandAnalysis
            .AsNoTracking()
            .Include(item => item.Sources)
            .Include(item => item.SimilarSolutions)
            .Include(item => item.TrendSignals)
            .Include(item => item.YearlyPoints)
            .Where(item =>
                item.UserId == userId &&
                item.ProjectIdeaId == IdeaId)
            .OrderByDescending(item => item.AnalyzedAt)
            .ThenByDescending(item => item.Id)
            .FirstOrDefaultAsync(cancellationToken);

        if (Analysis == null)
        {
            return Page();
        }

        CountryContext = Analysis.CountryContext;
        ProblemEvidence = ParseJsonList(Analysis.ProblemEvidenceJson);
        Risks = ParseJsonList(Analysis.RisksJson);
        NextSteps = ParseJsonList(Analysis.NextStepsJson);
        ForecastPoints = ParseForecastPoints(Analysis.ForecastPointsJson);

        var observed = Analysis.YearlyPoints
            .OrderBy(item => item.Year)
            .Select(item => new
            {
                year = item.Year,
                observed = item.DemandIndex,
                confidence = item.ConfidenceScore,
                forecast = (decimal?)null,
                lower = (decimal?)null,
                upper = (decimal?)null,
                kind = "observed"
            });

        var forecast = ForecastPoints
            .OrderBy(item => item.Year)
            .Select(item => new
            {
                year = item.Year,
                observed = (decimal?)null,
                confidence = (int?)null,
                forecast = (decimal?)item.PredictedScore,
                lower = (decimal?)item.LowerBound,
                upper = (decimal?)item.UpperBound,
                kind = "forecast"
            });

        AnnualChartJson = JsonSerializer.Serialize(
            observed.Cast<object>().Concat(forecast.Cast<object>()),
            JsonOptions);

        BreakdownJson = JsonSerializer.Serialize(
            new[]
            {
                new { label = "Problem evidence", value = Analysis.ProblemEvidenceScore },
                new { label = "Market fit", value = Analysis.MarketFitScore },
                new { label = "University value", value = Analysis.UniversityValueScore },
                new { label = "Competition opportunity", value = Analysis.CompetitionOpportunityScore },
                new { label = "Technology momentum", value = Analysis.TechnologyMomentumScore }
            },
            JsonOptions);

        return Page();
    }

    private static MarketDemandAnalysis MapAnalysis(
        AnalyzeMarketNeedsResponse response,
        int userId,
        int projectIdeaId,
        string countryContext)
    {
        var breakdown = response.ScoreBreakdown
            ?? new MarketNeedsScoreBreakdownDto(0, 0, 0, 0, 0);
        var forecast = response.AnnualForecast;
        var trend = forecast?.Trend;

        var analysis = new MarketDemandAnalysis
        {
            UserId = userId,
            ProjectIdeaId = projectIdeaId,
            DemandScore = ClampScore(response.DemandScore),
            ConfidenceScore = ClampScore(response.ConfidenceScore),
            ProblemEvidenceScore = ClampScore(breakdown.ProblemEvidence),
            MarketFitScore = ClampScore(breakdown.MarketFit),
            UniversityValueScore = ClampScore(breakdown.UniversityValue),
            CompetitionOpportunityScore = ClampScore(breakdown.CompetitionOpportunity),
            TechnologyMomentumScore = ClampScore(breakdown.TechnologyMomentum),
            MarketDemand = SafeText(response.MarketDemand, 50),
            TargetSector = SafeText(response.TargetSector, 300),
            CountryContext = NormalizeCountry(countryContext),
            Source = SafeText(response.Source, 120),
            Provider = SafeText(response.Provider, 120),
            ModelUsed = SafeTextOrNull(response.ModelUsed, 200),
            SearchUsed = response.SearchUsed,
            SearchProvider = SafeTextOrNull(response.SearchProvider, 200),
            GroundedInLiveData = response.GroundedInLiveData,
            ConfidenceLevel = SafeText(response.ConfidenceLevel, 30),
            CloudError = SafeTextOrNull(response.CloudError, 2000),
            ProblemEvidenceJson = JsonSerializer.Serialize(CleanList(response.ProblemEvidence, 8)),
            LebaneseMarketFit = SafeText(response.LebaneseMarketFit, 8000),
            UniversityValue = SafeText(response.UniversityValue, 8000),
            RisksJson = JsonSerializer.Serialize(CleanList(response.Risks, 8)),
            Recommendation = SafeText(response.Recommendation, 8000),
            NextStepsJson = JsonSerializer.Serialize(CleanList(response.NextSteps, 8)),
            ForecastStatus = SafeText(forecast?.Status, 80),
            ForecastReady = forecast?.ForecastReady ?? false,
            ForecastReliable = forecast?.ForecastReliable ?? false,
            ForecastModel = SafeTextOrNull(forecast?.ModelUsed, 120),
            ForecastMae = forecast?.ModelMae,
            NaiveForecastMae = forecast?.NaiveMae,
            ForecastPointsJson = JsonSerializer.Serialize(forecast?.ForecastPoints ?? []),
            ForecastWarning = SafeTextOrNull(forecast?.Warning, 3000),
            ForecastGeneratedAt = DateTime.UtcNow,
            TrendDirection = NormalizeTrendDirection(trend?.Direction),
            TrendStrength = NormalizeTrendStrength(trend?.Strength),
            TrendSlopePerYear = trend?.SlopePerYear,
            TrendTotalChange = trend?.TotalChange,
            TrendVolatility = trend?.Volatility,
            TrendRSquared = trend == null ? null : Math.Clamp(trend.RSquared, 0, 1),
            TrendSummary = SafeTextOrNull(trend?.Summary, 3000),
            AnalyzedAt = response.AnalyzedAt == default
                ? DateTime.UtcNow
                : response.AnalyzedAt.ToUniversalTime()
        };

        foreach (var point in (response.YearlyPoints ?? [])
            .GroupBy(item => item.Year)
            .Select(group => group.OrderByDescending(item => item.ConfidenceScore).First())
            .OrderBy(item => item.Year)
            .Take(10))
        {
            analysis.YearlyPoints.Add(new MarketDemandYearlyPoint
            {
                Year = point.Year,
                ProblemSignal = ClampScore(point.ProblemSignal),
                AdoptionSignal = ClampScore(point.AdoptionSignal),
                JobDemandSignal = ClampScore(point.JobDemandSignal),
                TechnologyMomentumSignal = ClampScore(point.TechnologyMomentumSignal),
                DemandIndex = Math.Clamp(point.DemandIndex, 0, 100),
                ConfidenceScore = ClampScore(point.ConfidenceScore),
                EvidenceSummary = SafeText(point.EvidenceSummary, 3000),
                SourceUrlsJson = JsonSerializer.Serialize(
                    (point.SourceUrls ?? [])
                    .Where(IsSafeHttpUrl)
                    .Distinct(StringComparer.OrdinalIgnoreCase)
                    .Take(6))
            });
        }

        foreach (var source in (response.Sources ?? [])
            .Where(item => !string.IsNullOrWhiteSpace(item.Title) && IsSafeHttpUrl(item.Url))
            .GroupBy(item => item.Url.Trim(), StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First())
            .OrderByDescending(item => item.IsVerified)
            .ThenByDescending(item => item.RelevanceScore)
            .Take(14))
        {
            analysis.Sources.Add(new MarketDemandSource
            {
                Title = SafeText(source.Title, 500),
                Url = SafeText(source.Url, 2000),
                Publisher = SafeText(source.Publisher, 250),
                Relevance = SafeText(source.Relevance, 2500),
                RelevanceScore = ClampScore(source.RelevanceScore),
                SourceType = SafeText(source.SourceType, 100),
                IsVerified = source.IsVerified,
                AccessedAt = DateTime.UtcNow
            });
        }

        foreach (var item in (response.SimilarSolutions ?? []).Take(6))
        {
            if (string.IsNullOrWhiteSpace(item.Name)) continue;
            analysis.SimilarSolutions.Add(new MarketSimilarSolution
            {
                Name = SafeText(item.Name, 300),
                Description = SafeText(item.Description, 3000),
                Similarity = NormalizeSimilarity(item.Similarity)
            });
        }

        foreach (var item in (response.TrendSignals ?? []).Take(6))
        {
            if (string.IsNullOrWhiteSpace(item.Topic)) continue;
            analysis.TrendSignals.Add(new MarketTrendSignal
            {
                Topic = SafeText(item.Topic, 300),
                Direction = NormalizeQualitativeDirection(item.Direction),
                Evidence = SafeText(item.Evidence, 3000),
                SourceUrl = IsSafeHttpUrl(item.SourceUrl)
                    ? SafeText(item.SourceUrl, 2000)
                    : null
            });
        }

        return analysis;
    }

    private static List<MarketNeedsAnnualForecastPointDto> ParseForecastPoints(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return [];
        try
        {
            return JsonSerializer.Deserialize<List<MarketNeedsAnnualForecastPointDto>>(
                value,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }

    private static List<string> ParseJsonList(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return [];
        try
        {
            return JsonSerializer.Deserialize<List<string>>(value)
                ?.Where(item => !string.IsNullOrWhiteSpace(item))
                .ToList() ?? [];
        }
        catch (JsonException)
        {
            return [];
        }
    }

    private static List<string> CleanList(IEnumerable<string>? values, int maximum) =>
        (values ?? [])
        .Where(value => !string.IsNullOrWhiteSpace(value))
        .Select(value => SafeText(value, 2000))
        .Take(maximum)
        .ToList();

    private static bool IsSafeHttpUrl(string? value) =>
        Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps);

    private static int ClampScore(int value) => Math.Clamp(value, 0, 100);

    private static string NormalizeCountry(string? value) => value?.Trim() switch
    {
        "MENA" => "MENA",
        "Global" => "Global",
        _ => "Lebanon"
    };

    private static string NormalizeSimilarity(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "low" => "low",
        "high" => "high",
        _ => "medium"
    };

    private static string NormalizeQualitativeDirection(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "rising" => "rising",
        "falling" => "falling",
        _ => "stable"
    };

    private static string NormalizeTrendDirection(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "rising" => "rising",
        "falling" => "falling",
        "stable" => "stable",
        "unstable" => "unstable",
        _ => "insufficient-data"
    };

    private static string NormalizeTrendStrength(string? value) => value?.Trim().ToLowerInvariant() switch
    {
        "strong" => "strong",
        "moderate" => "moderate",
        "weak" => "weak",
        _ => "insufficient-data"
    };

    private static string SafeText(string? value, int maximumLength)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var clean = value.Trim();
        return clean.Length <= maximumLength ? clean : clean[..maximumLength];
    }

    private static string? SafeTextOrNull(string? value, int maximumLength)
    {
        var clean = SafeText(value, maximumLength);
        return string.IsNullOrWhiteSpace(clean) ? null : clean;
    }

    private int UserId() => int.Parse(
        User.FindFirst(ClaimTypes.NameIdentifier)!.Value);
}
