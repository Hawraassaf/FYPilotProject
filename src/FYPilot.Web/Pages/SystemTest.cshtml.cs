using System.Diagnostics;
using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Web.Pages;

[Authorize(Roles = "admin")]
public class SystemTestModel(
    ApplicationDbContext db,
    IAiServiceClient ai,
    ILogger<SystemTestModel> logger)
    : PageModel
{
    /*
     * Required routes are only the routes used by the current main
     * application features.
     *
     * Optional/dead feature placeholders are deliberately excluded.
     */
    private static readonly string[] RequiredAiRoutes =
    [
        "/analyze-skills",
        "/generate-ideas",
        "/analyze-project-dna",
        "/generate-project-roadmap",
        "/analyze-market-demand",
        "/analyze-market-footprint",
        "/fyp-chat",
        "/generate-se-documentation",
        "/defense-simulator/generate-questions",
        "/defense-simulator/evaluate-answer",
        "/ai-jobs/{agent}"
    ];

    /*
     * These methods remain in IAiServiceClient for compatibility, but the
     * audit found that their Python routers are absent/dead in the current
     * application.
     *
     * Their presence is a cleanup warning, not an infrastructure outage.
     */
    private static readonly string[] LegacyAiRoutes =
    [
        "/predict-feasibility",
        "/check-similarity",
        "/match-market",
        "/risk-alarms"
    ];

    private static readonly string[] ReviewAgentNames =
    [
        "ProjectIdeaAgent",
        "ProjectRoadmapAgent",
        "SEDocumentationAgent",
        "ProjectDNAAgent",
        "IdeaComparisonAgent",
        "MarketNeedsAgent",
        "MarketFootprintAgent",
        "FypMentorAgent",
        "DefenseQuestionAgent",
        "DefenseEvaluatorAgent"
    ];

    private static readonly Regex RouteParameterRegex =
        new(
            @"\{[^/{}]+\}",
            RegexOptions.Compiled |
            RegexOptions.CultureInvariant);

    public void OnGet()
    {
    }

    public async Task<IActionResult> OnPostRunAllAsync(
        CancellationToken cancellationToken)
    {
        var results =
            new List<SystemTestResult>();

        results.Add(
            await RunCheckAsync(
                "database",
                TestDatabaseAsync,
                cancellationToken));

        results.Add(
            await RunCheckAsync(
                "migrations",
                TestMigrationsAsync,
                cancellationToken));

        results.Add(
            await RunCheckAsync(
                "ai-health",
                TestAiHealthAsync,
                cancellationToken));

        results.Add(
            await RunCheckAsync(
                "ai-auth",
                TestAuthenticatedAiChannelAsync,
                cancellationToken));

        await AddRouteChecksAsync(
            results,
            cancellationToken);

        results.Add(
            await RunCheckAsync(
                "review-audit",
                TestReviewAuditAsync,
                cancellationToken));

        return new JsonResult(results);
    }

    private async Task<SystemTestResult> TestDatabaseAsync(
        CancellationToken cancellationToken)
    {
        var canConnect =
            await db.Database.CanConnectAsync(
                cancellationToken);

        if (!canConnect)
        {
            return Error(
                "database",
                "PostgreSQL could not be reached.");
        }

        var userCount =
            await db.Users
                .AsNoTracking()
                .CountAsync(cancellationToken);

        var reviewCount =
            await db.AiOutputReviews
                .AsNoTracking()
                .CountAsync(cancellationToken);

        return Ok(
            "database",
            $"Connected to PostgreSQL. " +
            $"{userCount} user(s) and " +
            $"{reviewCount} AI review record(s) were found.");
    }

    private async Task<SystemTestResult> TestMigrationsAsync(
        CancellationToken cancellationToken)
    {
        var pendingMigrations =
            (await db.Database.GetPendingMigrationsAsync(
                cancellationToken))
            .ToArray();

        if (pendingMigrations.Length == 0)
        {
            return Ok(
                "migrations",
                "Database schema is current. " +
                "No pending EF Core migrations.");
        }

        var shownMigrations =
            string.Join(
                ", ",
                pendingMigrations.Take(5));

        var remainingCount =
            pendingMigrations.Length - 5;

        var additionalText =
            remainingCount > 0
                ? $" and {remainingCount} more"
                : string.Empty;

        return Warning(
            "migrations",
            $"{pendingMigrations.Length} pending migration(s): " +
            $"{shownMigrations}{additionalText}.");
    }

    private async Task<SystemTestResult> TestAiHealthAsync(
        CancellationToken cancellationToken)
    {
        var response =
            await ai.GetHealthAsync()
                .WaitAsync(
                    TimeSpan.FromSeconds(15),
                    cancellationToken);

        if (response is null)
        {
            return Error(
                "ai-health",
                "FastAPI /health returned no valid response.");
        }

        var statusText =
            string.IsNullOrWhiteSpace(response.Status)
                ? "Service responded successfully."
                : response.Status.Trim();

        return Ok(
            "ai-health",
            $"FastAPI is reachable. Status: {statusText}");
    }

    private async Task<SystemTestResult>
        TestAuthenticatedAiChannelAsync(
            CancellationToken cancellationToken)
    {
        /*
         * /health is exempt from authentication.
         *
         * /analyze-skills is deterministic and authenticated, so it safely
         * proves that the configured internal key and JSON request/response
         * channel work without consuming LLM provider quota.
         */
        var response =
            await ai.AnalyzeSkillsAsync(
                    new SkillAnalysisRequest(
                        ["Python", "C#", "SQL"],
                        "intermediate"))
                .WaitAsync(
                    TimeSpan.FromSeconds(20),
                    cancellationToken);

        if (response is null)
        {
            return Error(
                "ai-auth",
                "The authenticated Python request returned no response.");
        }

        return Ok(
            "ai-auth",
            $"Authenticated .NET → FastAPI request succeeded. " +
            $"Score: {response.SkillScore}; " +
            $"recommended level: {response.RecommendedLevel}.");
    }

    private async Task AddRouteChecksAsync(
        ICollection<SystemTestResult> results,
        CancellationToken cancellationToken)
    {
        var stopwatch =
            Stopwatch.StartNew();

        try
        {
            /*
             * One authenticated OpenAPI request supplies both route cards.
             * We do not call Python twice.
             */
            var registeredRoutes =
                await ai.GetRegisteredRoutesAsync(
                    cancellationToken);

            stopwatch.Stop();

            var duration =
                stopwatch.ElapsedMilliseconds;

            results.Add(
                BuildRequiredRouteResult(
                    registeredRoutes)
                with
                {
                    DurationMs = duration
                });

            results.Add(
                BuildLegacyRouteResult(
                    registeredRoutes)
                with
                {
                    DurationMs = duration
                });
        }
        catch (OperationCanceledException)
            when (!cancellationToken.IsCancellationRequested)
        {
            stopwatch.Stop();

            logger.LogWarning(
                "AI route inventory timed out after {ElapsedMs} ms.",
                stopwatch.ElapsedMilliseconds);

            AddRouteErrors(
                results,
                "The AI route inventory check timed out.",
                stopwatch.ElapsedMilliseconds);
        }
        catch (Exception exception)
        {
            stopwatch.Stop();

            logger.LogError(
                exception,
                "AI route inventory failed after {ElapsedMs} ms.",
                stopwatch.ElapsedMilliseconds);

            AddRouteErrors(
                results,
                DescribeException(exception),
                stopwatch.ElapsedMilliseconds);
        }
    }

    private static SystemTestResult BuildRequiredRouteResult(
        IReadOnlySet<string> registeredRoutes)
    {
        var normalizedRegisteredRoutes =
            registeredRoutes
                .Select(NormalizeRequiredRoute)
                .ToHashSet(StringComparer.Ordinal);

        var missingRoutes =
            RequiredAiRoutes
                .Select(NormalizeRequiredRoute)
                .Where(route =>
                    !normalizedRegisteredRoutes.Contains(route))
                .ToArray();

        if (missingRoutes.Length > 0)
        {
            var registeredCount =
                RequiredAiRoutes.Length -
                missingRoutes.Length;

            return Error(
                "ai-routes",
                $"{registeredCount}/{RequiredAiRoutes.Length} " +
                $"required FastAPI routes are registered. " +
                $"Missing: {string.Join(", ", missingRoutes)}.");
        }

        return Ok(
            "ai-routes",
            $"All {RequiredAiRoutes.Length} required " +
            "FastAPI routes are registered.");
    }

    private static SystemTestResult BuildLegacyRouteResult(
        IReadOnlySet<string> registeredRoutes)
    {
        var normalizedRegisteredRoutes =
            registeredRoutes
                .Select(NormalizeRequiredRoute)
                .ToHashSet(StringComparer.Ordinal);

        var registeredLegacyRoutes =
            LegacyAiRoutes
                .Select(NormalizeRequiredRoute)
                .Where(normalizedRegisteredRoutes.Contains)
                .ToArray();

        if (registeredLegacyRoutes.Length > 0)
        {
            return Warning(
                "legacy-routes",
                "Legacy routes are still registered: " +
                string.Join(", ", registeredLegacyRoutes) +
                ". Remove them only after confirming that no current " +
                "application caller depends on them.");
        }

        return Ok(
            "legacy-routes",
            "Legacy feasibility, similarity, market-match, " +
            "and risk-alarm Python routes are absent.");
    }

    private async Task<SystemTestResult> TestReviewAuditAsync(
        CancellationToken cancellationToken)
    {
        var reviews =
            await db.AiOutputReviews
                .AsNoTracking()
                .Where(review =>
                    ReviewAgentNames.Contains(
                        review.AgentName))
                .OrderByDescending(review =>
                    review.CreatedAt)
                .Select(review =>
                    new ReviewEvidence(
                        review.AgentName,
                        review.Status,
                        review.Usable,
                        review.CreatedAt))
                .Take(500)
                .ToListAsync(cancellationToken);

        var latestByAgent =
            reviews
                .GroupBy(
                    review => review.AgentName,
                    StringComparer.Ordinal)
                .ToDictionary(
                    group => group.Key,
                    group => group.First(),
                    StringComparer.Ordinal);

        if (latestByAgent.Count == 0)
        {
            return Warning(
                "review-audit",
                "No persisted AI review evidence was found. " +
                "This does not prove that routes are unavailable; " +
                "it means no reviewed execution is stored in this database.");
        }

        var missingAgents =
            ReviewAgentNames
                .Where(agent =>
                    !latestByAgent.ContainsKey(agent))
                .ToArray();

        var usableCount =
            latestByAgent.Values.Count(review =>
                review.Usable
                && review.Status is
                    "approved"
                    or "approved_with_minor_warnings");

        var newestReview =
            latestByAgent.Values
                .OrderByDescending(review =>
                    review.CreatedAt)
                .First();

        if (missingAgents.Length > 0)
        {
            return Warning(
                "review-audit",
                $"Execution evidence exists for " +
                $"{latestByAgent.Count}/{ReviewAgentNames.Length} " +
                $"registered agent key(s). " +
                $"{usableCount} latest result(s) are usable. " +
                $"No review record was found for: " +
                $"{string.Join(", ", missingAgents)}.");
        }

        return Ok(
            "review-audit",
            $"All {ReviewAgentNames.Length} registered agent keys " +
            $"have persisted execution evidence. " +
            $"{usableCount} latest result(s) are usable. " +
            $"Newest: {newestReview.AgentName} / " +
            $"{newestReview.Status}.");
    }

    private async Task<SystemTestResult> RunCheckAsync(
        string id,
        Func<CancellationToken, Task<SystemTestResult>> check,
        CancellationToken cancellationToken)
    {
        var stopwatch =
            Stopwatch.StartNew();

        try
        {
            var result =
                await check(cancellationToken);

            stopwatch.Stop();

            return result with
            {
                DurationMs =
                    stopwatch.ElapsedMilliseconds
            };
        }
        catch (OperationCanceledException)
            when (!cancellationToken.IsCancellationRequested)
        {
            stopwatch.Stop();

            logger.LogWarning(
                "System test {TestId} timed out after {ElapsedMs} ms.",
                id,
                stopwatch.ElapsedMilliseconds);

            return new SystemTestResult(
                id,
                "error",
                "The check timed out.",
                stopwatch.ElapsedMilliseconds);
        }
        catch (Exception exception)
        {
            stopwatch.Stop();

            logger.LogError(
                exception,
                "System test {TestId} failed after {ElapsedMs} ms.",
                id,
                stopwatch.ElapsedMilliseconds);

            return new SystemTestResult(
                id,
                "error",
                DescribeException(exception),
                stopwatch.ElapsedMilliseconds);
        }
    }

    private static void AddRouteErrors(
        ICollection<SystemTestResult> results,
        string detail,
        long durationMs)
    {
        results.Add(
            new SystemTestResult(
                "ai-routes",
                "error",
                detail,
                durationMs));

        results.Add(
            new SystemTestResult(
                "legacy-routes",
                "error",
                detail,
                durationMs));
    }

    private static string NormalizeRequiredRoute(
        string path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidOperationException(
                "A configured route path was empty.");
        }

        var normalized =
            path.Trim();

        if (!normalized.StartsWith(
                "/",
                StringComparison.Ordinal))
        {
            normalized =
                "/" + normalized;
        }

        if (normalized.Length > 1)
        {
            normalized =
                normalized.TrimEnd('/');
        }

        return RouteParameterRegex.Replace(
            normalized,
            "{}");
    }

    private static string DescribeException(
        Exception exception) =>
        exception switch
        {
            HttpRequestException
            {
                StatusCode:
                    HttpStatusCode.Unauthorized
            } =>
                "FastAPI rejected the internal API key (HTTP 401).",

            HttpRequestException
            {
                StatusCode:
                    HttpStatusCode.Forbidden
            } =>
                "FastAPI rejected access to its route document " +
                "(HTTP 403).",

            HttpRequestException
            {
                StatusCode:
                    HttpStatusCode.NotFound
            } =>
                "FastAPI /ds/openapi.json was not found (HTTP 404).",

            HttpRequestException httpException
                when httpException.StatusCode.HasValue =>
                $"The FastAPI route request failed with HTTP " +
                $"{(int)httpException.StatusCode.Value}.",

            HttpRequestException =>
                "The Python service could not be reached.",

            TimeoutException =>
                "The operation exceeded the allowed test time.",

            JsonException =>
                "FastAPI returned an invalid route document.",

            InvalidOperationException =>
                "FastAPI returned an unexpected route document.",

            _ =>
                "The check failed. See the web server log for details."
        };

    private static SystemTestResult Ok(
        string id,
        string detail) =>
        new(
            id,
            "ok",
            detail,
            0);

    private static SystemTestResult Warning(
        string id,
        string detail) =>
        new(
            id,
            "warning",
            detail,
            0);

    private static SystemTestResult Error(
        string id,
        string detail) =>
        new(
            id,
            "error",
            detail,
            0);

    public sealed record SystemTestResult(
        string Id,
        string Status,
        string Detail,
        long DurationMs);

    private sealed record ReviewEvidence(
        string AgentName,
        string Status,
        bool Usable,
        DateTime CreatedAt);
}