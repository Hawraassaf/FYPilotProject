using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Services;

/// <summary>
/// Project.ProjectIdeaId is the authoritative selected idea for a project.
/// ProjectIdea.IsSelected is a legacy per-row flag kept only for backward
/// compatibility with older pages/consumers that have not been migrated to
/// read the FK yet. Every idea-selection entry point (Idea Generator page,
/// Idea Comparison page, and the api/ideas controller) must call this after
/// changing Project.ProjectIdeaId, so the legacy flag never has more than
/// one true row for the same project.
/// </summary>
public static class ProjectIdeaSelectionSync
{
    public static async Task SyncSelectedFlagAsync(
        ApplicationDbContext db,
        int projectId,
        int selectedIdeaId,
        int? previousIdeaId,
        CancellationToken cancellationToken)
    {
        /*
         * Includes previousIdeaId explicitly for legacy ideas whose
         * GeneratedForProjectId was never populated -- otherwise their
         * stale IsSelected=true would never be cleared.
         */
        var relatedIdeas = await db.ProjectIdeas
            .Where(item =>
                item.GeneratedForProjectId == projectId ||
                item.Id == selectedIdeaId ||
                (
                    previousIdeaId.HasValue &&
                    item.Id == previousIdeaId.Value
                ))
            .ToListAsync(cancellationToken);

        foreach (var relatedIdea in relatedIdeas)
        {
            relatedIdea.IsSelected = relatedIdea.Id == selectedIdeaId;
        }
    }
}
