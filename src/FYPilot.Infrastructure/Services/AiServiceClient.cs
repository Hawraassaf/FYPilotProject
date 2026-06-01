using System.Text;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// HTTP client that calls the Python AI / Data Science microservice.
/// Implements IAiServiceClient — the only place in the .NET solution that
/// knows the Python service URL or HTTP details.
/// </summary>
public class AiServiceClient : IAiServiceClient
{
    private readonly HttpClient _http;
    private readonly string _baseUrl;
    private readonly ILogger<AiServiceClient> _logger;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    public AiServiceClient(IConfiguration configuration, ILogger<AiServiceClient> logger)
    {
        _logger = logger;

        // Ollama/Python idea generation can take time, so 10 seconds is too short.
        _http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(600)
        };

        _baseUrl = Environment.GetEnvironmentVariable("AI_SERVICE_URL")
                ?? configuration["AiService:BaseUrl"]
                ?? "http://localhost:8000";
    }

    // ── Helper ────────────────────────────────────────────────────────────────
    private async Task<T?> PostAsync<T>(string path, object request)
    {
        try
        {
            var body = new StringContent(
                JsonSerializer.Serialize(request),
                Encoding.UTF8,
                "application/json"
            );

            var resp = await _http.PostAsync($"{_baseUrl}{path}", body);
            var json = await resp.Content.ReadAsStringAsync();

            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning(
                    "AI service {Path} returned {Code}: {Body}",
                    path,
                    (int)resp.StatusCode,
                    json
                );

                return default;
            }

            return JsonSerializer.Deserialize<T>(json, JsonOpts);
        }
        catch (Exception ex)
        {
            _logger.LogError(
                ex,
                "Failed to call {Path} on AI service at {Url}",
                path,
                _baseUrl
            );

            return default;
        }
    }

    // ── Health ────────────────────────────────────────────────────────────────
    public async Task<AiHealthResponse?> GetHealthAsync()
    {
        try
        {
            var resp = await _http.GetAsync($"{_baseUrl}/health");
            var json = await resp.Content.ReadAsStringAsync();

            return JsonSerializer.Deserialize<AiHealthResponse>(json, JsonOpts);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to reach AI service /health at {Url}", _baseUrl);
            return null;
        }
    }

    // ── Skill Analysis ────────────────────────────────────────────────────────
    public Task<SkillAnalysisResponse?> AnalyzeSkillsAsync(SkillAnalysisRequest request)
        => PostAsync<SkillAnalysisResponse>("/analyze-skills", request);

    // ── AI Idea Generation ────────────────────────────────────────────────────
    public Task<GenerateIdeasResponse?> GenerateIdeasAsync(GenerateIdeasRequest request)
        => PostAsync<GenerateIdeasResponse>("/generate-ideas", request);

    // ── Feasibility Prediction ────────────────────────────────────────────────
    public Task<FeasibilityPredictionResponse?> PredictFeasibilityAsync(FeasibilityPredictionRequest request)
        => PostAsync<FeasibilityPredictionResponse>("/predict-feasibility", request);

    // ── Similarity Check ──────────────────────────────────────────────────────
    public Task<SimilarityCheckResponse?> CheckSimilarityAsync(SimilarityCheckRequest request)
        => PostAsync<SimilarityCheckResponse>("/check-similarity", request);

    // ── Market Matching ───────────────────────────────────────────────────────
    public Task<MarketMatchResponse?> MatchMarketAsync(MarketMatchRequest request)
        => PostAsync<MarketMatchResponse>("/match-market", request);

    // ── Risk Alarms ───────────────────────────────────────────────────────────
    public Task<RiskAlarmResponse?> GetRiskAlarmsAsync(RiskAlarmRequest request)
        => PostAsync<RiskAlarmResponse>("/risk-alarms", request);
}