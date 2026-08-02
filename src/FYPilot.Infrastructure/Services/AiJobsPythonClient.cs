using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using FYPilot.Application.Interfaces;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// HTTP client for Python's centralized job endpoints (app/routers/ai_jobs.py
/// -- POST /ai-jobs/{agent-name}, GET /ai-jobs/{jobId}, GET /ai-jobs/{jobId}/result,
/// POST /ai-jobs/{jobId}/cancel). Deliberately separate from IAiServiceClient:
/// that client's per-agent methods are called from browser-facing Razor
/// Page handlers with the same generous timeout as the AI call itself; this
/// client is called only by AiAgentJobCoordinator on a fast poll loop and
/// must never let a slow/hung Python process block a coordinator tick for
/// long, so it uses its own short timeout.
/// </summary>
public class AiJobsPythonClient : IAiJobsPythonClient
{
    private readonly HttpClient _http;
    private readonly string _baseUrl;
    private readonly ILogger<AiJobsPythonClient> _logger;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true
    };

    public AiJobsPythonClient(IConfiguration configuration, ILogger<AiJobsPythonClient> logger)
    {
        _logger = logger;

        _http = new HttpClient
        {
            // Short and independent of any agent's own generation deadline
            // -- this client only ever calls cheap, near-instant endpoints
            // (start-accept, snapshot read, cancel-flag set). A slow
            // response here means Python itself is unhealthy, which the
            // coordinator handles as a failed poll, not a long wait.
            Timeout = TimeSpan.FromSeconds(8),
        };

        _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

        _baseUrl = Environment.GetEnvironmentVariable("AI_SERVICE_URL")
            ?? configuration["AiService:BaseUrl"]
            ?? "http://localhost:8000";

        var internalApiKey = Environment.GetEnvironmentVariable("AI_SERVICE_API_KEY")
            ?? configuration["AiService:InternalApiKey"];

        if (!string.IsNullOrWhiteSpace(internalApiKey))
        {
            _http.DefaultRequestHeaders.TryAddWithoutValidation("X-Internal-Api-Key", internalApiKey);
        }
    }

    public async Task<PythonJobStartResult> StartJobAsync(string agentName, Guid jobId, string requestJson, CancellationToken ct = default)
    {
        var url = $"{_baseUrl.TrimEnd('/')}/ai-jobs/{Uri.EscapeDataString(agentName)}";

        try
        {
            using var requestDocument = JsonDocument.Parse(requestJson);

            var body = JsonSerializer.Serialize(new
            {
                jobId = jobId.ToString(),
                request = requestDocument.RootElement,
            }, JsonOptions);

            using var httpRequest = new HttpRequestMessage(HttpMethod.Post, url)
            {
                Content = new StringContent(body, Encoding.UTF8, "application/json"),
            };

            using var response = await _http.SendAsync(httpRequest, ct);

            if (response.StatusCode is HttpStatusCode.Accepted or HttpStatusCode.OK)
            {
                return new PythonJobStartResult(Accepted: true, ErrorMessage: null);
            }

            var body2 = await response.Content.ReadAsStringAsync(ct);
            _logger.LogWarning("Python ai-jobs start for {AgentName}/{JobId} returned {Code}: {Body}", agentName, jobId, response.StatusCode, body2);
            return new PythonJobStartResult(Accepted: false, ErrorMessage: $"AI service returned {(int)response.StatusCode}");
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Python ai-jobs start failed for {AgentName}/{JobId}", agentName, jobId);
            return new PythonJobStartResult(Accepted: false, ErrorMessage: ex.Message);
        }
    }

    public async Task<PythonJobSnapshot> GetSnapshotAsync(Guid jobId, CancellationToken ct = default)
    {
        var url = $"{_baseUrl.TrimEnd('/')}/ai-jobs/{jobId}";

        try
        {
            using var response = await _http.GetAsync(url, ct);

            if (response.StatusCode == HttpStatusCode.NotFound)
            {
                return NotFoundSnapshot;
            }

            var json = await response.Content.ReadAsStringAsync(ct);

            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning("Python ai-jobs snapshot for {JobId} returned {Code}: {Body}", jobId, response.StatusCode, json);
                return NotFoundSnapshot;
            }

            var dto = JsonSerializer.Deserialize<PythonJobSnapshotDto>(json, JsonOptions);
            if (dto == null)
            {
                return NotFoundSnapshot;
            }

            return new PythonJobSnapshot(
                Found: true,
                WorkerStatus: dto.Status ?? "unknown",
                StageStatesJson: dto.StageStates != null ? JsonSerializer.Serialize(dto.StageStates, JsonOptions) : "{}",
                StageKey: dto.StageKey ?? "",
                Message: dto.Message ?? "",
                Provider: dto.Provider,
                Model: dto.Model,
                CurrentAttemptChunkCount: dto.CurrentAttemptChunkCount,
                CurrentAttemptTokenCount: dto.CurrentAttemptTokenCount,
                ProviderAttemptNumber: dto.ProviderAttemptNumber,
                ErrorMessage: dto.ErrorMessage);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
        {
            _logger.LogWarning(ex, "Python ai-jobs snapshot request failed for {JobId}", jobId);
            return NotFoundSnapshot;
        }
    }

    public async Task<string?> GetResultJsonAsync(Guid jobId, CancellationToken ct = default)
    {
        var url = $"{_baseUrl.TrimEnd('/')}/ai-jobs/{jobId}/result";

        try
        {
            using var response = await _http.GetAsync(url, ct);

            if (!response.IsSuccessStatusCode)
            {
                return null;
            }

            return await response.Content.ReadAsStringAsync(ct);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Python ai-jobs result request failed for {JobId}", jobId);
            return null;
        }
    }

    public async Task CancelAsync(Guid jobId, CancellationToken ct = default)
    {
        var url = $"{_baseUrl.TrimEnd('/')}/ai-jobs/{jobId}/cancel";

        try
        {
            using var response = await _http.PostAsync(url, content: null, ct);

            if (!response.IsSuccessStatusCode)
            {
                _logger.LogWarning("Python ai-jobs cancel for {JobId} returned {Code}", jobId, response.StatusCode);
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            _logger.LogWarning(ex, "Python ai-jobs cancel request failed for {JobId}", jobId);
        }
    }

    private static readonly PythonJobSnapshot NotFoundSnapshot = new(
        Found: false, WorkerStatus: "unknown", StageStatesJson: "{}", StageKey: "",
        Message: "", Provider: null, Model: null, CurrentAttemptChunkCount: 0,
        CurrentAttemptTokenCount: null, ProviderAttemptNumber: 0, ErrorMessage: null);

    private sealed class PythonJobSnapshotDto
    {
        public string? Status { get; set; }
        public Dictionary<string, string>? StageStates { get; set; }
        public string? StageKey { get; set; }
        public string? Message { get; set; }
        public string? Provider { get; set; }
        public string? Model { get; set; }
        public int CurrentAttemptChunkCount { get; set; }
        public int? CurrentAttemptTokenCount { get; set; }
        public int ProviderAttemptNumber { get; set; }
        public string? ErrorMessage { get; set; }
    }
}
