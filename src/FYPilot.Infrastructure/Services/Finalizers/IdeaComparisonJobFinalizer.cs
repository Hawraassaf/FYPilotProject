using System.Text.Json;
using FYPilot.Application.DTOs;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace FYPilot.Infrastructure.Services.Finalizers;

/// <summary>
/// Persists the output of one Idea Comparison job: a single AiOutputReview
/// row, correlated to the job via JobId (unique index -- see
/// ApplicationDbContext). This is a verbatim extraction of
/// IdeaComparisonModel.PersistReviewAsync's logic, unchanged, so a
/// job-driven comparison is persisted identically to the legacy synchronous
/// flow.
///
/// Note what this finalizer deliberately does NOT do: it does not attempt
/// to reconstruct or merge the AI ranking (ComparedIdeaDto per idea) onto
/// any domain row, because IdeaComparisonDto's per-idea ranking (rank,
/// whyThisRank, mainStrength, ...) has never been domain-persisted --
/// today's synchronous OnPostCompareAsync only ever holds it in-memory for
/// one render (ApplyAiComparison). AiAgentJob.ResultJson is what makes this
/// output durable now; IdeaComparison.cshtml.cs's OnGetAsync (Phase 7)
/// reconstructs the same in-memory view from ResultJson via
/// ReconstructComparisonResponse below, rather than this finalizer
/// inventing a new place to store it.
/// </summary>
public class IdeaComparisonJobFinalizer(ApplicationDbContext db) : IAiAgentJobFinalizer
{
    public string AgentName => "IdeaComparisonAgent";

    /// <summary>Python's ai_jobs result payloads are camelCase; case-insensitive matching binds them onto these PascalCase records without needing [JsonPropertyName] everywhere (same approach AiServiceClient already uses).</summary>
    private static readonly JsonSerializerOptions PythonResultJsonOptions = new() { PropertyNameCaseInsensitive = true };

    public static IdeaComparisonServiceResponse? ReconstructComparisonResponse(string? resultJson)
    {
        if (string.IsNullOrWhiteSpace(resultJson))
        {
            return null;
        }

        try
        {
            return JsonSerializer.Deserialize<IdeaComparisonServiceResponse>(resultJson, PythonResultJsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public async Task FinalizeAsync(AiAgentJob job, JsonElement resultPayload, CancellationToken ct)
    {
        var alreadyPersisted = await db.AiOutputReviews
            .AsNoTracking()
            .AnyAsync(r => r.JobId == job.JobId, ct);

        // Explicit transaction (rather than relying on one implicit
        // SaveChangesAsync transaction) because the review insert uses
        // tracked SaveChangesAsync while the AiAgentJob update below
        // deliberately uses ExecuteUpdateAsync -- never a tracked read of
        // the job row, since the coordinator's own flow touches the same
        // row via bulk ExecuteUpdateAsync calls (lease renewal, claim)
        // that bump Postgres's xmin outside this DbContext's change
        // tracker; mixing that with a tracked-entity SaveChangesAsync on
        // the same row produced a real DbUpdateConcurrencyException in
        // testing. Wrapping both statements in one explicit transaction
        // keeps them atomic without needing a tracked entity at all.
        await using var transaction = await db.Database.BeginTransactionAsync(ct);

        if (!alreadyPersisted)
        {
            var response = JsonSerializer.Deserialize<IdeaComparisonServiceResponse>(resultPayload.GetRawText(), PythonResultJsonOptions);
            var review = response?.Review;

            if (review != null)
            {
                db.AiOutputReviews.Add(new AiOutputReview
                {
                    JobId = job.JobId,
                    ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId) ? reviewRunId : Guid.NewGuid(),
                    UserId = job.UserId,
                    ProjectIdeaId = null,
                    MentorChatSessionId = null,
                    AgentName = AgentName,
                    Status = review.Status,
                    Usable = review.Usable,
                    WasRewritten = review.Attempts > 1,
                    Attempts = review.Attempts,
                    QualityScore = review.QualityScore,
                    DecisionReason = review.DecisionReason,
                    GeneratorProvider = response!.Provider,
                    GeneratorModel = response.ModelUsed,
                    ReviewerProvider = review.ReviewerProvider,
                    ReviewerModel = review.ReviewerModel,
                    FirewallStatus = review.Status == "firewall_blocked" ? "blocked" : "passed",
                    FirewallInputFlagsJson = JsonSerializer.Serialize(review.FirewallInputFlags ?? []),
                    FirewallOutputFlagsJson = JsonSerializer.Serialize(review.FirewallOutputFlags ?? []),
                    IssuesJson = JsonSerializer.Serialize(review.Issues),
                    StrengthsJson = JsonSerializer.Serialize(review.Strengths),
                    AttemptHistoryJson = JsonSerializer.Serialize(review.AttemptHistory ?? []),
                    ReviewerVersion = review.ReviewerVersion,
                    CreatedAt = DateTime.UtcNow,
                    CompletedAt = DateTime.UtcNow,
                });

                await db.SaveChangesAsync(ct);
            }
        }

        // AiAgentJobCoordinator's crash-recovery pass relies on
        // ResultPersistedAtUtc to know persistence already happened even
        // if it never gets to call CompleteJobAsync.
        await db.AiAgentJobs
            .Where(j => j.JobId == job.JobId)
            .ExecuteUpdateAsync(s => s.SetProperty(j => j.ResultPersistedAtUtc, DateTime.UtcNow), ct);

        await transaction.CommitAsync(ct);
    }

    public async Task<object> BuildSafeResultViewAsync(AiAgentJob job, CancellationToken ct)
    {
        // Idea Comparison is a reload-style page (Phase 7) -- its browser
        // never calls GET /api/ai-agent-jobs/{jobId}/result, so this exists
        // only for interface completeness. Still returns something honest
        // rather than throwing, in case a future caller needs a summary.
        var review = await db.AiOutputReviews
            .AsNoTracking()
            .Where(r => r.JobId == job.JobId)
            .Select(r => new { r.Status, r.Usable, r.QualityScore })
            .FirstOrDefaultAsync(ct);

        return new { status = review?.Status, usable = review?.Usable, qualityScore = review?.QualityScore };
    }
}
