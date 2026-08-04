using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// HTTP client that calls the Python AI / Data Science microservice.
/// Implements IAiServiceClient — the only place in the .NET solution that
/// knows the Python service URL or HTTP details.
///
/// MERGE NOTE (see chat for full reasoning): two versions of this file
/// existed. One returned null on any AI-call failure (silent); the other
/// throws (loud). These are mutually exclusive behaviors — kept THROW,
/// because it is paired with the X-Internal-Api-Key security fix and is
/// already proven safe against MentorChat.cshtml.cs (wrapped in try/catch).
/// BEFORE RELYING ON THIS IN OTHER PAGES: confirm every other caller of
/// this client (IdeaGenerator, Roadmap, DefenseSimulator, etc.) wraps its
/// call in try/catch — otherwise an AI failure that used to degrade
/// gracefully will now surface as an unhandled 500.
/// </summary>
public class AiServiceClient : IAiServiceClient
{
    private readonly HttpClient _http;
    // Dedicated client for SE Documentation only -- its Python-side pipeline
    // deadline is 960s (see app/review/registry.py's SEDocumentationAgent
    // entry), so this must stay longer than that so .NET never abandons a
    // request while Python is still legitimately working on it. Kept
    // completely separate from _http (600s) so no other agent's timeout
    // changes: HttpClient.Timeout is a hard per-client ceiling that a
    // per-request CancellationToken can only shorten, never extend, so
    // sharing _http here would still cap this call at 600s regardless of
    // this class's own math.
    private readonly HttpClient _seDocumentationHttp;
    private readonly string _baseUrl;
    private readonly ILogger<AiServiceClient> _logger;

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true
    };

    // Used for endpoints whose Python/pydantic response fields are
    // camelCase (e.g. mentor chat, defense questions, market demand) — both
    // serializes outgoing requests AND deserializes responses as camelCase.
    private static readonly JsonSerializerOptions CamelCaseJsonOpts =
        new(JsonSerializerDefaults.Web)
        {
            PropertyNameCaseInsensitive = true
        };

    public AiServiceClient(
        IConfiguration configuration,
        ILogger<AiServiceClient> logger)
    {
        _logger = logger;

        // Ollama/Python generation can take time, so 10 seconds is too short.
        _http = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(600)
        };

        _http.DefaultRequestHeaders.Accept.Add(
            new MediaTypeWithQualityHeaderValue("application/json"));

        _baseUrl =
            Environment.GetEnvironmentVariable("AI_SERVICE_URL")
            ?? configuration["AiService:BaseUrl"]
            ?? "http://localhost:8000";

        // SEV-0 FIX (see audit Part 1): without this header, every call to
        // the Python service returns 401, because app/security.py enforces
        // X-Internal-Api-Key globally. Both sides must use the SAME value —
        // set AI_SERVICE_API_KEY as an env var on both .NET and FastAPI.
        var internalApiKey =
            Environment.GetEnvironmentVariable("AI_SERVICE_API_KEY")
            ?? configuration["AiService:InternalApiKey"];

        // 1020s: intentionally longer than SEDocumentationAgent's 960s Python
        // pipeline deadline (see app/review/registry.py) -- see the field
        // comment above for why this can't just be a longer CancellationToken
        // on the shared _http client.
        _seDocumentationHttp = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(1020)
        };

        _seDocumentationHttp.DefaultRequestHeaders.Accept.Add(
            new MediaTypeWithQualityHeaderValue("application/json"));

        if (!string.IsNullOrWhiteSpace(internalApiKey))
        {
            _http.DefaultRequestHeaders.TryAddWithoutValidation(
                "X-Internal-Api-Key",
                internalApiKey);

            _seDocumentationHttp.DefaultRequestHeaders.TryAddWithoutValidation(
                "X-Internal-Api-Key",
                internalApiKey);
        }
        else
        {
            _logger.LogWarning(
                "AI_SERVICE_API_KEY is not set. Every call to the AI service " +
                "will be rejected with 401 if the Python side enforces it.");
        }
    }

    // ── Helper ────────────────────────────────────────────────────────────────

    private Task<T?> PostAsync<T>(
        string path,
        object request,
        JsonSerializerOptions? requestOptions = null,
        CancellationToken cancellationToken = default) =>
        PostAsync<T>(path, request, requestOptions, cancellationToken, extraHeaders: null, client: _http);

    private async Task<T?> PostAsync<T>(
        string path,
        object request,
        JsonSerializerOptions? requestOptions,
        CancellationToken cancellationToken,
        IReadOnlyDictionary<string, string>? extraHeaders,
        HttpClient? client = null)
    {
        client ??= _http;
        var fullUrl =
            $"{_baseUrl.TrimEnd('/')}/{path.TrimStart('/')}";

        try
        {
            var requestJson = JsonSerializer.Serialize(
                request,
                requestOptions ?? JsonOpts);

            using var httpRequest = new HttpRequestMessage(HttpMethod.Post, fullUrl)
            {
                Content = new StringContent(
                    requestJson,
                    Encoding.UTF8,
                    "application/json"),
            };

            if (extraHeaders != null)
            {
                foreach (var (headerName, headerValue) in extraHeaders)
                {
                    httpRequest.Headers.TryAddWithoutValidation(headerName, headerValue);
                }
            }

            using var response = await client.SendAsync(
                httpRequest,
                cancellationToken);

            var responseJson =
                await response.Content.ReadAsStringAsync(cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning(
                    "AI service {Path} returned {Code}. Request: {Request}. Response: {Response}",
                    path,
                    (int)response.StatusCode,
                    requestJson,
                    responseJson);

                throw new HttpRequestException(
                    $"AI service call failed. URL: {fullUrl}. " +
                    $"Status: {(int)response.StatusCode}. " +
                    $"Response: {responseJson}");
            }

            try
            {
                // Always deserialize with plain JsonOpts (case-insensitive),
                // even for endpoints that serialize the OUTGOING request with
                // CamelCaseJsonOpts — this matches version 2's original,
                // intentional behavior exactly. Case-insensitive matching
                // already handles PascalCase-vs-camelCase on the way in;
                // CamelCaseJsonOpts exists to control OUTGOING serialization
                // to Python, not incoming parsing.
                return JsonSerializer.Deserialize<T>(
                    responseJson,
                    JsonOpts);
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException(
                    $"AI service response could not be deserialized. " +
                    $"URL: {fullUrl}. Response: {responseJson}. " +
                    $"Error: {ex.Message}",
                    ex);
            }
        }
        catch (Exception ex) when (ex is not HttpRequestException and not InvalidOperationException)
        {
            _logger.LogError(
                ex,
                "Failed to call {Path} on AI service at {Url}",
                path,
                _baseUrl);

            throw;
        }
    }

    // ── Health ────────────────────────────────────────────────────────────────

    public async Task<AiHealthResponse?> GetHealthAsync()
    {
        try
        {
            using var response = await _http.GetAsync(
                $"{_baseUrl.TrimEnd('/')}/health");

            var json = await response.Content.ReadAsStringAsync();

            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning(
                    "AI health endpoint returned {Code}. Response: {Response}",
                    (int)response.StatusCode,
                    json);

                return null;
            }

            return JsonSerializer.Deserialize<AiHealthResponse>(json, JsonOpts);
        }
        catch (Exception ex)
        {
            _logger.LogError(
                ex,
                "Failed to reach AI service /health at {Url}",
                _baseUrl);

            return null;
        }
    }

    // ── Skill Analysis ────────────────────────────────────────────────────────

    public Task<SkillAnalysisResponse?> AnalyzeSkillsAsync(SkillAnalysisRequest request) =>
        PostAsync<SkillAnalysisResponse>("/analyze-skills", request);

    // ── AI Idea Generation ────────────────────────────────────────────────────

    public Task<GenerateIdeasResponse?> GenerateIdeasAsync(GenerateIdeasRequest request) =>
        PostAsync<GenerateIdeasResponse>("/generate-ideas", request);

    // ── Feasibility Prediction ────────────────────────────────────────────────

    public Task<FeasibilityPredictionResponse?> PredictFeasibilityAsync(FeasibilityPredictionRequest request) =>
        PostAsync<FeasibilityPredictionResponse>("/predict-feasibility", request);

    // ── Similarity Check ──────────────────────────────────────────────────────

    public Task<SimilarityCheckResponse?> CheckSimilarityAsync(SimilarityCheckRequest request) =>
        PostAsync<SimilarityCheckResponse>("/check-similarity", request);

    // ── Market Matching ───────────────────────────────────────────────────────

    public Task<MarketMatchResponse?> MatchMarketAsync(MarketMatchRequest request) =>
        PostAsync<MarketMatchResponse>("/match-market", request);

    // ── Risk Alarms ───────────────────────────────────────────────────────────

    public Task<RiskAlarmResponse?> GetRiskAlarmsAsync(RiskAlarmRequest request) =>
        PostAsync<RiskAlarmResponse>("/risk-alarms", request);

    // ── Project DNA Analysis ──────────────────────────────────────────────────

    public Task<ProjectDnaServiceResponse?> AnalyzeProjectDnaAsync(ProjectDnaRequest request) =>
        PostAsync<ProjectDnaServiceResponse>("/analyze-project-dna", request);

    // ── Roadmap ────────────────────────────────────────────────────────────────

    public Task<ProjectRoadmapServiceResponse?> GenerateProjectRoadmapAsync(ProjectRoadmapRequest request) =>
        PostAsync<ProjectRoadmapServiceResponse>("/generate-project-roadmap", request);

    // ── Idea Comparison ────────────────────────────────────────────────────────

    public async Task<IdeaComparisonServiceResponse?> CompareGeneratedIdeasAsync(
        IdeaComparisonRequest request,
        TimeSpan deadline,
        CancellationToken cancellationToken = default)
    {
        // Enforced on both ends: the header lets the AI service stop
        // starting new provider/rewrite/reviewer work once it's spent its
        // share of the budget, and CancelAfter bounds the .NET-side wait
        // to the same deadline even if the AI service doesn't respect it
        // (e.g. it's stuck mid-way through a single provider call).
        using var deadlineCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadlineCts.CancelAfter(deadline);

        var headers = new Dictionary<string, string>
        {
            ["X-Request-Deadline-Ms"] = ((int)deadline.TotalMilliseconds).ToString(),
        };

        return await PostAsync<IdeaComparisonServiceResponse>(
            "/compare-generated-ideas",
            request,
            CamelCaseJsonOpts,
            deadlineCts.Token,
            headers);
    }

    // ── Defense Simulator ─────────────────────────────────────────────────────

    public Task<DefenseGenerateQuestionsResponse?> GenerateDefenseQuestionsAsync(
        DefenseGenerateQuestionsRequest request) =>
        PostAsync<DefenseGenerateQuestionsResponse>(
            "/defense-simulator/generate-questions",
            request,
            CamelCaseJsonOpts);
    public Task<DefenseEvaluateAnswerResponse?> EvaluateDefenseAnswerAsync(
    DefenseEvaluateAnswerRequest request) =>
    PostAsync<DefenseEvaluateAnswerResponse>(
        "/defense-simulator/evaluate-answer",
        request,
        CamelCaseJsonOpts);

    // ── Market Demand (real-time AI) ──────────────────────────────────────────
    // VERIFY: confirm this path matches what's actually registered in
    // app/main.py — earlier work in this project used /analyze-market-needs
    // (Gemini + search grounding) and /analyze-market-demand-live (Brave
    // Search + local LLM). This third path, /analyze-market-demand, must
    // match a real registered route or every call here will 404.
    public Task<AnalyzeMarketNeedsResponse?> AnalyzeMarketNeedsAsync(
        AnalyzeMarketNeedsRequest request,
        CancellationToken cancellationToken = default) =>
        PostAsync<AnalyzeMarketNeedsResponse>(
            "/analyze-market-demand",
            request,
            CamelCaseJsonOpts,
            cancellationToken);

    // ── Mentor Chat ────────────────────────────────────────────────────────────

    public Task<FypMentorServiceResponse?> AskFypMentorAsync(FypMentorRequest request) =>
        PostAsync<FypMentorServiceResponse>("/fyp-chat", request, CamelCaseJsonOpts);

    // ── SE Documentation ───────────────────────────────────────────────────────

    public Task<AiSeDocumentationServiceResponse?> GenerateSeDocumentationAsync(
        AiSeDocumentationRequest request) =>
        PostAsync<AiSeDocumentationServiceResponse>(
            "/generate-se-documentation",
            request,
            CamelCaseJsonOpts,
            cancellationToken: default,
            extraHeaders: null,
            client: _seDocumentationHttp);

    // ── Market Insight — Regional Demand Footprint ─────────────────────────────

    public Task<MarketFootprintResponse?> AnalyzeMarketFootprintAsync(
        MarketFootprintRequest request,
        CancellationToken cancellationToken = default) =>
        PostAsync<MarketFootprintResponse>(
            "/analyze-market-footprint",
            request,
            CamelCaseJsonOpts,
            cancellationToken);
}


