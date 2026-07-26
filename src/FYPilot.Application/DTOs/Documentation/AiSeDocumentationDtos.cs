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
    List<string> ConsistencyWarnings,
    List<AiSeDocUiScreen>? UiScreens = null,
    List<AiSeDocSecurityItem>? SecurityAndPrivacy = null,
    List<AiSeDocAssumption>? Assumptions = null,
    AiSeDocAiTechnicalReport? AiTechnicalReport = null,
    bool AiTechnicalReportApplicable = false,
    AiSeDocQualityAssessment? QualityAssessment = null,
    // "provider" | "fallback" per section key -- lets the UI/report be
    // honest when a provider outage caused only SOME sections to fall back
    // to deterministic content, instead of mislabeling a partial result as
    // either fully AI-generated or fully generic.
    Dictionary<string, string>? SectionProvenance = null
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
    string Source,
    string? Rationale = null,
    string? PrimaryActor = null,
    List<string>? Preconditions = null,
    string? Trigger = null,
    List<string>? Inputs = null,
    string? SystemBehavior = null,
    List<string>? Outputs = null,
    List<string>? BusinessRules = null,
    List<string>? ValidationRules = null,
    List<string>? AcceptanceCriteria = null,
    List<string>? RelatedUseCaseIds = null,
    List<string>? RelatedModuleIds = null,
    string? SourceClassification = null,
    string? Category = null,
    string? MeasurableTarget = null,
    string? VerificationMethod = null,
    List<string>? RelatedComponents = null
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
    List<string> RelatedRequirements,
    string? Trigger = null,
    List<string>? SupportingActors = null,
    List<string>? ExceptionFlows = null,
    List<string>? DataUsed = null,
    string? SecurityConsiderations = null,
    string? Importance = null,
    string? SourceClassification = null
);

public record AiSeDocEdgeCase(
    string Id,
    string Scenario,
    string ExpectedHandling,
    string RelatedRequirement,
    string? Severity = null,
    string? RecoveryAction = null,
    string? UserMessage = null,
    string? LoggingRequirement = null,
    string? TestScenario = null,
    List<string>? AffectedUseCases = null,
    string? SourceClassification = null
);

public record AiSeDocModule(
    string Id,
    string Name,
    string Responsibility,
    List<string> Inputs,
    List<string> Outputs,
    List<string> RelatedRequirements,
    List<string>? Dependencies = null,
    List<string>? ExposedInterfaces = null,
    string? FailureBehavior = null,
    string? SourceClassification = null
);

public record AiSeDocEntityField(
    string Name,
    string DataType,
    bool Nullable,
    string? DefaultValue,
    string Description,
    string? Constraints,
    bool IsSensitive = false,
    bool IsPrimaryKey = false,
    bool IsForeignKey = false,
    string? ReferencedEntity = null,
    string? ReferencedField = null
);

public record AiSeDocEntity(
    string Name,
    string Purpose,
    List<string> ImportantFields,
    List<string> Relationships,
    List<AiSeDocEntityField>? Fields = null,
    string? PrimaryKey = null,
    List<string>? ForeignKeys = null,
    List<string>? UniqueConstraints = null,
    List<string>? Indexes = null,
    string? SourceClassification = null,
    string? EntityId = null,
    List<string>? ValidationConstraints = null,
    List<string>? SensitiveFields = null,
    List<string>? RelatedRequirementIds = null
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
    string Explanation,
    List<string>? Components = null,
    List<string>? CommunicationPaths = null,
    List<string>? TrustBoundaries = null,
    string? DataFlow = null,
    string? AuthenticationFlow = null,
    string? ErrorHandling = null,
    string? DeploymentAssumptions = null,
    string? ScalabilityConsiderations = null
);

public record AiSeDocApiPoint(
    string Name,
    string Method,
    string Endpoint,
    string Purpose,
    string RequestSummary,
    string ResponseSummary,
    string? Authentication = null,
    List<string>? RequestFields = null,
    List<string>? ResponseFields = null,
    string? TimeoutBehavior = null,
    string? SourceClassification = null,
    string? ApiId = null,
    List<string>? RelatedRequirements = null
);

public record AiSeDocUiScreen(
    string ScreenId,
    string Name,
    List<string> AuthorizedRoles,
    string Purpose,
    List<string> MainComponents,
    List<string> UserActions,
    List<string> ValidationRules,
    string LoadingState,
    string EmptyState,
    string ErrorState,
    string SuccessState,
    string AccessibilityNotes,
    List<string> RelatedUseCases,
    List<string> RelatedRequirements,
    string? SourceClassification = null
);

public record AiSeDocSecurityItem(
    string Category,
    string Requirement,
    string? Rationale = null
);

public record AiSeDocAssumption(
    string Item,
    string Classification,
    string? Rationale = null
);

public record AiSeDocQualityAssessment(
    int OverallScore,
    Dictionary<string, int> CriterionScores,
    List<string> FailedChecks,
    List<string> Warnings,
    List<string> MissingInformation,
    int AssumptionsCount,
    int CriticalIssuesCount = 0,
    Dictionary<string, int>? CoverageStatistics = null
);

public record AiSeDocAiTechnicalReport(
    string? ProblemDefinition = null,
    string? TaskType = null,
    string? InputData = null,
    string? Output = null,
    string? ModelOrApproach = null,
    string? TrainingVsInference = null,
    string? RetrievalStrategy = null,
    string? FallbackStrategy = null,
    string? ConfidenceHandling = null,
    List<string>? EvaluationMetrics = null,
    string? DatasetNeeds = null,
    string? BiasAndSafetyRisks = null,
    string? HallucinationMitigation = null,
    string? Monitoring = null,
    string? Limitations = null
);

public record AiSeDocTestCase(
    string Id,
    string Title,
    string Type,
    List<string> Steps,
    string ExpectedResult,
    List<string> RelatedRequirements,
    List<string>? Preconditions = null,
    string? Priority = null,
    List<string>? TestData = null,
    string? PassCriteria = null,
    bool NegativeCase = false,
    List<string>? RelatedUseCaseIds = null,
    bool AutomationCandidate = false,
    string? SourceClassification = null
);

public record AiSeDocTraceability(
    string RequirementId,
    string UseCaseId,
    string ModuleId,
    string Entity,
    string TestCaseId,
    string? ScreenId = null,
    string? ApiId = null,
    List<string>? UseCaseIds = null,
    List<string>? ModuleIds = null,
    List<string>? EntityIds = null,
    List<string>? ScreenIds = null,
    List<string>? ApiIds = null,
    List<string>? TestCaseIds = null,
    string? CoverageStatus = null,
    string? Notes = null
);
