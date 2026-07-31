using FYPilot.Application.DTOs;

namespace FYPilot.Application.Interfaces;

/// <summary>
/// Selects the bounded, relevant slice of the admin-curated Idea Generation
/// Knowledge Base (IdeaGenerationGuidance / HistoricalFypProject /
/// HistoricalFypFutureOpportunity) for one student's generation request.
///
/// This is contextual retrieval and controlled prompting -- FYPilot
/// retrieves admin-curated institutional knowledge and injects the most
/// relevant bounded context into ProjectIdeaAgent during generation. It is
/// NOT model fine-tuning or training, and no permanent model learning is
/// involved.
///
/// Selection is deterministic (active + effective-date + scope match,
/// ranked by relevance/priority/recency, then capped) -- never an
/// embeddings/vector-search lookup. When the knowledge base is empty or
/// nothing matches, callers get back empty lists and the Idea Generator
/// behaves exactly as it does today.
/// </summary>
public interface IAdminIdeaContextService
{
    Task<AdminIdeaGenerationContextDto> GetContextAsync(
        AdminIdeaContextQuery query,
        CancellationToken cancellationToken = default);
}

/// <summary>
/// The student-side signal used to select relevant admin context. Built
/// from whatever the Idea Generator already knows about the student today
/// (Major, PreferredDomain, assessed skill names) -- KeywordTokens is
/// deliberately a flat bag of words (skills, goals, preferred stack) rather
/// than named fields, so it stays useful even as more per-student signals
/// (interests, preferred technologies) get added to the page later without
/// requiring changes here.
/// </summary>
public sealed record AdminIdeaContextQuery(
    string Major,
    string PreferredDomain,
    IReadOnlyList<string> KeywordTokens
);
