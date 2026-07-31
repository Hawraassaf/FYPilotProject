namespace FYPilot.Domain.Entities;

/// <summary>
/// Optional, admin-authored institutional guidance that FYPilot retrieves
/// and injects as bounded contextual data into ProjectIdeaAgent during idea
/// generation ("Idea Generation Knowledge Base" -- see
/// AdminIdeaContextService). This is contextual retrieval and controlled
/// prompting, never model fine-tuning or training, and Content is treated
/// as untrusted data by the LLM firewall, never as an executable
/// instruction, regardless of who authored it.
/// </summary>
public class IdeaGenerationGuidance
{
    public int Id { get; set; }

    public string Title { get; set; } = string.Empty;
    public string Content { get; set; } = string.Empty;

    /// <summary>General | PreferredTheme | AvoidTheme | InstitutionalRule | TechnicalConstraint | ScopeConstraint</summary>
    public string GuidanceType { get; set; } = "General";

    /// <summary>Null means this guidance applies globally, across every major.</summary>
    public string? Major { get; set; }

    /// <summary>Null means this guidance applies globally, across every domain.</summary>
    public string? Domain { get; set; }

    /// <summary>1 (lowest) to 5 (highest) -- affects selection order, never scores.</summary>
    public int Priority { get; set; } = 3;

    public bool IsActive { get; set; } = true;

    public DateTime? EffectiveFrom { get; set; }
    public DateTime? EffectiveUntil { get; set; }

    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>The admin who authored this entry -- Restrict on delete (audit-trail actor reference, not an owned child).</summary>
    public int? CreatedByUserId { get; set; }
    public User? CreatedByUser { get; set; }
}
