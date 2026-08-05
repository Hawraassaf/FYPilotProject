using FYPilot.Application.DTOs;
using FYPilot.Application.DTOs.Documentation;
using FYPilot.Application.Interfaces;
using FYPilot.Infrastructure.Data;
using FYPilot.Infrastructure.Services;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using Microsoft.Extensions.Logging.Abstractions;
using Moq;
using Xunit;

namespace FYPilot.Tests.Documentation;

/// <summary>
/// Covers DocumentationGeneratorService.GenerateAsync's acceptance policy,
/// error handling, and persistence behavior -- the core of the SE
/// Documentation reliability fix. No live network/provider calls: the AI
/// service is mocked so every test is deterministic and free.
/// </summary>
public class DocumentationGeneratorServiceTests
{
    /// <summary>
    /// The InMemory provider doesn't support real transactions and throws on
    /// BeginTransactionAsync by default; production runs on Npgsql, which
    /// fully supports them, so this suppression is test-only plumbing, not a
    /// behavior change under test.
    /// </summary>
    private static DbContextOptions<ApplicationDbContext> NewDbOptions(string? name = null) =>
        new DbContextOptionsBuilder<ApplicationDbContext>()
            .UseInMemoryDatabase(name ?? Guid.NewGuid().ToString())
            .ConfigureWarnings(w => w.Ignore(InMemoryEventId.TransactionIgnoredWarning))
            .Options;

    private static ApplicationDbContext NewDb(string? name = null) => new(NewDbOptions(name));

    private static GenerateDocumentationRequest NewRequest() => new()
    {
        UserId = 1,
        ProjectIdeaId = 42,
        ProjectTitle = "Arabic Medical Symptom Triage Assistant",
        ProjectDescription = "Helps patients triage symptoms.",
        Domain = "Healthcare Technology",
        TechStack = "ASP.NET Core, FastAPI, PyTorch",
        ContainsAi = true,
    };

    /// <summary>A minimally valid AiSeDocumentationDto -- every required list/scalar filled so BuildEntityFromAiDocumentation never null-refs.</summary>
    private static AiSeDocumentationDto MinimalValidDocumentation() => new(
        ProjectTitle: "Arabic Medical Symptom Triage Assistant",
        ProjectOverview: "Overview.",
        ProblemStatement: "Problem.",
        Objectives: ["Objective 1"],
        Stakeholders: ["Patients"],
        Scope: new AiSeDocScope(InScope: ["Triage"], OutOfScope: ["Billing"], FutureWork: []),
        FunctionalRequirements: [new AiSeDocRequirement("FR-01", "Title", "Description", "High", "Selected idea")],
        NonFunctionalRequirements: [new AiSeDocRequirement("NFR-01", "Title", "Description", "High", "Software quality")],
        UseCases: [new AiSeDocUseCase("UC-01", "Title", "Patient", "Goal", [], ["1. Do it"], [], [], [])],
        EdgeCases: [new AiSeDocEdgeCase("EC-01", "Scenario", "Handling", "FR-01")],
        SystemModules: [new AiSeDocModule("MOD-01", "Module", "Responsibility", [], [], [])],
        DatabaseEntities: [new AiSeDocEntity(Name: "Entity", Purpose: "Purpose", ImportantFields: [], Relationships: [], EntityId: "ENT-01")],
        EntityRelationships: [],
        MermaidERD: "erDiagram",
        MermaidClassDiagram: "classDiagram",
        ActivityDiagram: "flowchart TD",
        SequenceDiagram: "sequenceDiagram",
        Architecture: new AiSeDocArchitecture("Layered", "Razor Pages", "FastAPI", "PostgreSQL", "FastAPI", [], "Explanation."),
        ApiIntegrationPoints: [],
        TestingPlan: [new AiSeDocTestCase("TC-01", "Test", "Unit", ["1. Step"], "Result", ["FR-01"])],
        TraceabilityMatrix: [],
        RisksAndLimitations: [],
        ExpectedOutcomes: [],
        DocumentationQualityScore: 80,
        ConsistencyWarnings: []);

    private static AiQualityPassportDto NewReview(
        bool usable,
        bool displayable,
        string status,
        string warning = "",
        int attempts = 1) => new(
        Status: status,
        Usable: usable,
        ReviewUnavailable: status == "review_unavailable",
        Warning: warning,
        QualityScore: 80,
        Strengths: [],
        Issues: [],
        DecisionReason: "test",
        Attempts: attempts,
        ReviewerVersion: "review-pipeline-v2",
        ReviewRunId: Guid.NewGuid().ToString(),
        Displayable: displayable,
        OutputOrigin: usable ? "writer" : "none",
        OutputReviewLevel: usable ? (status == "review_unavailable" ? "structural_only" : "approved") : "none");

    private static (Mock<IAiServiceClient> Mock, ApplicationDbContext Db, DocumentationGeneratorService Service) NewSut()
    {
        var db = NewDb();
        var aiMock = new Mock<IAiServiceClient>();
        var service = new DocumentationGeneratorService(db, aiMock.Object, NullLogger<DocumentationGeneratorService>.Instance);
        return (aiMock, db, service);
    }

    // 1. Real AI output is persisted.
    [Fact]
    public async Task RealAiOutput_IsPersisted()
    {
        var (aiMock, db, service) = NewSut();
        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "deepinfra", "deepinfra",
            "anthropic/claude-opus-4-8", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "approved"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.True(result.Succeeded);
        Assert.True(result.Persisted);
        Assert.NotNull(result.Documentation);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());
        Assert.Equal(1, await db.AiOutputReviews.CountAsync());
    }

    // 2. AI output with review_unavailable, usable=true, displayable=true is persisted with a warning.
    [Fact]
    public async Task ReviewUnavailable_ButUsableAndDisplayable_IsPersistedWithWarning()
    {
        var (aiMock, db, service) = NewSut();
        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "deepinfra", "deepinfra",
            "anthropic/claude-opus-4-8", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "review_unavailable", warning: "Semantic review unavailable."));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.True(result.Succeeded);
        Assert.True(result.Persisted);
        Assert.Equal("review_unavailable", result.ReviewStatus);
        Assert.False(string.IsNullOrWhiteSpace(result.Warning));
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());
    }

    // 3. Python fallback with llmUsed=false is rejected.
    [Fact]
    public async Task LlmUsedFalse_IsRejected_NoPersistence()
    {
        var (aiMock, db, service) = NewSut();
        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", false, "dynamic-fallback", null,
            null, "No AI provider produced an initial answer.", null, DateTime.UtcNow, "fallback",
            NewReview(usable: false, displayable: false, status: "provider_unavailable"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.False(result.Persisted);
        Assert.Null(result.Documentation);
        Assert.Contains("No fallback document was saved", result.UserMessage);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 4. source=dynamic-fallback is rejected even if llmUsed somehow true (defense in depth).
    [Fact]
    public async Task FallbackSource_IsRejected_EvenIfLlmUsedTrue()
    {
        var (aiMock, db, service) = NewSut();
        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "dynamic-fallback", null,
            null, null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "approved"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 5. HTTP exception does not create fallback documentation.
    [Fact]
    public async Task HttpRequestException_ProducesTypedFailure_NoPersistence()
    {
        var (aiMock, db, service) = NewSut();
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>()))
            .ThrowsAsync(new HttpRequestException("AI service call failed. Status: 401. Response: {\"detail\":\"Missing X-Internal-Api-Key header.\"}"));

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.NonSuccessResponse, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 6. JSON deserialization failure does not create fallback documentation.
    [Fact]
    public async Task DeserializationFailure_ProducesTypedFailure_NoPersistence()
    {
        var (aiMock, db, service) = NewSut();
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>()))
            .ThrowsAsync(new InvalidOperationException("AI service response could not be deserialized. URL: x. Response: y. Error: z"));

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.DeserializationFailure, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 7. Empty/null response is rejected.
    [Fact]
    public async Task NullResponse_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>()))
            .ReturnsAsync((AiSeDocumentationServiceResponse?)null);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.InvalidResponse, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 8. usable=false is rejected even when llmUsed=true and documentation is present.
    [Fact]
    public async Task UsableFalse_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "deepinfra", "deepinfra",
            "anthropic/claude-opus-4-8", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: false, displayable: false, status: "rejected"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.ReviewRejected, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 9. Existing valid documentation remains unchanged after failed regeneration.
    [Fact]
    public async Task FailedRegeneration_LeavesExistingDocumentUntouched()
    {
        var (aiMock, db, service) = NewSut();
        var request = NewRequest();

        // First: a real success, persisted.
        var successResponse = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "deepinfra", "deepinfra",
            "anthropic/claude-opus-4-8", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "approved"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(successResponse);
        var firstResult = await service.GenerateAsync(request);
        Assert.True(firstResult.Succeeded);
        var existingId = firstResult.Documentation!.Id;

        // Second: a failed regeneration.
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>()))
            .ThrowsAsync(new HttpRequestException("boom"));
        var secondResult = await service.GenerateAsync(request);

        Assert.False(secondResult.Succeeded);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());
        var stillThere = await db.ProjectDocumentations.FindAsync(existingId);
        Assert.NotNull(stillThere);

        var preserved = await service.GetLatestForIdeaAsync(request.ProjectIdeaId);
        Assert.NotNull(preserved);
        Assert.Equal(existingId, preserved!.Id);
    }

    // 10. Database failure during persistence does not leave a partially replaced document.
    [Fact]
    public async Task PersistenceFailure_RollsBackAndReturnsTypedFailure()
    {
        var db = new ThrowingOnSaveDbContext(NewDbOptions());
        var aiMock = new Mock<IAiServiceClient>();
        var service = new DocumentationGeneratorService(db, aiMock.Object, NullLogger<DocumentationGeneratorService>.Instance);

        var response = new AiSeDocumentationServiceResponse(
            MinimalValidDocumentation(), "SEDocumentationAgent", true, "deepinfra", "deepinfra",
            "anthropic/claude-opus-4-8", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "approved"));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.PersistenceFailure, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    /// <summary>Test double: simulates a DB write failure inside the transaction.</summary>
    private sealed class ThrowingOnSaveDbContext(DbContextOptions<ApplicationDbContext> options) : ApplicationDbContext(options)
    {
        public override Task<int> SaveChangesAsync(CancellationToken cancellationToken = default) =>
            throw new DbUpdateException("Simulated database failure.");
    }

    // ── Stabilization task Step C: strict core-section fallback rejection ──

    private static Dictionary<string, string> AllCoreSectionsReal() => new()
    {
        ["requirements"] = "provider",
        ["useCases"] = "provider",
        ["modulesArchitecture"] = "provider",
        ["database"] = "provider",
        ["uiApi"] = "provider",
        ["testingSecurity"] = "provider",
    };

    private static AiSeDocumentationDto DocumentationWithProvenance(
        Dictionary<string, string> provenance,
        bool aiApplicable = false) =>
        MinimalValidDocumentation() with
        {
            SectionProvenance = provenance,
            AiTechnicalReportApplicable = aiApplicable,
        };

    private static AiSeDocumentationServiceResponse ResponseWith(
        AiSeDocumentationDto documentation,
        bool llmUsed = true,
        string source = "deepinfra",
        bool coreSectionFallback = false) =>
        new(
            documentation, "SEDocumentationAgent", llmUsed, source, "deepinfra",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo", null, null, DateTime.UtcNow, "ok",
            NewReview(usable: true, displayable: true, status: "approved"),
            CoreSectionFallback: coreSectionFallback);

    // 11. Zero fallback core sections -- accepted.
    [Fact]
    public async Task ZeroFallbackCoreSections_IsAccepted()
    {
        var (aiMock, db, service) = NewSut();
        var response = ResponseWith(DocumentationWithProvenance(AllCoreSectionsReal()));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.True(result.Succeeded);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());
    }

    // 12. requirements fallback -- rejected.
    [Fact]
    public async Task RequirementsFallback_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["requirements"] = "fallback";
        var response = ResponseWith(DocumentationWithProvenance(provenance));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, result.ErrorCode);
        Assert.Contains("requirements", result.UserMessage, StringComparison.OrdinalIgnoreCase);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 13. testingSecurity fallback -- rejected.
    [Fact]
    public async Task TestingSecurityFallback_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["testingSecurity"] = "fallback";
        var response = ResponseWith(DocumentationWithProvenance(provenance));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 14. Two fallback core sections -- rejected (not partially accepted).
    [Fact]
    public async Task TwoFallbackCoreSections_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["database"] = "fallback";
        provenance["uiApi"] = "fallback";
        var response = ResponseWith(DocumentationWithProvenance(provenance));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 15. Fallback only in a NON-applicable AI report -- explicitly accepted
    // (aiReport is not a core section when aiTechnicalReportApplicable is
    // false; this is the policy decision documented in
    // DocumentationGeneratorService.HasCoreSectionFallback).
    [Fact]
    public async Task FallbackInNonApplicableAiReport_IsAccepted()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["aiReport"] = "fallback";
        var response = ResponseWith(DocumentationWithProvenance(provenance, aiApplicable: false));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.True(result.Succeeded);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());
    }

    // 15b. Fallback in an APPLICABLE AI report -- rejected (the documented
    // decision: a document must never describe its AI technical report as
    // confirmed/complete when it is actually generic fallback text).
    [Fact]
    public async Task FallbackInApplicableAiReport_IsRejected()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["aiReport"] = "fallback";
        var response = ResponseWith(DocumentationWithProvenance(provenance, aiApplicable: true));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 6/7 (Step C list). A core-fallback rejection leaves the previously
    // accepted document unchanged and is never exported/persisted as current.
    [Fact]
    public async Task CoreSectionFallback_LeavesPreviousValidDocumentUnchangedAndIsNotPersisted()
    {
        var (aiMock, db, service) = NewSut();
        var request = NewRequest();

        var successResponse = ResponseWith(DocumentationWithProvenance(AllCoreSectionsReal()));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(successResponse);
        var firstResult = await service.GenerateAsync(request);
        Assert.True(firstResult.Succeeded);
        var existingId = firstResult.Documentation!.Id;

        var provenance = AllCoreSectionsReal();
        provenance["useCases"] = "fallback";
        var fallbackResponse = ResponseWith(DocumentationWithProvenance(provenance));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(fallbackResponse);
        var secondResult = await service.GenerateAsync(request);

        Assert.False(secondResult.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, secondResult.ErrorCode);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());

        var preserved = await service.GetLatestForIdeaAsync(request.ProjectIdeaId);
        Assert.NotNull(preserved);
        Assert.Equal(existingId, preserved!.Id);
    }

    // 8 (Step C list). llmUsed=true and a non-fallback top-level source
    // cannot bypass per-section fallback rejection -- the exact gap this
    // step fixes.
    [Fact]
    public async Task LlmUsedTrueAndRealSource_CannotBypassCoreSectionFallbackRejection()
    {
        var (aiMock, db, service) = NewSut();
        var provenance = AllCoreSectionsReal();
        provenance["modulesArchitecture"] = "fallback";
        // Deliberately llmUsed=true, source="deepinfra" (a genuinely real,
        // non-fallback-marked top-level origin) and CoreSectionFallback left
        // at its default false -- the .NET gate must catch this from
        // SectionProvenance alone, independent of every other flag.
        var response = ResponseWith(DocumentationWithProvenance(provenance), llmUsed: true, source: "deepinfra", coreSectionFallback: false);
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.CoreSectionFallback, result.ErrorCode);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // ── Stabilization task: bounded-concurrency + reserved-review-budget ──

    private static AiSeDocumentationServiceResponse WriterBudgetExceededResponse(
        List<string> missingSections, List<string> completedSections) =>
        new(
            Documentation: null, "SEDocumentationAgent", LlmUsed: false, Source: "writer-budget-exceeded",
            Provider: null, ModelUsed: null, OllamaError: null, OllamaRawPreview: null,
            GeneratedAt: DateTime.UtcNow, Message: "ran out of time", Review: null,
            CoreSectionFallback: false,
            WriterBudgetExceeded: true,
            MissingSections: missingSections,
            CompletedSections: completedSections);

    // 17. WriterBudgetExceeded is rejected with its own distinct error code
    // (never bucketed under a generic ProviderUnavailable/CoreSectionFallback).
    [Fact]
    public async Task WriterBudgetExceeded_IsRejectedWithItsOwnErrorCode_NoPersistence()
    {
        var (aiMock, db, service) = NewSut();
        var response = WriterBudgetExceededResponse(
            missingSections: ["testingSecurity", "aiReport"],
            completedSections: ["requirements", "useCases", "modulesArchitecture", "database", "uiApi"]);
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(response);

        var result = await service.GenerateAsync(NewRequest());

        Assert.False(result.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.WriterBudgetExceeded, result.ErrorCode);
        Assert.Null(result.Documentation);
        Assert.Contains("testingSecurity", result.UserMessage);
        Assert.Equal(0, await db.ProjectDocumentations.CountAsync());
    }

    // 15 (stabilization task Step E list). The previously accepted document
    // remains unchanged after a WriterBudgetExceeded regeneration attempt.
    [Fact]
    public async Task WriterBudgetExceeded_LeavesPreviousValidDocumentUnchanged()
    {
        var (aiMock, db, service) = NewSut();
        var request = NewRequest();

        var successResponse = ResponseWith(DocumentationWithProvenance(AllCoreSectionsReal()));
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(successResponse);
        var firstResult = await service.GenerateAsync(request);
        Assert.True(firstResult.Succeeded);
        var existingId = firstResult.Documentation!.Id;

        var budgetExceededResponse = WriterBudgetExceededResponse(
            missingSections: ["uiApi", "testingSecurity"],
            completedSections: ["requirements", "useCases", "modulesArchitecture", "database"]);
        aiMock.Setup(x => x.GenerateSeDocumentationAsync(It.IsAny<AiSeDocumentationRequest>())).ReturnsAsync(budgetExceededResponse);
        var secondResult = await service.GenerateAsync(request);

        Assert.False(secondResult.Succeeded);
        Assert.Equal(SeDocumentationErrorCode.WriterBudgetExceeded, secondResult.ErrorCode);
        Assert.Equal(1, await db.ProjectDocumentations.CountAsync());

        var preserved = await service.GetLatestForIdeaAsync(request.ProjectIdeaId);
        Assert.NotNull(preserved);
        Assert.Equal(existingId, preserved!.Id);
    }
}
