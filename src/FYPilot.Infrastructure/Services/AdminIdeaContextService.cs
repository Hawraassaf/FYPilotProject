using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// Deterministic, capped selection of the Idea Generation Knowledge Base --
/// see IAdminIdeaContextService for the "contextual retrieval, not model
/// training" framing. No embeddings, no vector search: candidates are
/// filtered by active/effective-date/scope, ranked by scope-match strength
/// + keyword overlap + priority + recency, then capped.
/// </summary>
public sealed class AdminIdeaContextService(
    ApplicationDbContext db,
    ILogger<AdminIdeaContextService> logger) : IAdminIdeaContextService
{
    private const int MaxGuidanceItems = 5;
    private const int MaxExcludedProjects = 6;
    private const int MaxHistoricalProjectsForContext = 10;
    private const int MaxFutureOpportunities = 5;

    public async Task<AdminIdeaGenerationContextDto> GetContextAsync(
        AdminIdeaContextQuery query,
        CancellationToken cancellationToken = default)
    {
        var empty = new AdminIdeaGenerationContextDto([], [], [], []);

        try
        {
            var now = DateTime.UtcNow;
            var keywordTokens = Tokenize(query.KeywordTokens);

            var guidance = await BuildGuidanceContextAsync(query, now, keywordTokens, cancellationToken);

            var activeProjects = await db.HistoricalFypProjects
                .AsNoTracking()
                .Where(p => p.IsActive)
                .ToListAsync(cancellationToken);

            var scopedProjects = activeProjects
                .Where(p => MatchesScope(p.Major, p.Domain, query.Major, query.PreferredDomain))
                .Select(p => (Project: p, Score: RelevanceScore(
                    p.Major, p.Domain, query.Major, query.PreferredDomain,
                    Tokenize($"{p.Keywords} {p.Technologies}"), keywordTokens, priority: 0)))
                .OrderByDescending(x => x.Score)
                .ThenByDescending(x => x.Project.UpdatedAt)
                .ToList();

            var previousProjectsToAvoid = scopedProjects
                .Where(x => x.Project.ExcludeSimilarIdeas)
                .Take(MaxExcludedProjects)
                .Select(x => ToHistoricalProjectContext(x.Project))
                .ToList();

            var historicalProjectsForContext = scopedProjects
                .Take(MaxHistoricalProjectsForContext)
                .Select(x => ToHistoricalProjectContext(x.Project))
                .ToList();

            var activeProjectIds = activeProjects.Select(p => p.Id).ToHashSet();

            var futureOpportunities = await db.HistoricalFypFutureOpportunities
                .AsNoTracking()
                .Include(o => o.HistoricalFypProject)
                .Where(o => o.IsActive && activeProjectIds.Contains(o.HistoricalFypProjectId))
                .ToListAsync(cancellationToken);

            var scopedOpportunities = futureOpportunities
                .Where(o => MatchesScope(
                    o.SuggestedDomain ?? o.HistoricalFypProject.Domain,
                    o.SuggestedDomain ?? o.HistoricalFypProject.Domain,
                    query.Major,
                    query.PreferredDomain))
                .Select(o => (Opportunity: o, Score: RelevanceScore(
                    null, o.SuggestedDomain ?? o.HistoricalFypProject.Domain, query.Major, query.PreferredDomain,
                    Tokenize($"{o.Description} {o.ResearchGap} {o.SuggestedTechnologies}"), keywordTokens,
                    priority: o.Priority)))
                .OrderByDescending(x => x.Score)
                .ThenByDescending(x => x.Opportunity.UpdatedAt)
                .Take(MaxFutureOpportunities)
                .Select(x => new FutureOpportunityContextDto(
                    x.Opportunity.Title,
                    x.Opportunity.Description,
                    x.Opportunity.SuggestedDomain,
                    x.Opportunity.SuggestedTechnologies,
                    x.Opportunity.ResearchGap,
                    x.Opportunity.HistoricalFypProject.Title))
                .ToList();

            return new AdminIdeaGenerationContextDto(
                guidance,
                previousProjectsToAvoid,
                historicalProjectsForContext,
                scopedOpportunities);
        }
        catch (Exception ex)
        {
            // Admin context is an enhancement, never a hard requirement --
            // a retrieval failure must never fail the student's request.
            logger.LogWarning(ex, "Admin idea context retrieval failed; continuing without it.");
            return empty;
        }
    }

    private async Task<List<AdminGuidanceContextDto>> BuildGuidanceContextAsync(
        AdminIdeaContextQuery query,
        DateTime now,
        HashSet<string> keywordTokens,
        CancellationToken cancellationToken)
    {
        var active = await db.IdeaGenerationGuidances
            .AsNoTracking()
            .Where(g => g.IsActive)
            .Where(g => g.EffectiveFrom == null || g.EffectiveFrom <= now)
            .Where(g => g.EffectiveUntil == null || g.EffectiveUntil >= now)
            .ToListAsync(cancellationToken);

        return active
            .Where(g => MatchesScope(g.Major, g.Domain, query.Major, query.PreferredDomain))
            .Select(g => (Guidance: g, Score: RelevanceScore(
                g.Major, g.Domain, query.Major, query.PreferredDomain,
                Tokenize(g.Content), keywordTokens, priority: g.Priority)))
            .OrderByDescending(x => x.Score)
            .ThenByDescending(x => x.Guidance.UpdatedAt)
            .Take(MaxGuidanceItems)
            .Select(x => new AdminGuidanceContextDto(x.Guidance.Title, x.Guidance.Content, x.Guidance.GuidanceType))
            .ToList();
    }

    private static HistoricalProjectContextDto ToHistoricalProjectContext(HistoricalFypProject project) =>
        new(project.Title, project.ProblemStatement, project.Domain, project.Technologies);

    /// <summary>
    /// A record qualifies when its scope constraints (if any) are satisfied:
    /// a null Major/Domain is a wildcard; a set Major/Domain must match the
    /// student's (case-insensitive) exactly. This is what makes a record
    /// "global" (both null) or scoped to one major/domain -- keyword
    /// overlap is a ranking signal, not an inclusion gate.
    /// </summary>
    private static bool MatchesScope(string? recordMajor, string? recordDomain, string studentMajor, string studentDomain)
    {
        var majorOk = string.IsNullOrWhiteSpace(recordMajor)
            || string.Equals(recordMajor.Trim(), studentMajor.Trim(), StringComparison.OrdinalIgnoreCase);

        var domainOk = string.IsNullOrWhiteSpace(recordDomain)
            || string.Equals(recordDomain.Trim(), studentDomain.Trim(), StringComparison.OrdinalIgnoreCase);

        return majorOk && domainOk;
    }

    private static double RelevanceScore(
        string? recordMajor,
        string? recordDomain,
        string studentMajor,
        string studentDomain,
        HashSet<string> recordTokens,
        HashSet<string> studentTokens,
        int priority)
    {
        var score = 0.0;

        if (!string.IsNullOrWhiteSpace(recordMajor)
            && string.Equals(recordMajor.Trim(), studentMajor.Trim(), StringComparison.OrdinalIgnoreCase))
        {
            score += 3;
        }

        if (!string.IsNullOrWhiteSpace(recordDomain)
            && string.Equals(recordDomain.Trim(), studentDomain.Trim(), StringComparison.OrdinalIgnoreCase))
        {
            score += 3;
        }

        if (string.IsNullOrWhiteSpace(recordMajor) && string.IsNullOrWhiteSpace(recordDomain))
        {
            // Global records still deserve some baseline relevance so they
            // aren't always outranked by a merely keyword-matching one.
            score += 1;
        }

        if (studentTokens.Count > 0 && recordTokens.Count > 0)
        {
            var overlap = recordTokens.Intersect(studentTokens).Count();
            score += overlap * 2.0 / Math.Max(recordTokens.Count, studentTokens.Count);
        }

        score += Math.Clamp(priority, 0, 5) * 0.5;

        return score;
    }

    private static HashSet<string> Tokenize(string? text) => Tokenize(
        string.IsNullOrWhiteSpace(text) ? [] : text.Split([' ', ',', ';', '\t', '\n'], StringSplitOptions.RemoveEmptyEntries));

    private static HashSet<string> Tokenize(IEnumerable<string> words) =>
        words
            .SelectMany(word => word.Split([' ', ',', ';', '\t', '\n'], StringSplitOptions.RemoveEmptyEntries))
            .Select(word => word.Trim().ToLowerInvariant())
            .Where(word => word.Length > 2)
            .ToHashSet();
}
