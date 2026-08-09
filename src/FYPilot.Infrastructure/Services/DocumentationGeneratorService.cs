using System.Text.Json;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using FYPilot.Domain.Entities;
using FYPilot.Infrastructure.Data;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace FYPilot.Infrastructure.Services;

public class DocumentationGeneratorService : IDocumentationGeneratorService
{
    // Source values that mean "not real AI output" -- checked as a substring
    // so a future renamed marker (e.g. "static-template-v2") is still caught
    // without needing a code change. Real provider names (deepinfra, groq,
    // gemini, ollama, ...) never contain either word.
    private static readonly string[] FallbackSourceMarkers = ["fallback", "template"];

    // A review status is an application acceptance decision, not display
    // metadata.  Python's usable/displayable fields remain mandatory below,
    // but they can never promote a rejected, unavailable, blocked, invalid,
    // unresolved, or future/unknown status into a persistable student
    // document.  Keep this explicit allowlist aligned with ReviewPipeline's
    // accepted terminal statuses.
    private static readonly HashSet<string> AcceptedReviewStatuses = new(StringComparer.Ordinal)
    {
        "approved",
        "approved_with_minor_warnings",
    };

    // The 6 core sections a real generation must actually produce -- must
    // match app/routers/se_documentation.py's _CORE_SECTION_KEYS exactly
    // (same string contract via SectionProvenance's keys). Any core section
    // using deterministic fallback content is rejected outright; see
    // HasCoreSectionFallback. Not configurable/tiered by design -- the
    // pre-presentation policy is deliberately all-or-nothing.
    private static readonly string[] CoreSectionKeys =
        ["requirements", "useCases", "modulesArchitecture", "database", "uiApi", "testingSecurity"];

    private readonly ApplicationDbContext _db;
    private readonly IAiServiceClient _aiService;
    private readonly ILogger<DocumentationGeneratorService> _logger;

    public DocumentationGeneratorService(
        ApplicationDbContext db,
        IAiServiceClient aiService,
        ILogger<DocumentationGeneratorService> logger)
    {
        _db = db;
        _aiService = aiService;
        _logger = logger;
    }

    /// <summary>
    /// Generates SE documentation via the AI service and, only if the result
    /// passes <see cref="IsRealAiOutput"/>, persists it in a transaction and
    /// returns a success result. Any failure -- network, non-success HTTP
    /// response, deserialization, or a non-usable/non-displayable review --
    /// is logged with full detail (never silently swallowed) and returns a
    /// typed failure result. Nothing is ever written to the database on
    /// failure, so any previously persisted valid document for this idea is
    /// left exactly as it was.
    /// </summary>
    public async Task<SeDocumentationGenerationResult> GenerateAsync(GenerateDocumentationRequest request)
    {
        var correlationId = Guid.NewGuid();

        AiSeDocumentationServiceResponse? aiResponse;

        try
        {
            var aiRequest = await BuildAiRequestAsync(request);
            aiResponse = await _aiService.GenerateSeDocumentationAsync(aiRequest);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogError(
                ex,
                "SE documentation generation failed: AI service returned a non-success response. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=ai_call",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.NonSuccessResponse,
                BuildUserFacingFailureMessage(correlationId));
        }
        catch (TaskCanceledException ex)
        {
            _logger.LogError(
                ex,
                "SE documentation generation failed: AI service call timed out or was canceled. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=ai_call",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.Timeout,
                BuildUserFacingFailureMessage(correlationId));
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("could not be deserialized", StringComparison.OrdinalIgnoreCase))
        {
            // AiServiceClient wraps JsonException as InvalidOperationException
            // with this message -- see AiServiceClient.PostAsync.
            _logger.LogError(
                ex,
                "SE documentation generation failed: AI service response could not be deserialized. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=deserialization",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.DeserializationFailure,
                BuildUserFacingFailureMessage(correlationId));
        }
        catch (JsonException ex)
        {
            _logger.LogError(
                ex,
                "SE documentation generation failed: AI service response JSON was invalid. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=deserialization",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.DeserializationFailure,
                BuildUserFacingFailureMessage(correlationId));
        }
        catch (Exception ex)
        {
            // Last resort only -- still logged loudly at Error with full
            // exception detail and a correlation id, never swallowed. This
            // must never be reached by a known failure mode; if it is, that
            // is itself a bug to investigate from the logs.
            _logger.LogError(
                ex,
                "SE documentation generation failed with an unexpected error. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=unexpected",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.UnexpectedError,
                BuildUserFacingFailureMessage(correlationId));
        }

        if (!IsRealAiOutput(aiResponse, out var rejectReason, out var errorCode))
        {
            _logger.LogWarning(
                "SE documentation generation did not produce usable AI output and was rejected -- " +
                "no document was saved. CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} " +
                "Reason={Reason} LlmUsed={LlmUsed} ReviewStatus={ReviewStatus} ReviewUsable={ReviewUsable} " +
                "Source={Source}",
                correlationId,
                request.ProjectIdeaId,
                rejectReason,
                aiResponse?.LlmUsed,
                aiResponse?.Review?.Status,
                aiResponse?.Review?.Usable,
                aiResponse?.Source);

            return SeDocumentationGenerationResult.Failure(
                errorCode,
                BuildUserFacingFailureMessage(correlationId, errorCode, rejectReason),
                reviewStatus: aiResponse?.Review?.Status,
                warning: aiResponse?.Review?.Warning);
        }

        var review = aiResponse!.Review!;
        var documentation = BuildEntityFromAiDocumentation(request, aiResponse.Documentation!);

        await using var transaction = await _db.Database.BeginTransactionAsync();

        try
        {
            _db.ProjectDocumentations.Add(documentation);

            _db.AiOutputReviews.Add(new AiOutputReview
            {
                ReviewRunId = Guid.TryParse(review.ReviewRunId, out var reviewRunId)
                    ? reviewRunId
                    : Guid.NewGuid(),
                UserId = request.UserId,
                ProjectIdeaId = request.ProjectIdeaId,
                MentorChatSessionId = null,
                AgentName = "SEDocumentationAgent",
                Status = review.Status,
                Usable = review.Usable,
                WasRewritten = review.Attempts > 1,
                Attempts = review.Attempts,
                QualityScore = review.QualityScore,
                DecisionReason = review.DecisionReason,
                GeneratorProvider = aiResponse.Provider,
                GeneratorModel = aiResponse.ModelUsed,
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
                CompletedAt = DateTime.UtcNow
            });

            await _db.SaveChangesAsync();
            await transaction.CommitAsync();
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync();

            _logger.LogError(
                ex,
                "SE documentation generation succeeded but persistence failed; rolled back. " +
                "CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} Stage=persistence",
                correlationId,
                request.ProjectIdeaId);

            return SeDocumentationGenerationResult.Failure(
                SeDocumentationErrorCode.PersistenceFailure,
                BuildUserFacingFailureMessage(correlationId));
        }

        _logger.LogInformation(
            "SE documentation generated and persisted. CorrelationId={CorrelationId} ProjectIdeaId={ProjectIdeaId} " +
            "Provider={Provider} Model={Model} ReviewStatus={ReviewStatus}",
            correlationId,
            request.ProjectIdeaId,
            aiResponse.Provider,
            aiResponse.ModelUsed,
            review.Status);

        return SeDocumentationGenerationResult.Success(
            ToDto(documentation),
            usedLlm: aiResponse.LlmUsed,
            source: aiResponse.Source,
            provider: aiResponse.Provider,
            model: aiResponse.ModelUsed,
            reviewStatus: review.Status,
            outputOrigin: review.OutputOrigin,
            outputReviewLevel: review.OutputReviewLevel,
            warning: string.IsNullOrWhiteSpace(review.Warning) ? null : review.Warning);
    }

    /// <summary>
    /// The single acceptance gate for persisting a generation result. A
    /// result must have reached a real provider, carry parsed documentation,
    /// and be marked usable+displayable by the review pipeline. Its review
    /// status must also be explicitly accepted: only "approved" and
    /// "approved_with_minor_warnings" pass. This independently prevents a
    /// diagnostic or future Python response from making a rejected or
    /// unresolved candidate persistable merely by setting usable/displayable.
    /// </summary>
    private static bool IsRealAiOutput(
        AiSeDocumentationServiceResponse? response,
        out string reason,
        out SeDocumentationErrorCode errorCode)
    {
        if (response is null)
        {
            reason = "AI service returned an empty response body.";
            errorCode = SeDocumentationErrorCode.InvalidResponse;
            return false;
        }

        if (response.WriterBudgetExceeded)
        {
            reason = $"Writer budget exceeded before {string.Join(", ", response.MissingSections ?? [])} " +
                     $"could be attempted (completed: {string.Join(", ", response.CompletedSections ?? [])}).";
            errorCode = SeDocumentationErrorCode.WriterBudgetExceeded;
            return false;
        }

        if (!response.LlmUsed)
        {
            reason = "llmUsed=false -- no real provider produced this output.";
            errorCode = SeDocumentationErrorCode.ProviderUnavailable;
            return false;
        }

        if (response.Documentation is null)
        {
            reason = "documentation field was null despite llmUsed=true.";
            errorCode = SeDocumentationErrorCode.InvalidResponse;
            return false;
        }

        if (response.Review is null)
        {
            reason = "review field was null despite llmUsed=true.";
            errorCode = SeDocumentationErrorCode.InvalidResponse;
            return false;
        }

        if (!AcceptedReviewStatuses.Contains(response.Review.Status))
        {
            reason = $"review status '{response.Review.Status}' is not an accepted SE Documentation outcome.";
            errorCode = SeDocumentationErrorCode.ReviewRejected;
            return false;
        }

        if (!response.Review.Usable || !response.Review.Displayable)
        {
            reason = $"review.usable={response.Review.Usable}, review.displayable={response.Review.Displayable} -- " +
                     $"status={response.Review.Status}.";
            errorCode = SeDocumentationErrorCode.ReviewRejected;
            return false;
        }

        if (IsFallbackSource(response.Source))
        {
            reason = $"source '{response.Source}' is a fallback marker, not a real provider.";
            errorCode = SeDocumentationErrorCode.ProviderUnavailable;
            return false;
        }

        // Strict pre-presentation policy (stabilization task Step C):
        // independently recomputed from the typed per-section provenance,
        // not merely trusted from response.CoreSectionFallback or from
        // llmUsed/source alone -- a real provider response (llmUsed=true,
        // a non-fallback top-level source) must still be rejected if ANY
        // core section actually fell back to deterministic content.
        if (HasCoreSectionFallback(response.Documentation, out var fallbackSections) ||
            response.CoreSectionFallback)
        {
            reason = fallbackSections.Count > 0
                ? $"core section(s) used deterministic fallback content: {string.Join(", ", fallbackSections)}."
                : "the AI service reported coreSectionFallback=true.";
            errorCode = SeDocumentationErrorCode.CoreSectionFallback;
            return false;
        }

        reason = "";
        errorCode = SeDocumentationErrorCode.None;
        return true;
    }

    /// <summary>
    /// True when any of the 6 core sections used deterministic fallback
    /// content, or when the AI technical report is applicable but itself
    /// used fallback content (a document must never describe its AI
    /// technical report as confirmed/complete when it is actually generic
    /// fallback text). Never partial acceptance/tiering -- any one core
    /// fallback section rejects the whole candidate.
    /// </summary>
    private static bool HasCoreSectionFallback(
        AiSeDocumentationDto? documentation,
        out List<string> fallbackCoreSections)
    {
        fallbackCoreSections = [];

        var provenance = documentation?.SectionProvenance;
        if (provenance is null)
        {
            return false;
        }

        foreach (var key in CoreSectionKeys)
        {
            if (provenance.TryGetValue(key, out var origin) &&
                string.Equals(origin, "fallback", StringComparison.OrdinalIgnoreCase))
            {
                fallbackCoreSections.Add(key);
            }
        }

        if (documentation!.AiTechnicalReportApplicable &&
            provenance.TryGetValue("aiReport", out var aiOrigin) &&
            string.Equals(aiOrigin, "fallback", StringComparison.OrdinalIgnoreCase))
        {
            fallbackCoreSections.Add("aiReport");
        }

        return fallbackCoreSections.Count > 0;
    }

    private static bool IsFallbackSource(string? source)
    {
        if (string.IsNullOrWhiteSpace(source))
        {
            return false;
        }

        foreach (var marker in FallbackSourceMarkers)
        {
            if (source.Contains(marker, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static string BuildUserFacingFailureMessage(
        Guid correlationId,
        SeDocumentationErrorCode errorCode = SeDocumentationErrorCode.None,
        string? detail = null)
    {
        var reasonSentence = errorCode switch
        {
            SeDocumentationErrorCode.CoreSectionFallback =>
                "Part of the generated documentation used generic fallback content instead of a real " +
                "AI-generated section, so the new version was not accepted" +
                (string.IsNullOrWhiteSpace(detail) ? ". " : $" ({detail}). "),
            SeDocumentationErrorCode.WriterBudgetExceeded =>
                "Documentation generation ran out of time before every section could be generated" +
                (string.IsNullOrWhiteSpace(detail) ? ". " : $" ({detail}). "),
            _ => "The AI service could not generate the documentation. ",
        };

        return reasonSentence +
            "No fallback document was saved. Your previous documentation remains unchanged. Please try again. " +
            $"(Reference: {correlationId})";
    }

    private async Task<AiSeDocumentationRequest> BuildAiRequestAsync(GenerateDocumentationRequest request)
    {
        var idea = await _db.ProjectIdeas
            .AsNoTracking()
            .FirstOrDefaultAsync(i => i.Id == request.ProjectIdeaId);

        var profile = await _db.StudentProfiles
            .AsNoTracking()
            .FirstOrDefaultAsync(p => p.UserId == request.UserId);

        var skills = await _db.StudentSkills
            .AsNoTracking()
            .Where(s => s.UserId == request.UserId)
            .ToListAsync();

        var roadmap = await _db.ProjectRoadmaps
            .Include(r => r.Phases)
            .AsNoTracking()
            .FirstOrDefaultAsync(r => r.IdeaId == request.ProjectIdeaId && r.UserId == request.UserId);

        var skillNames = skills
            .Select(s => s.SkillName)
            .Where(s => !string.IsNullOrWhiteSpace(s))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

        var skillRatings = skills
            .Where(s => !string.IsNullOrWhiteSpace(s.SkillName))
            .GroupBy(s => s.SkillName.Trim(), StringComparer.OrdinalIgnoreCase)
            .ToDictionary(
                g => g.Key,
                g => Math.Clamp(g.First().Rating, 1, 5),
                StringComparer.OrdinalIgnoreCase);

        var studentProfile = new AiSeDocStudentProfile(
            Major: profile?.Major ?? "Computer Science",
            ExperienceLevel: profile?.ExperienceLevel ?? "intermediate",
            TeamSize: profile?.TeamMembers ?? 1,
            AvailableHoursPerWeek: profile?.AvailableHoursPerWeek ?? 10,
            Skills: skillNames,
            SkillRatings: skillRatings);

        var selectedIdea = new AiSeDocSelectedIdea(
            Id: idea?.Id,
            Title: idea?.Title ?? request.ProjectTitle,
            ProblemStatement: idea?.ProblemStatement ?? request.ProjectDescription,
            TargetUsers: idea?.TargetUsers ?? string.Empty,
            WhyUseful: idea?.WhyUseful ?? string.Empty,
            RequiredTechnologies: idea?.RequiredTechnologies ?? request.TechStack,
            RequiredSkills: idea?.RequiredSkills ?? string.Empty,
            MissingSkills: idea?.MissingSkills ?? string.Empty,
            DifficultyLevel: idea?.DifficultyLevel ?? "medium",
            ExpectedDurationWeeks: idea != null && idea.ExpectedDurationWeeks > 0 ? idea.ExpectedDurationWeeks : 10,
            Domain: idea?.Domain ?? request.Domain,
            FinalDeliverables: idea?.FinalDeliverables ?? string.Empty);

        var roadmapPhases = roadmap?.Phases
            .OrderBy(p => p.PhaseNumber)
            .Select(p => new AiSeDocRoadmapPhase(
                PhaseNumber: p.PhaseNumber,
                Name: p.Name,
                Objective: p.Objective,
                Tasks: DeserializeTasks(p.TasksJson),
                ExpectedOutput: p.ExpectedOutput,
                SuccessCriteria: p.SuccessCriteria,
                IsCompleted: p.IsCompleted))
            .ToList() ?? [];

        return new AiSeDocumentationRequest(
            StudentProfile: studentProfile,
            SelectedIdea: selectedIdea,
            Roadmap: roadmapPhases,
            ExistingNotes: string.Empty);
    }

    private static List<string> DeserializeTasks(string tasksJson)
    {
        try
        {
            return JsonSerializer.Deserialize<List<string>>(tasksJson) ?? [];
        }
        catch
        {
            return [];
        }
    }

    private static ProjectDocumentation BuildEntityFromAiDocumentation(
        GenerateDocumentationRequest request,
        AiSeDocumentationDto doc)
    {
        var functionalRequirements = doc.FunctionalRequirements
            .Select(r => $"{r.Id}: {r.Title} — {r.Description} (Priority: {r.Priority}" +
                (string.IsNullOrWhiteSpace(r.SourceClassification) || r.SourceClassification == "confirmed" ? "" : $"; {r.SourceClassification}") +
                ")" +
                (r.AcceptanceCriteria is { Count: > 0 } ? $" Acceptance: {string.Join(" | ", r.AcceptanceCriteria)}" : ""))
            .ToList();

        var nonFunctionalRequirements = doc.NonFunctionalRequirements
            .Select(r => $"{r.Id}: {r.Title} — {r.Description} (Priority: {r.Priority})" +
                (!string.IsNullOrWhiteSpace(r.MeasurableTarget) ? $" Target: {r.MeasurableTarget}" : "") +
                (!string.IsNullOrWhiteSpace(r.VerificationMethod) ? $" Verified by: {r.VerificationMethod}" : ""))
            .ToList();

        var useCases = doc.UseCases
            .Select(u => $"{u.Id}: {u.Title} — Actor: {u.Actor}. Goal: {u.Goal}." +
                (u.MainFlow is { Count: > 0 } ? $" Main flow: {string.Join(" ", u.MainFlow)}" : ""))
            .ToList();

        var edgeCases = doc.EdgeCases
            .Select(e => $"{e.Id}: {e.Scenario} — {e.ExpectedHandling}" +
                (!string.IsNullOrWhiteSpace(e.Severity) ? $" (Severity: {e.Severity})" : "") +
                (!string.IsNullOrWhiteSpace(e.RecoveryAction) ? $" Recovery: {e.RecoveryAction}" : ""))
            .ToList();

        // Field-level detail -- previously rendered e.ImportantFields (which
        // the LLM never populates, since the prompt only ever asks for the
        // richer e.Fields list), producing "Fields: )" with nothing inside.
        var databaseDesign = doc.DatabaseEntities
            .Select(e =>
            {
                var fieldDetails = (e.Fields is { Count: > 0 })
                    ? string.Join("; ", e.Fields.Select(f =>
                        $"{f.Name} ({f.DataType}{(f.Nullable ? ", nullable" : ", required")}" +
                        (f.IsPrimaryKey ? ", PK" : "") +
                        (f.IsForeignKey ? $", FK->{f.ReferencedEntity}.{f.ReferencedField}" : "") +
                        (f.IsSensitive ? ", sensitive" : "") +
                        ")"))
                    : (e.ImportantFields is { Count: > 0 } ? string.Join(", ", e.ImportantFields) : "(no fields returned)");

                return $"{e.EntityId ?? ""} {e.Name}: {e.Purpose} (PK: {e.PrimaryKey ?? "Id"}" +
                    (e.ForeignKeys is { Count: > 0 } ? $"; FK: {string.Join(", ", e.ForeignKeys)}" : "") +
                    $") Fields: {fieldDetails}" +
                    (e.Indexes is { Count: > 0 } ? $" | Indexes: {string.Join(", ", e.Indexes)}" : "");
            })
            .Concat(doc.EntityRelationships.Select(r =>
                $"{r.FromEntity} → {r.ToEntity} ({r.Type}): {r.Description}"))
            .ToList();

        // Real user-facing screens -- previously this mislabeled backend
        // SystemModules as "UI screens"; the Python agent now returns a
        // genuine UiScreens section describing actual pages, not modules.
        var uiDesign = (doc.UiScreens ?? [])
            .Select(s => $"{s.Name}: {s.Purpose}" +
                (s.AuthorizedRoles is { Count: > 0 } ? $" (Roles: {string.Join(", ", s.AuthorizedRoles)})" : "") +
                (s.MainComponents is { Count: > 0 } ? $" Components: {string.Join(", ", s.MainComponents)}" : ""))
            .ToList();

        var diagramDescriptions =
            $"Entity Relationship Diagram (Mermaid):\n{doc.MermaidERD}\n\n" +
            $"Class Diagram (Mermaid):\n{doc.MermaidClassDiagram}\n\n" +
            $"Activity Diagram (Mermaid) — {request.ProjectTitle}'s main workflow:\n{doc.ActivityDiagram}\n\n" +
            $"Sequence Diagram (Mermaid) — {request.ProjectTitle}'s primary interaction:\n{doc.SequenceDiagram}";

        var architectureBlock =
            $"Style: {doc.Architecture.Style}\n" +
            $"Frontend: {doc.Architecture.Frontend} | Backend: {doc.Architecture.Backend} | Database: {doc.Architecture.Database}\n" +
            $"AI/Service layer: {doc.Architecture.AiService}\n" +
            (doc.Architecture.Components is { Count: > 0 } ? $"Components: {string.Join("; ", doc.Architecture.Components)}\n" : "") +
            (!string.IsNullOrWhiteSpace(doc.Architecture.DataFlow) ? $"Data flow: {doc.Architecture.DataFlow}\n" : "") +
            $"\n{doc.Architecture.Explanation}";

        var moduleBlock = string.Join("\n", doc.SystemModules.Select(m =>
            $"- {m.Name}: {m.Responsibility}" +
            (!string.IsNullOrWhiteSpace(m.FailureBehavior) ? $" (On failure: {m.FailureBehavior})" : "")));

        var apiBlock = (doc.ApiIntegrationPoints is { Count: > 0 })
            ? string.Join("\n", doc.ApiIntegrationPoints.Select(a =>
                $"- {a.Method} {a.Endpoint} — {a.Purpose}" +
                (!string.IsNullOrWhiteSpace(a.SourceClassification) && a.SourceClassification != "confirmed" ? $" [{a.SourceClassification}]" : "")))
            : "No external APIs or third-party integrations were confirmed for this project.";

        var securityBlock = (doc.SecurityAndPrivacy is { Count: > 0 })
            ? string.Join("\n", doc.SecurityAndPrivacy.Select(s => $"- [{s.Category}] {s.Requirement}"))
            : "No security/privacy requirements were generated.";

        var testingBlock = string.Join("\n\n", doc.TestingPlan.Select(t =>
            $"{t.Id} ({t.Type}, {t.Priority ?? "Medium"}{(t.NegativeCase ? ", negative case" : "")}): {t.Title}\n" +
            (t.Preconditions is { Count: > 0 } ? $"  Preconditions: {string.Join("; ", t.Preconditions)}\n" : "") +
            (t.TestData is { Count: > 0 } ? $"  Test data: {string.Join("; ", t.TestData)}\n" : "") +
            (t.Steps is { Count: > 0 } ? $"  Steps: {string.Join(" ", t.Steps)}\n" : "") +
            $"  Expected result: {t.ExpectedResult}\n" +
            (!string.IsNullOrWhiteSpace(t.PassCriteria) ? $"  Pass criteria: {t.PassCriteria}" : "")));

        // Requirement-centric traceability table: one row per functional
        // requirement showing every real use case/module/entity/screen/API/
        // test that references it, plus coverage status -- previously this
        // was an arbitrary positional zip that silently truncated to the
        // shortest section's length (e.g. only FR-01..FR-06 of 10 FRs).
        var traceabilityBlock = string.Join("\n", doc.TraceabilityMatrix.Select(t =>
        {
            string Join(List<string>? ids, string fallback) =>
                (ids is { Count: > 0 }) ? string.Join(",", ids) : fallback;

            return $"{t.RequirementId,-8} | UC: {Join(t.UseCaseIds, "-"),-14} | MOD: {Join(t.ModuleIds, "-"),-10} | " +
                   $"ENT: {Join(t.EntityIds, "-"),-18} | UI: {Join(t.ScreenIds, "-"),-10} | API: {Join(t.ApiIds, "-"),-8} | " +
                   $"TC: {Join(t.TestCaseIds, "-"),-10} | {t.CoverageStatus ?? "covered"}" +
                   (!string.IsNullOrWhiteSpace(t.Notes) ? $" ({t.Notes})" : "");
        }));

        var aiReportBlock = doc.AiTechnicalReportApplicable && doc.AiTechnicalReport is { } ai
            ? $"Task type: {ai.TaskType}\nApproach: {ai.ModelOrApproach}\nTraining vs. inference: {ai.TrainingVsInference}\n" +
              $"Fallback strategy: {ai.FallbackStrategy}\nEvaluation metrics: {string.Join(", ", ai.EvaluationMetrics ?? [])}\n" +
              $"Limitations: {ai.Limitations}"
            : "Not applicable — this project does not include a confirmed or inferred AI/ML/NLP/data-science component.";

        var assumptionsBlock = (doc.Assumptions is { Count: > 0 })
            ? string.Join("\n", doc.Assumptions.Select((a, i) => $"A-{i + 1:D2} [{a.Classification}]: {a.Item}"))
            : "No assumptions were required; all sections were derived from confirmed project information.";

        var qualityBlock = doc.QualityAssessment is { } quality
            ? $"Overall score: {quality.OverallScore}/100 (critical issues: {quality.CriticalIssuesCount})\n" +
              string.Join("\n", quality.CriterionScores.Select(kv => $"- {kv.Key}: {kv.Value}/100")) +
              (quality.CoverageStatistics is { Count: > 0 } ? $"\nCoverage statistics:\n{string.Join("\n", quality.CoverageStatistics.Select(kv => $"- {kv.Key}: {kv.Value}"))}" : "") +
              (quality.FailedChecks is { Count: > 0 } ? $"\nFailed checks:\n{string.Join("\n", quality.FailedChecks.Select(c => $"- {c}"))}" : "") +
              (quality.Warnings is { Count: > 0 } ? $"\nWarnings:\n{string.Join("\n", quality.Warnings.Select(w => $"- {w}"))}" : "")
            : $"Documentation Quality Score: {doc.DocumentationQualityScore}/100";

        // Per-section provenance ("provider" vs "fallback") -- so a partial
        // AI-provider outage is never silently presented as either fully
        // AI-generated or fully generic; see the content-depth batch's
        // "provider failure and partial results" fix.
        var provenanceBlock = (doc.SectionProvenance is { Count: > 0 })
            ? string.Join("\n", doc.SectionProvenance.Select(kv => $"- {kv.Key}: {kv.Value}"))
            : "Not recorded for this generation.";

        var aiTechnicalReport =
            $"1. Architecture\n{architectureBlock}\n\n" +
            $"2. Module & Component Design\n{moduleBlock}\n\n" +
            $"3. API & Integration Specification\n{apiBlock}\n\n" +
            $"4. Security & Privacy\n{securityBlock}\n\n" +
            $"5. Testing Strategy\n{testingBlock}\n\n" +
            $"6. Traceability Matrix (Requirement → Use Case → Module → Entity → Test → Screen)\n{traceabilityBlock}\n\n" +
            $"7. AI / Data Science Technical Report\n{aiReportBlock}\n\n" +
            "8. Risks and Limitations\n" +
            string.Join("\n", doc.RisksAndLimitations.Select(r => $"- {r}")) + "\n\n" +
            "9. Assumptions\n" + assumptionsBlock + "\n\n" +
            "10. Expected Outcomes\n" +
            string.Join("\n", doc.ExpectedOutcomes.Select(o => $"- {o}")) + "\n\n" +
            $"11. Documentation Quality Assessment\n{qualityBlock}\n\n" +
            $"12. Generation Provenance (per section: provider vs. deterministic fallback)\n{provenanceBlock}";

        return new ProjectDocumentation
        {
            UserId = request.UserId,
            ProjectIdeaId = request.ProjectIdeaId,
            Title = $"{request.ProjectTitle} - Software Engineering Specification",

            FunctionalRequirementsJson = JsonSerializer.Serialize(functionalRequirements),
            NonFunctionalRequirementsJson = JsonSerializer.Serialize(nonFunctionalRequirements),
            UseCasesJson = JsonSerializer.Serialize(useCases),
            EdgeCasesJson = JsonSerializer.Serialize(edgeCases),
            DatabaseDesignJson = JsonSerializer.Serialize(databaseDesign),
            UiDesignJson = JsonSerializer.Serialize(uiDesign),
            DiagramDescriptionsJson = JsonSerializer.Serialize(diagramDescriptions),
            AiTechnicalReportJson = JsonSerializer.Serialize(aiTechnicalReport),

            SupervisorStatus = "Draft",
            SupervisorComment = string.Empty,
            CreatedAt = DateTime.UtcNow,
            UpdatedAt = DateTime.UtcNow
        };
    }

    public async Task<GeneratedDocumentationDto?>
    GetLatestForIdeaAsync(
        int projectIdeaId,
        CancellationToken cancellationToken = default)
    {
        var documentation =
            await _db.ProjectDocumentations
                .AsNoTracking()
                .Where(item =>
                    item.ProjectIdeaId == projectIdeaId)
                .OrderByDescending(item =>
                    item.UpdatedAt)
                .ThenByDescending(item =>
                    item.Id)
                .FirstOrDefaultAsync(
                    cancellationToken);

        return documentation == null
            ? null
            : ToDto(documentation);
    }
    public async Task<GeneratedDocumentationDto?> GetByIdAsync(int id)
    {
        var documentation = await _db.ProjectDocumentations
            .AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == id);

        return documentation == null ? null : ToDto(documentation);
    }

    public async Task<List<GeneratedDocumentationDto>> GetByUserIdAsync(int userId)
    {
        var documentations = await _db.ProjectDocumentations
            .AsNoTracking()
            .Where(x => x.UserId == userId)
            .OrderByDescending(x => x.CreatedAt)
            .ToListAsync();

        return documentations.Select(ToDto).ToList();
    }

    public async Task<List<GeneratedDocumentationDto>> GetAllForSupervisorAsync()
    {
        var documentations = await _db.ProjectDocumentations
            .AsNoTracking()
            .OrderByDescending(x => x.CreatedAt)
            .ToListAsync();

        return documentations.Select(ToDto).ToList();
    }

    public async Task AddSupervisorFeedbackAsync(int documentationId, string status, string comment)
    {
        var documentation = await _db.ProjectDocumentations
            .FirstOrDefaultAsync(x => x.Id == documentationId);

        if (documentation == null)
        {
            return;
        }

        documentation.SupervisorStatus = string.IsNullOrWhiteSpace(status) ? "Needs Revision" : status;
        documentation.SupervisorComment = comment ?? string.Empty;
        documentation.UpdatedAt = DateTime.UtcNow;

        await _db.SaveChangesAsync();
    }

    private static GeneratedDocumentationDto ToDto(ProjectDocumentation documentation)
    {
        return new GeneratedDocumentationDto
        {
            Id = documentation.Id,
            Title = documentation.Title,
            FunctionalRequirements = DeserializeList(documentation.FunctionalRequirementsJson),
            NonFunctionalRequirements = DeserializeList(documentation.NonFunctionalRequirementsJson),
            UseCases = DeserializeList(documentation.UseCasesJson),
            EdgeCases = DeserializeList(documentation.EdgeCasesJson),
            DatabaseDesign = DeserializeList(documentation.DatabaseDesignJson),
            UiDesign = DeserializeList(documentation.UiDesignJson),
            DiagramDescriptions = DeserializeString(documentation.DiagramDescriptionsJson),
            AiTechnicalReport = DeserializeString(documentation.AiTechnicalReportJson),
            SupervisorStatus = documentation.SupervisorStatus,
            SupervisorComment = documentation.SupervisorComment,
            CreatedAt = documentation.CreatedAt
        };
    }

    private static List<string> DeserializeList(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<List<string>>(json) ?? new List<string>();
        }
        catch
        {
            return new List<string>();
        }
    }

    private static string DeserializeString(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<string>(json) ?? string.Empty;
        }
        catch
        {
            return json;
        }
    }
}
