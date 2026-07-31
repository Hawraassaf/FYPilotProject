namespace FYPilot.Domain.Entities;

/// <summary>
/// A previous final year project registered by an admin as part of the
/// Idea Generation Knowledge Base. ExcludeSimilarIdeas and
/// AllowAsInspiration are independent flags -- a project may be exclusion
/// only, inspiration only, both, or neither (historical context only).
/// </summary>
public class HistoricalFypProject
{
    public int Id { get; set; }

    public string Title { get; set; } = string.Empty;
    public string ProblemStatement { get; set; } = string.Empty;

    /// <summary>Null means this record isn't scoped to one major.</summary>
    public string? Major { get; set; }

    /// <summary>Null means this record isn't scoped to one domain.</summary>
    public string? Domain { get; set; }

    public string? TargetUsers { get; set; }

    /// <summary>Comma-separated, matches ProjectIdea.RequiredTechnologies' convention.</summary>
    public string Technologies { get; set; } = string.Empty;

    public int? CompletionYear { get; set; }

    /// <summary>Completed | Rejected | Repeated | Archived | InProgress | Unknown</summary>
    public string ProjectStatus { get; set; } = "Completed";

    /// <summary>Comma-separated keywords used for relevance matching.</summary>
    public string Keywords { get; set; } = string.Empty;

    /// <summary>When true, ProjectIdeaAgent must not return a substantially similar project.</summary>
    public bool ExcludeSimilarIdeas { get; set; }

    /// <summary>When true, this project's TYPE may inform new directions -- it must never be copied or merely renamed.</summary>
    public bool AllowAsInspiration { get; set; }

    public string? ExclusionReason { get; set; }

    public bool IsActive { get; set; } = true;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>The admin who registered this entry -- Restrict on delete (audit-trail actor reference).</summary>
    public int? CreatedByUserId { get; set; }
    public User? CreatedByUser { get; set; }

    public ICollection<HistoricalFypFutureOpportunity> FutureOpportunities { get; set; } = [];
}
