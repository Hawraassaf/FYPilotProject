using FYPilot.Application.DTOs;

namespace FYPilot.Application.DTOs.Documentation;

// ── Outgoing request to POST /generate-se-documentation ─────────────────────
// Serialized with camelCase (see AiServiceClient.CamelCaseJsonOpts) to match
// the Python service's SEDocumentationRequest field names exactly.

public record AiSeDocStudentProfile(
    string Major,
    string ExperienceLevel,
    int TeamSize,
    int AvailableHoursPerWeek,
    List<string> Skills,
    Dictionary<string, int> SkillRatings
);

public record AiSeDocSelectedIdea(
    int? Id,
    string Title,
    string ProblemStatement,
    string TargetUsers,
    string WhyUseful,
    string RequiredTechnologies,
    string RequiredSkills,
    string MissingSkills,
    string DifficultyLevel,
    int ExpectedDurationWeeks,
    string Domain,
    string FinalDeliverables
);

public record AiSeDocRoadmapPhase(
    int PhaseNumber,
    string Name,
    string Objective,
    List<string> Tasks,
    string ExpectedOutput,
    string SuccessCriteria,
    bool IsCompleted
);

public record AiSeDocumentationRequest(
    AiSeDocStudentProfile? StudentProfile,
    AiSeDocSelectedIdea? SelectedIdea,
    List<AiSeDocRoadmapPhase> Roadmap,
    string ExistingNotes
);

// ── Incoming response from POST /generate-se-documentation ──────────────────
// Deserialized case-insensitively (see AiServiceClient.JsonOpts), so plain
// PascalCase properties here match the Python service's camelCase JSON.

public record AiSeDocumentationServiceResponse(
    AiSeDocumentationDto? Documentation,
    string Agent,
    bool LlmUsed,
    string Source,
    string? Provider,
    string? ModelUsed,
    string? OllamaError,
    string? OllamaRawPreview,
    DateTime? GeneratedAt,
    string Message,
    AiQualityPassportDto? Review = null
);

public record AiSeDocumentationDto(
    string ProjectTitle,
    string ProjectOverview,
    string ProblemStatement,
    List<string> Objectives,
    List<string> Stakeholders,
    AiSeDocScope Scope,
    List<AiSeDocRequirement> FunctionalRequirements,
    List<AiSeDocRequirement> NonFunctionalRequirements,
    List<AiSeDocUseCase> UseCases,
    List<AiSeDocEdgeCase> EdgeCases,
    List<AiSeDocModule> SystemModules,
    List<AiSeDocEntity> DatabaseEntities,
    List<AiSeDocRelationship> EntityRelationships,
    string MermaidERD,
    string MermaidClassDiagram,
    string ActivityDiagram,
    string SequenceDiagram,
    AiSeDocArchitecture Architecture,
    List<AiSeDocApiPoint> ApiIntegrationPoints,
    List<AiSeDocTestCase> TestingPlan,
    List<AiSeDocTraceability> TraceabilityMatrix,
    List<string> RisksAndLimitations,
    List<string> ExpectedOutcomes,
    int DocumentationQualityScore,
    List<string> ConsistencyWarnings
);

public record AiSeDocScope(
    List<string> InScope,
    List<string> OutOfScope,
    List<string> FutureWork
);

public record AiSeDocRequirement(
    string Id,
    string Title,
    string Description,
    string Priority,
    string Source
);

public record AiSeDocUseCase(
    string Id,
    string Title,
    string Actor,
    string Goal,
    List<string> Preconditions,
    List<string> MainFlow,
    List<string> AlternativeFlow,
    List<string> Postconditions,
    List<string> RelatedRequirements
);

public record AiSeDocEdgeCase(
    string Id,
    string Scenario,
    string ExpectedHandling,
    string RelatedRequirement
);

public record AiSeDocModule(
    string Id,
    string Name,
    string Responsibility,
    List<string> Inputs,
    List<string> Outputs,
    List<string> RelatedRequirements
);

public record AiSeDocEntity(
    string Name,
    string Purpose,
    List<string> ImportantFields,
    List<string> Relationships
);

public record AiSeDocRelationship(
    string FromEntity,
    string ToEntity,
    string Type,
    string Description
);

public record AiSeDocArchitecture(
    string Style,
    string Frontend,
    string Backend,
    string Database,
    string AiService,
    List<string> ExternalServices,
    string Explanation
);

public record AiSeDocApiPoint(
    string Name,
    string Method,
    string Endpoint,
    string Purpose,
    string RequestSummary,
    string ResponseSummary
);

public record AiSeDocTestCase(
    string Id,
    string Title,
    string Type,
    List<string> Steps,
    string ExpectedResult,
    List<string> RelatedRequirements
);

public record AiSeDocTraceability(
    string RequirementId,
    string UseCaseId,
    string ModuleId,
    string Entity,
    string TestCaseId
);
