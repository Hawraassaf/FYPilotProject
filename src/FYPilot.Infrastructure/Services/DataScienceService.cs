using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// HTTP client that calls the Python Data Science microservice for advanced analytics.
/// Used by DataScienceController and AiController for /ds/* endpoints.
/// For core AI health/skill-analysis, use IAiServiceClient instead.
/// </summary>
public class DataScienceService
{
    private readonly HttpClient _http = new();
    private readonly string _baseUrl;
    private readonly ILogger<DataScienceService> _logger;

    public DataScienceService(IConfiguration configuration, ILogger<DataScienceService> logger)
    {
        _logger = logger;
        _baseUrl = Environment.GetEnvironmentVariable("AI_SERVICE_URL")
            ?? configuration["AiService:BaseUrl"]
            ?? "http://localhost:8000";
    }

    private HttpRequestMessage BuildRequest(HttpMethod method, string path, string token, object? body = null)
    {
        var req = new HttpRequestMessage(method, $"{_baseUrl}{path}");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        if (body != null)
            req.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        return req;
    }

    private async Task<T?> SendAsync<T>(HttpRequestMessage req)
    {
        try
        {
            var resp = await _http.SendAsync(req);
            var json = await resp.Content.ReadAsStringAsync();
            if (!resp.IsSuccessStatusCode)
            {
                _logger.LogWarning("AI service returned {Code}: {Body}", (int)resp.StatusCode, json);
                return default;
            }
            return JsonSerializer.Deserialize<T>(json, new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to reach AI service at {Url}", _baseUrl);
            return default;
        }
    }

    public Task<JsonElement?> GetRiskAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/analytics/risk/{projectId}", token));

    public Task<JsonElement?> GetBurndownAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/analytics/burndown/{projectId}", token));

    public Task<JsonElement?> GetGradePredictionAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/analytics/grade/{projectId}", token));

    public Task<JsonElement?> GetAnomaliesAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/analytics/anomalies/{projectId}", token));

    public Task<JsonElement?> GetAnalyticsDashboardAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/analytics/dashboard/{projectId}", token));

    public Task<JsonElement?> GenerateRoadmapAsync(object request, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Post, "/ds/intelligence/roadmap", token, request));

    public Task<JsonElement?> GetSimilarityAsync(int projectId, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Get, $"/ds/intelligence/similarity/{projectId}", token));

    public Task<JsonElement?> MatchSupervisorsAsync(object request, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Post, "/ds/intelligence/supervisor-match", token, request));

    public Task<JsonElement?> AnalyzeSkillGapAsync(object request, string token) =>
        SendAsync<JsonElement?>(BuildRequest(HttpMethod.Post, "/ds/intelligence/skill-gap", token, request));
}
