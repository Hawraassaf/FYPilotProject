namespace FYPilot.Domain.Entities;

/// <summary>
/// An unfinished extension, research gap, or future opportunity derived
/// from a <see cref="HistoricalFypProject"/>. Future opportunities are
/// positive inspiration only, never mandatory output templates -- a
/// generated idea that merely renames one is still a duplicate and must be
/// rejected by the same exclusion logic as an excluded historical project.
/// </summary>
public class HistoricalFypFutureOpportunity
{
    public int Id { get; set; }

    public int HistoricalFypProjectId { get; set; }

    public string Title { get; set; } = string.Empty;
    public string Description { get; set; } = string.Empty;

    public string? SuggestedDomain { get; set; }

    /// <summary>Comma-separated, matches HistoricalFypProject.Technologies' convention.</summary>
    public string SuggestedTechnologies { get; set; } = string.Empty;

    public string? ResearchGap { get; set; }

    /// <summary>1 (lowest) to 5 (highest) -- affects selection order, never scores.</summary>
    public int Priority { get; set; } = 3;

    public bool IsActive { get; set; } = true;

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>The admin who authored this entry -- Restrict on delete (audit-trail actor reference).</summary>
    public int? CreatedByUserId { get; set; }
    public User? CreatedByUser { get; set; }

    public HistoricalFypProject HistoricalFypProject { get; set; } = null!;
}
