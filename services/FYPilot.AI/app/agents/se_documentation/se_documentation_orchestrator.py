import json
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.se_documentation.mermaid_utils import (
    participant_declaration,
    safe_label,
    safe_participant_id,
    split_combined_actor,
    validate_mermaid,
)
from app.agents.se_documentation.project_facts import (
    CanonicalFeature,
    ProjectFacts,
    TechnicalProfile,
    ai_service_label,
    build_project_facts,
    derive_canonical_features,
    facts_context_text,
    required_entities_for_text,
    required_screens_for_text,
)
from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-se-documentation")

# Canonical launch order for the ordered bounded queue (see
# _generate_llm_sections). "aiReport" is appended conditionally, only when
# facts.ai_involved. Every one of these 7 prompts is built solely from the
# same static facts_context_text(facts) string -- none reads another
# section's generated output -- so this order reflects the previous
# sequential order for minimal behavioral drift, not a true dependency
# requirement.
_CORE_SECTION_QUEUE: tuple[str, ...] = (
    "requirements",
    "useCases",
    "modulesArchitecture",
    "database",
    "uiApi",
    "testingSecurity",
)

# At most this many section calls run concurrently.
_MAX_CONCURRENT_SECTION_CALLS = 2

# Matches ProviderChain._MIN_SECONDS_PER_PROVIDER_ATTEMPT -- do not launch
# (or attempt a fallback provider within) a call when less than this much
# Writer budget remains; a call started with less than this has no
# realistic chance of completing usefully.
_MIN_SECONDS_PER_SECTION_ATTEMPT = 4.0

# Generic AI/ML vocabulary used ONLY as a project-agnostic centrality signal
# (see _select_primary_use_case and _build_sequence_diagram) -- never a
# project-type check like "if medical project then choose triage". Matches
# equally well against, e.g., a spam-classification FYP, a course-
# recommendation FYP, or a symptom-triage FYP, because it keys off generic
# ML task vocabulary that a requirement/module/use-case's own title or
# description already contains, never off a domain name.
_AI_CENTRALITY_KEYWORDS = (
    "ai", "ml", "nlp", "model", "classif", "predict", "recommend", "inference", "intent", "confidence",
)


def _normalize_actor_key(name: str) -> str:
    """Folds trivial actor-name variants (plural, case, punctuation/
    whitespace) to the SAME identity key -- "Patients"/"Patient"/"patient"
    all normalize to "patient" -- while leaving genuinely different actors
    (e.g. "Administrator" vs "Patient") distinct. Deliberately simple (strip
    non-letters, lowercase, drop a trailing 's' on words longer than 3
    letters) -- good enough for the short actor names this project ever
    generates, not a general English pluralization library."""
    cleaned = re.sub(r"[^a-z]", "", (name or "").lower())
    if len(cleaned) > 3 and cleaned.endswith("s"):
        cleaned = cleaned[:-1]
    return cleaned


@dataclass
class _DiagramParticipant:
    key: str
    mermaid_id: str
    label: str
    is_actor: bool = False


class _ParticipantRegistry:
    """
    Deterministic sequence-diagram participant registry (see this task's
    "participant registry" requirement): every participant is registered
    exactly once, keyed by a NORMALIZED semantic identity (not raw display
    text), and rendered exactly once. This is what actually fixes the live
    duplicate-participant bug -- architecture.frontend and
    architecture.backend both resolving to the literal string "ASP.NET Core
    Razor Pages" (because that technology name matches both the frontend and
    backend keyword lists in _pick_layer) used to produce two separate
    `participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages`
    declarations plus a meaningless `ASPNETCoreRazorPages -> ASPNETCoreRazorPages`
    self-call. Registering both under the same normalized key collapses them
    into a single participant instead.

    Mermaid ids are also guaranteed unique even when two DIFFERENT
    participants sanitize to the same id (e.g. "AI Service" and
    "AI-Service" both sanitizing to "AIService") -- a numeric suffix is
    appended deterministically (AIService, AIService2, ...).
    """

    def __init__(self) -> None:
        self._by_key: Dict[str, _DiagramParticipant] = {}
        self._used_ids: set = set()
        self._order: List[str] = []

    @staticmethod
    def _normalize_key(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip()).lower()

    def register(self, identity: str, label: str, *, is_actor: bool = False) -> _DiagramParticipant:
        key = self._normalize_key(identity) or self._normalize_key(label) or "participant"
        existing = self._by_key.get(key)
        if existing is not None:
            return existing

        base_id = safe_participant_id(label) or "Participant"
        candidate_id = base_id
        suffix = 2
        while candidate_id in self._used_ids:
            candidate_id = f"{base_id}{suffix}"
            suffix += 1
        self._used_ids.add(candidate_id)

        participant = _DiagramParticipant(key=key, mermaid_id=candidate_id, label=label.strip() or label, is_actor=is_actor)
        self._by_key[key] = participant
        self._order.append(key)
        return participant

    def render(self) -> List[str]:
        return [
            participant_declaration(
                self._by_key[key].mermaid_id, self._by_key[key].label, is_actor=self._by_key[key].is_actor,
            )
            for key in self._order
        ]


class SEDocStudentProfile(BaseModel):
    major: str = "Computer Science"
    experienceLevel: str = "intermediate"
    teamSize: int = 1
    availableHoursPerWeek: int = 10
    skills: List[str] = Field(default_factory=list)
    skillRatings: Dict[str, int] = Field(default_factory=dict)


class SEDocSelectedIdea(BaseModel):
    id: Optional[int] = None
    title: str = ""
    problemStatement: str = ""
    targetUsers: str = ""
    whyUseful: str = ""
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = ""
    expectedDurationWeeks: int = 10
    domain: str = ""
    finalDeliverables: str = ""


class SEDocRoadmapPhase(BaseModel):
    phaseNumber: int = 0
    name: str = ""
    objective: str = ""
    tasks: List[str] = Field(default_factory=list)
    expectedOutput: str = ""
    successCriteria: str = ""
    isCompleted: bool = False


class SEDocumentationRequest(BaseModel):
    studentProfile: Optional[SEDocStudentProfile] = None
    selectedIdea: Optional[SEDocSelectedIdea] = None
    roadmap: List[SEDocRoadmapPhase] = Field(default_factory=list)
    existingNotes: str = ""
    model: str = "qwen2.5-coder:7b"


class ScopeDto(BaseModel):
    inScope: List[str] = Field(default_factory=list)
    outOfScope: List[str] = Field(default_factory=list)
    futureWork: List[str] = Field(default_factory=list)


class RequirementDto(BaseModel):
    """
    Shared shape for both functional and non-functional requirements. Every
    field beyond id/title/description/priority/source is optional so
    existing callers/tests that only set the original 5 fields keep
    working -- richer content (rationale, acceptance criteria, measurable
    targets, ...) is additive, not a breaking schema change.
    """

    id: str
    title: str
    description: str
    priority: str
    source: str
    rationale: str = ""
    primaryActor: str = ""
    preconditions: List[str] = Field(default_factory=list)
    trigger: str = ""
    inputs: List[str] = Field(default_factory=list)
    systemBehavior: str = ""
    outputs: List[str] = Field(default_factory=list)
    businessRules: List[str] = Field(default_factory=list)
    validationRules: List[str] = Field(default_factory=list)
    acceptanceCriteria: List[str] = Field(default_factory=list)
    relatedUseCaseIds: List[str] = Field(default_factory=list)
    relatedModuleIds: List[str] = Field(default_factory=list)
    sourceClassification: str = "confirmed"
    # NFR-only fields (ignored for FRs)
    category: str = ""
    measurableTarget: str = ""
    verificationMethod: str = ""
    relatedComponents: List[str] = Field(default_factory=list)


class UseCaseDto(BaseModel):
    id: str
    title: str
    actor: str
    goal: str
    preconditions: List[str] = Field(default_factory=list)
    mainFlow: List[str] = Field(default_factory=list)
    alternativeFlow: List[str] = Field(default_factory=list)
    postconditions: List[str] = Field(default_factory=list)
    relatedRequirements: List[str] = Field(default_factory=list)
    trigger: str = ""
    supportingActors: List[str] = Field(default_factory=list)
    exceptionFlows: List[str] = Field(default_factory=list)
    dataUsed: List[str] = Field(default_factory=list)
    securityConsiderations: str = ""
    importance: str = "Medium"
    sourceClassification: str = "confirmed"


class EdgeCaseDto(BaseModel):
    id: str
    scenario: str
    expectedHandling: str
    relatedRequirement: str
    severity: str = "Medium"
    recoveryAction: str = ""
    userMessage: str = ""
    loggingRequirement: str = ""
    testScenario: str = ""
    affectedUseCases: List[str] = Field(default_factory=list)
    sourceClassification: str = "confirmed"


class ModuleDto(BaseModel):
    id: str
    name: str
    responsibility: str
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    relatedRequirements: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    exposedInterfaces: List[str] = Field(default_factory=list)
    failureBehavior: str = ""
    sourceClassification: str = "confirmed"


class EntityFieldDto(BaseModel):
    name: str
    dataType: str = "string"
    nullable: bool = False
    defaultValue: str = ""
    description: str = ""
    constraints: str = ""
    isSensitive: bool = False
    isPrimaryKey: bool = False
    isForeignKey: bool = False
    referencedEntity: str = ""
    referencedField: str = ""


class EntityDto(BaseModel):
    entityId: str = ""
    name: str
    purpose: str
    importantFields: List[str] = Field(default_factory=list)
    relationships: List[str] = Field(default_factory=list)
    fields: List[EntityFieldDto] = Field(default_factory=list)
    primaryKey: str = "Id"
    foreignKeys: List[str] = Field(default_factory=list)
    uniqueConstraints: List[str] = Field(default_factory=list)
    indexes: List[str] = Field(default_factory=list)
    validationConstraints: List[str] = Field(default_factory=list)
    sensitiveFields: List[str] = Field(default_factory=list)
    relatedRequirementIds: List[str] = Field(default_factory=list)
    sourceClassification: str = "confirmed"


class RelationshipDto(BaseModel):
    fromEntity: str
    toEntity: str
    type: str
    description: str


class ArchitectureDto(BaseModel):
    style: str
    frontend: str
    backend: str
    database: str
    aiService: str
    externalServices: List[str] = Field(default_factory=list)
    explanation: str
    components: List[str] = Field(default_factory=list)
    communicationPaths: List[str] = Field(default_factory=list)
    trustBoundaries: List[str] = Field(default_factory=list)
    dataFlow: str = ""
    authenticationFlow: str = ""
    errorHandling: str = ""
    deploymentAssumptions: str = ""
    scalabilityConsiderations: str = ""


class ApiPointDto(BaseModel):
    apiId: str = ""
    name: str
    method: str
    endpoint: str
    purpose: str
    requestSummary: str
    responseSummary: str
    authentication: str = ""
    requestFields: List[str] = Field(default_factory=list)
    responseFields: List[str] = Field(default_factory=list)
    timeoutBehavior: str = ""
    relatedRequirements: List[str] = Field(default_factory=list)
    sourceClassification: str = "assumption"


class UiScreenDto(BaseModel):
    screenId: str
    name: str
    authorizedRoles: List[str] = Field(default_factory=list)
    purpose: str = ""
    mainComponents: List[str] = Field(default_factory=list)
    userActions: List[str] = Field(default_factory=list)
    validationRules: List[str] = Field(default_factory=list)
    loadingState: str = ""
    emptyState: str = ""
    errorState: str = ""
    successState: str = ""
    accessibilityNotes: str = ""
    relatedUseCases: List[str] = Field(default_factory=list)
    relatedRequirements: List[str] = Field(default_factory=list)
    sourceClassification: str = "confirmed"


class SecurityItemDto(BaseModel):
    category: str
    requirement: str
    rationale: str = ""


class AssumptionDto(BaseModel):
    item: str
    classification: str = "assumption"
    rationale: str = ""


class QualityAssessmentDto(BaseModel):
    overallScore: int = 0
    criterionScores: Dict[str, int] = Field(default_factory=dict)
    failedChecks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missingInformation: List[str] = Field(default_factory=list)
    assumptionsCount: int = 0
    criticalIssuesCount: int = 0
    coverageStatistics: Dict[str, int] = Field(default_factory=dict)


# The single source of truth for how each criterion contributes to
# overallScore -- shared between _compute_quality_assessment (the base,
# pre-review calculation) and quality_outcome_policy.apply_review_outcome_to_quality
# (the post-review adjustment, which must recompute overallScore from
# criterionScores using these SAME weights whenever it caps one). Defining
# this in exactly one place is what keeps "every numeric cap or penalty in
# one policy location" true across both functions.
QUALITY_CRITERION_WEIGHTS: Dict[str, float] = {
    "completeness": 0.15,
    "requirementTestability": 0.10,
    "crossSectionConsistency": 0.15,
    "traceabilityCoverage": 0.15,
    "projectSpecificity": 0.10,
    "diagramValidity": 0.07,
    "assumptionTransparency": 0.06,
    "databaseQuality": 0.07,
    "contentDepth": 0.15,
}


class AiTechnicalReportDto(BaseModel):
    problemDefinition: str = ""
    taskType: str = ""
    inputData: str = ""
    output: str = ""
    modelOrApproach: str = ""
    trainingVsInference: str = ""
    retrievalStrategy: str = ""
    fallbackStrategy: str = ""
    confidenceHandling: str = ""
    evaluationMetrics: List[str] = Field(default_factory=list)
    datasetNeeds: str = ""
    biasAndSafetyRisks: str = ""
    hallucinationMitigation: str = ""
    monitoring: str = ""
    limitations: str = ""


class TestCaseDto(BaseModel):
    __test__ = False  # not a pytest test class -- silences a collection warning

    id: str
    title: str
    type: str
    steps: List[str] = Field(default_factory=list)
    expectedResult: str
    relatedRequirements: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    priority: str = "Medium"
    testData: List[str] = Field(default_factory=list)
    passCriteria: str = ""
    negativeCase: bool = False
    relatedUseCaseIds: List[str] = Field(default_factory=list)
    automationCandidate: bool = False
    sourceClassification: str = "confirmed"


class TraceabilityDto(BaseModel):
    """
    Requirement-centric traceability row. The singular fields
    (useCaseId/moduleId/entity/testCaseId/screenId/apiId) are kept for
    backward compatibility with existing .NET DTOs/persisted documents and
    are always the first item of the corresponding plural list; new code
    should read the plural *Ids lists, which capture every real reference
    instead of an arbitrary positional pairing.
    """

    requirementId: str
    useCaseId: str = ""
    moduleId: str = ""
    entity: str = ""
    testCaseId: str = ""
    screenId: str = ""
    apiId: str = ""
    useCaseIds: List[str] = Field(default_factory=list)
    moduleIds: List[str] = Field(default_factory=list)
    entityIds: List[str] = Field(default_factory=list)
    screenIds: List[str] = Field(default_factory=list)
    apiIds: List[str] = Field(default_factory=list)
    testCaseIds: List[str] = Field(default_factory=list)
    coverageStatus: str = "covered"
    notes: str = ""


class SEDocumentationDto(BaseModel):
    projectTitle: str
    projectOverview: str
    problemStatement: str
    objectives: List[str] = Field(default_factory=list)
    stakeholders: List[str] = Field(default_factory=list)
    scope: ScopeDto
    functionalRequirements: List[RequirementDto] = Field(default_factory=list)
    nonFunctionalRequirements: List[RequirementDto] = Field(default_factory=list)
    useCases: List[UseCaseDto] = Field(default_factory=list)
    edgeCases: List[EdgeCaseDto] = Field(default_factory=list)
    systemModules: List[ModuleDto] = Field(default_factory=list)
    databaseEntities: List[EntityDto] = Field(default_factory=list)
    entityRelationships: List[RelationshipDto] = Field(default_factory=list)
    mermaidERD: str
    mermaidClassDiagram: str
    activityDiagram: str
    sequenceDiagram: str
    architecture: ArchitectureDto
    apiIntegrationPoints: List[ApiPointDto] = Field(default_factory=list)
    uiScreens: List[UiScreenDto] = Field(default_factory=list)
    securityAndPrivacy: List[SecurityItemDto] = Field(default_factory=list)
    testingPlan: List[TestCaseDto] = Field(default_factory=list)
    traceabilityMatrix: List[TraceabilityDto] = Field(default_factory=list)
    risksAndLimitations: List[str] = Field(default_factory=list)
    expectedOutcomes: List[str] = Field(default_factory=list)
    assumptions: List[AssumptionDto] = Field(default_factory=list)
    aiTechnicalReport: Optional[AiTechnicalReportDto] = None
    aiTechnicalReportApplicable: bool = False
    documentationQualityScore: int
    qualityAssessment: Optional[QualityAssessmentDto] = None
    consistencyWarnings: List[str] = Field(default_factory=list)
    # "provider" | "fallback" per section key (requirements, useCases,
    # modulesArchitecture, database, uiApi, testingSecurity, aiReport) --
    # internal provenance so a partial failure is never silently presented
    # as either "fully AI-generated" or "fully generic fallback" when it was
    # actually a mix of both. Additive/optional so older persisted documents
    # without this field still deserialize.
    sectionProvenance: Dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project-specific fallback content templates, keyed by the entity/screen
# names CanonicalFeature rules in project_facts.py actually produce. Fixes
# the verified "Fields: )" / generic-CRUD bug: the deterministic fallback
# used to synthesize a single generic "{domain}Record" entity with 4 filler
# fields and a "Summary Dashboard" screen regardless of project. These
# templates give every feature-derived entity/screen real, detailed content;
# any entity/screen name that isn't in these dicts (should not normally
# happen given the curated feature list) still gets a minimal-but-non-empty
# generic template rather than an empty one.
# ---------------------------------------------------------------------------

_ENTITY_PURPOSE: Dict[str, str] = {
    "User": "Stores authenticated system users.",
    "Role": "Stores role definitions used for authorization.",
    "Conversation": "Stores a conversation/session between an actor and the system.",
    "Message": "Stores individual messages within a conversation.",
    "KnowledgeArticle": "Stores verified knowledge-base articles/FAQ entries used to answer queries.",
    "KnowledgeCategory": "Stores categories used to organize knowledge-base articles.",
    "Intent": "Stores classifiable intents used to interpret incoming queries.",
    "TrainingPhrase": "Stores example phrases used to train or match an intent.",
    "SupportTicket": "Stores escalated support tickets requiring staff follow-up.",
    "ResponseFeedback": "Stores actor feedback/ratings on system responses.",
    "UnansweredQuery": "Stores queries the system could not confidently answer, queued for review.",
    "QueryLog": "Stores a record of processed queries/interactions for analytics and review.",
    "SystemSetting": "Stores configurable system thresholds/settings.",
    "Product": "Stores product catalog information and reorder thresholds.",
    "StockTransaction": "Stores an auditable record of every stock-affecting event.",
    "Supplier": "Stores suppliers and their contact information.",
}

_ENTITY_FIELD_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "User": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Email", "dataType": "string", "description": "Login identifier.", "constraints": "unique"},
        {"name": "PasswordHash", "dataType": "string", "description": "Salted hash of the account password.", "constraints": "sensitive", "isSensitive": True},
        {"name": "RoleId", "dataType": "int", "description": "Assigned role.", "constraints": "foreign key -> Role.Id", "isForeignKey": True, "referencedEntity": "Role", "referencedField": "Id"},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Account creation timestamp."},
    ],
    "Role": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Name", "dataType": "string", "description": "Role name (e.g. Student, Support Staff, Administrator).", "constraints": "unique"},
    ],
    "Conversation": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "StudentId", "dataType": "int", "description": "Owning actor.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "Status", "dataType": "string", "description": "Conversation status.", "constraints": "allowed values: Open, Resolved, Escalated"},
        {"name": "StartedAt", "dataType": "datetime", "description": "When the conversation began."},
        {"name": "LastMessageAt", "dataType": "datetime", "nullable": True, "description": "Timestamp of the most recent message."},
        {"name": "ClosedAt", "dataType": "datetime", "nullable": True, "description": "When the conversation was closed, if applicable."},
    ],
    "Message": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "ConversationId", "dataType": "int", "description": "Parent conversation.", "constraints": "foreign key -> Conversation.Id", "isForeignKey": True, "referencedEntity": "Conversation", "referencedField": "Id"},
        {"name": "SenderType", "dataType": "string", "description": "Who sent the message.", "constraints": "allowed values: Actor, System"},
        {"name": "Content", "dataType": "text", "description": "Message body."},
        {"name": "DetectedIntent", "dataType": "string", "nullable": True, "description": "Intent classification result, if applicable."},
        {"name": "ConfidenceScore", "dataType": "decimal", "nullable": True, "description": "Confidence of the classification/answer."},
        {"name": "Provider", "dataType": "string", "nullable": True, "description": "AI provider/approach that produced the response."},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Message timestamp."},
        {"name": "IsFlagged", "dataType": "bool", "description": "Whether the message was flagged by content screening."},
    ],
    "KnowledgeArticle": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Title", "dataType": "string", "description": "Article title."},
        {"name": "QuestionPattern", "dataType": "text", "description": "Representative question(s) this article answers."},
        {"name": "AnswerText", "dataType": "text", "description": "The verified answer content."},
        {"name": "CategoryId", "dataType": "int", "nullable": True, "description": "Article category.", "constraints": "foreign key -> KnowledgeCategory.Id", "isForeignKey": True, "referencedEntity": "KnowledgeCategory", "referencedField": "Id"},
        {"name": "Status", "dataType": "string", "description": "Publication status.", "constraints": "allowed values: Draft, Published, Retired"},
        {"name": "Version", "dataType": "int", "description": "Revision number."},
        {"name": "CreatedById", "dataType": "int", "description": "Author.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "UpdatedAt", "dataType": "datetime", "description": "Last update timestamp."},
    ],
    "KnowledgeCategory": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Name", "dataType": "string", "description": "Category name.", "constraints": "unique"},
        {"name": "Description", "dataType": "string", "nullable": True, "description": "Category description."},
    ],
    "Intent": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Name", "dataType": "string", "description": "Intent name.", "constraints": "unique"},
        {"name": "Description", "dataType": "string", "nullable": True, "description": "What this intent represents."},
    ],
    "TrainingPhrase": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "IntentId", "dataType": "int", "description": "Parent intent.", "constraints": "foreign key -> Intent.Id", "isForeignKey": True, "referencedEntity": "Intent", "referencedField": "Id"},
        {"name": "PhraseText", "dataType": "string", "description": "Example phrase used to train/match this intent."},
    ],
    "SupportTicket": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "ConversationId", "dataType": "int", "nullable": True, "description": "Originating conversation.", "constraints": "foreign key -> Conversation.Id", "isForeignKey": True, "referencedEntity": "Conversation", "referencedField": "Id"},
        {"name": "StudentId", "dataType": "int", "description": "Ticket owner.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "AssignedStaffId", "dataType": "int", "nullable": True, "description": "Assigned support staff member.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "Status", "dataType": "string", "description": "Ticket status.", "constraints": "allowed values: Open, InProgress, Resolved"},
        {"name": "Priority", "dataType": "string", "description": "Ticket priority."},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Creation timestamp."},
        {"name": "ResolvedAt", "dataType": "datetime", "nullable": True, "description": "Resolution timestamp."},
    ],
    "ResponseFeedback": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "MessageId", "dataType": "int", "description": "The response being rated.", "constraints": "foreign key -> Message.Id", "isForeignKey": True, "referencedEntity": "Message", "referencedField": "Id"},
        {"name": "UserId", "dataType": "int", "description": "Actor giving feedback.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "Rating", "dataType": "int", "description": "Rating value (e.g. 1-5)."},
        {"name": "Comment", "dataType": "text", "nullable": True, "description": "Optional free-text feedback."},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Feedback timestamp."},
    ],
    "UnansweredQuery": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "MessageId", "dataType": "int", "description": "The unanswered message.", "constraints": "foreign key -> Message.Id", "isForeignKey": True, "referencedEntity": "Message", "referencedField": "Id"},
        {"name": "QueryText", "dataType": "text", "description": "The original query text."},
        {"name": "ReviewedById", "dataType": "int", "nullable": True, "description": "Staff member who reviewed it.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
        {"name": "ReviewStatus", "dataType": "string", "description": "Review status.", "constraints": "allowed values: Pending, Resolved"},
        {"name": "CreatedAt", "dataType": "datetime", "description": "When the query went unanswered."},
    ],
    "QueryLog": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "ConversationId", "dataType": "int", "nullable": True, "description": "Related conversation, if any.", "constraints": "foreign key -> Conversation.Id", "isForeignKey": True, "referencedEntity": "Conversation", "referencedField": "Id"},
        {"name": "QueryText", "dataType": "text", "description": "The processed query."},
        {"name": "DetectedIntent", "dataType": "string", "nullable": True, "description": "Classified intent, if applicable."},
        {"name": "ConfidenceScore", "dataType": "decimal", "nullable": True, "description": "Confidence of the result."},
        {"name": "ResponseTimeMs", "dataType": "int", "description": "Time taken to produce the response, in milliseconds."},
        {"name": "CreatedAt", "dataType": "datetime", "description": "When the query was logged."},
    ],
    "SystemSetting": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Key", "dataType": "string", "description": "Setting name.", "constraints": "unique"},
        {"name": "Value", "dataType": "string", "description": "Current configured value."},
        {"name": "Description", "dataType": "string", "nullable": True, "description": "What this setting controls."},
        {"name": "UpdatedAt", "dataType": "datetime", "description": "Last change timestamp."},
    ],
    "Product": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Sku", "dataType": "string", "description": "Stock-keeping unit code.", "constraints": "unique"},
        {"name": "Name", "dataType": "string", "description": "Product name."},
        {"name": "Description", "dataType": "string", "nullable": True, "description": "Product description."},
        {"name": "UnitPrice", "dataType": "decimal", "description": "Current unit price."},
        {"name": "ReorderThreshold", "dataType": "int", "description": "Stock level below which a low-stock alert is raised."},
        {"name": "SupplierId", "dataType": "int", "nullable": True, "description": "Primary supplier.", "constraints": "foreign key -> Supplier.Id", "isForeignKey": True, "referencedEntity": "Supplier", "referencedField": "Id"},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Product creation timestamp."},
    ],
    "StockTransaction": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "ProductId", "dataType": "int", "description": "Affected product.", "constraints": "foreign key -> Product.Id", "isForeignKey": True, "referencedEntity": "Product", "referencedField": "Id"},
        {"name": "Type", "dataType": "string", "description": "Transaction type.", "constraints": "allowed values: Receipt, Sale, Adjustment"},
        {"name": "Quantity", "dataType": "int", "description": "Quantity affected (signed)."},
        {"name": "OccurredAt", "dataType": "datetime", "description": "When the transaction occurred."},
        {"name": "RecordedById", "dataType": "int", "description": "Actor who recorded the transaction.", "constraints": "foreign key -> User.Id", "isForeignKey": True, "referencedEntity": "User", "referencedField": "Id"},
    ],
    "Supplier": [
        {"name": "Id", "dataType": "int", "description": "Primary key.", "constraints": "primary key", "isPrimaryKey": True},
        {"name": "Name", "dataType": "string", "description": "Supplier name."},
        {"name": "ContactEmail", "dataType": "string", "nullable": True, "description": "Primary contact email."},
        {"name": "Phone", "dataType": "string", "nullable": True, "description": "Primary contact phone number."},
        {"name": "CreatedAt", "dataType": "datetime", "description": "Record creation timestamp."},
    ],
}

_SCREEN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "Login": {
        "purpose": "Authenticate the actor before granting access to protected features.",
        "mainComponents": ["Email field", "Password field", "Submit button"],
        "userActions": ["Enter credentials", "Submit"],
        "validationRules": ["Both fields are required."],
        "loadingState": "Shows a spinner while credentials are verified.",
        "emptyState": "Not applicable.",
        "errorState": "Shows an inline error for invalid credentials.",
        "successState": "Redirects to the actor's main screen.",
    },
    "Chat Interface": {
        "purpose": "Let the actor submit a question and view the system's response in real time.",
        "mainComponents": ["Message input box", "Send button", "Message thread"],
        "userActions": ["Type a question", "Submit the question", "View the response"],
        "validationRules": ["Question must not be empty.", "Question length must stay within the configured limit."],
        "loadingState": "Shows a typing/response indicator while the query is processed.",
        "emptyState": "Shows a prompt inviting the actor to ask their first question.",
        "errorState": "Shows a retry option if the query could not be processed.",
        "successState": "Displays the answer, clarification request, or escalation confirmation.",
    },
    "Conversation History": {
        "purpose": "Let the actor browse and reopen their previous conversations.",
        "mainComponents": ["Conversation list", "Search/filter control"],
        "userActions": ["Select a past conversation", "Reopen a conversation"],
        "validationRules": [],
        "loadingState": "Shows a skeleton list while conversations load.",
        "emptyState": "Shows guidance when the actor has no previous conversations.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the selected conversation's full message history.",
    },
    "Knowledge Base Browser": {
        "purpose": "Let the actor search and browse published knowledge-base articles directly.",
        "mainComponents": ["Search box", "Category filter", "Article list"],
        "userActions": ["Search articles", "Filter by category", "Open an article"],
        "validationRules": [],
        "loadingState": "Shows a skeleton list while articles load.",
        "emptyState": "Shows a message when no articles match the search.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the matched articles.",
    },
    "Knowledge Base Management": {
        "purpose": "Let authorized staff create, edit, categorize, and retire knowledge-base articles.",
        "mainComponents": ["Article list", "Article editor", "Category selector", "Publish/retire controls"],
        "userActions": ["Create article", "Edit article", "Assign category", "Publish", "Retire"],
        "validationRules": ["Title and answer text are required before publishing."],
        "loadingState": "Shows a skeleton list while articles load.",
        "emptyState": "Shows guidance to create the first article.",
        "errorState": "Shows a retry option on save/load failure.",
        "successState": "Shows the updated article list.",
    },
    "Support Ticket Submission": {
        "purpose": "Let the actor escalate an unresolved query to a support ticket.",
        "mainComponents": ["Ticket summary field", "Related conversation reference", "Submit button"],
        "userActions": ["Review the auto-filled summary", "Submit the ticket"],
        "validationRules": ["Summary must not be empty."],
        "loadingState": "Shows a spinner while the ticket is created.",
        "emptyState": "Not applicable.",
        "errorState": "Shows an error if ticket creation fails.",
        "successState": "Shows the created ticket's tracking id.",
    },
    "Ticket Tracking": {
        "purpose": "Let the actor track the status of their submitted support tickets.",
        "mainComponents": ["Ticket list", "Ticket status badge"],
        "userActions": ["View ticket list", "Open a ticket for detail"],
        "validationRules": [],
        "loadingState": "Shows a skeleton list while tickets load.",
        "emptyState": "Shows a message when the actor has no tickets.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the current ticket list with status.",
    },
    "Feedback Controls": {
        "purpose": "Let the actor rate or comment on a system response.",
        "mainComponents": ["Rating control", "Optional comment box"],
        "userActions": ["Select a rating", "Submit optional comment"],
        "validationRules": ["A rating value is required."],
        "loadingState": "Shows a brief saving indicator.",
        "emptyState": "Not applicable.",
        "errorState": "Shows an error if the feedback could not be saved.",
        "successState": "Shows a confirmation that feedback was recorded.",
    },
    "Unanswered Query Review": {
        "purpose": "Let staff review queries the system could not confidently answer.",
        "mainComponents": ["Unresolved query list", "Link-to-article control"],
        "userActions": ["Review a query", "Link it to a knowledge article", "Mark resolved"],
        "validationRules": [],
        "loadingState": "Shows a skeleton list while queries load.",
        "emptyState": "Shows a message when there are no unresolved queries.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the updated review queue.",
    },
    "Analytics Dashboard": {
        "purpose": "Let staff review usage volume, unresolved-query rate, and quality metrics.",
        "mainComponents": ["Summary metric cards", "Trend chart"],
        "userActions": ["Select a reporting period", "View metric detail"],
        "validationRules": [],
        "loadingState": "Shows placeholder cards while metrics load.",
        "emptyState": "Shows a message when there is no data for the selected period.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the populated metrics.",
    },
    "System Configuration": {
        "purpose": "Let administrators view and update configurable system settings.",
        "mainComponents": ["Settings list", "Value editor"],
        "userActions": ["View a setting", "Update a setting's value"],
        "validationRules": ["New value must satisfy the setting's expected type/range."],
        "loadingState": "Shows a skeleton list while settings load.",
        "emptyState": "Not applicable.",
        "errorState": "Shows an error if the update fails validation.",
        "successState": "Shows the updated setting value.",
    },
    "Stock Tracking Dashboard": {
        "purpose": "Let the actor view current stock levels and recent transactions.",
        "mainComponents": ["Stock level table", "Recent transactions list"],
        "userActions": ["Filter by product", "View a product's transaction history"],
        "validationRules": [],
        "loadingState": "Shows a skeleton table while stock data loads.",
        "emptyState": "Shows guidance when no products are recorded yet.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows current stock levels.",
    },
    "Low-Stock Alerts": {
        "purpose": "Show products that have fallen below their configured reorder threshold.",
        "mainComponents": ["Low-stock product list"],
        "userActions": ["Review a flagged product", "Initiate a reorder"],
        "validationRules": [],
        "loadingState": "Shows a skeleton list while alerts load.",
        "emptyState": "Shows a confirmation that no products are currently low on stock.",
        "errorState": "Shows a retry option on load failure.",
        "successState": "Shows the current list of low-stock products.",
    },
    "Product Details": {
        "purpose": "Let the actor create, edit, and view product catalog records.",
        "mainComponents": ["Product form", "Product list"],
        "userActions": ["Create product", "Edit product", "View product detail"],
        "validationRules": ["SKU, name, and unit price are required."],
        "loadingState": "Shows a skeleton form/list while data loads.",
        "emptyState": "Shows guidance to add the first product.",
        "errorState": "Shows a retry option on save/load failure.",
        "successState": "Shows the saved product / updated catalog list.",
    },
    "Supplier Management": {
        "purpose": "Let the actor record suppliers and their contact information.",
        "mainComponents": ["Supplier form", "Supplier list"],
        "userActions": ["Create supplier", "Edit supplier"],
        "validationRules": ["Supplier name is required."],
        "loadingState": "Shows a skeleton list while suppliers load.",
        "emptyState": "Shows guidance to add the first supplier.",
        "errorState": "Shows a retry option on save/load failure.",
        "successState": "Shows the updated supplier list.",
    },
}


@dataclass(frozen=True)
class RequirementsRegistryEntry:
    """One immutable, canonical entry -- id/title never change after the
    requirements section is frozen (see RequirementsRegistry)."""

    id: str
    title: str
    description: str
    priority: str
    sourceClassification: str
    acceptanceCriteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementsRegistry:
    """
    The frozen, canonical FR/NFR id -> meaning mapping built ONCE, right
    after the requirements section is generated and validated, and handed
    to every dependent section's prompt (useCases, modulesArchitecture,
    database, uiApi, testingSecurity) so they all agree on what each id
    actually means.

    Root cause this exists to close: every section prompt used to be built
    solely from the same static project-facts context, independently of one
    another (see _generate_llm_sections' old docstring, and
    se_documentation_relationship_fallback.py's sibling fix for the
    analogous entityRelationships problem) -- so nothing stopped
    requirements from deciding "FR-02 = Symptom Description Submission"
    while testingSecurity, generated as a completely separate call with no
    visibility into that choice, independently guessed "FR-02 = Login" for
    its own test cases. Every id referenced was syntactically real (both
    sections used the FR-01..FR-16 numbering scheme), so structural
    validation never caught it -- the meanings had just drifted apart.
    Injecting this registry's compact rendering into every dependent
    prompt is what actually fixes that: the ids stop being ambiguous.
    """

    entries: tuple[RequirementsRegistryEntry, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(entry.id for entry in self.entries)

    def render_compact(self) -> str:
        """Deliberately terse -- id and title only, no long prose per
        entry -- so this doesn't blow up every dependent section's prompt
        budget just to keep ids unambiguous."""
        if not self.entries:
            return "CANONICAL REQUIREMENTS -- THESE IDS ARE IMMUTABLE\n(none generated; do not reference any requirement id)"
        lines = ["CANONICAL REQUIREMENTS -- THESE IDS ARE IMMUTABLE", ""]
        for entry in self.entries:
            lines.append(f"{entry.id} | {entry.title}")
        return "\n".join(lines)

    def render_with_acceptance_criteria(self) -> str:
        """Richer rendering used only by the testingSecurity prompt, which
        can use each requirement's own acceptance criteria to generate
        specific positive/negative tests instead of guessing an FR id --
        see this task's Testing prompt-change requirement."""
        if not self.entries:
            return "CANONICAL REQUIREMENTS -- THESE IDS ARE IMMUTABLE\n(none generated; do not reference any requirement id)"
        lines = ["CANONICAL REQUIREMENTS -- THESE IDS ARE IMMUTABLE (with acceptance criteria)", ""]
        for entry in self.entries:
            lines.append(f"{entry.id} | {entry.title}")
            for criterion in entry.acceptanceCriteria[:4]:
                lines.append(f"  - {criterion}")
        return "\n".join(lines)


_REGISTRY_REFERENCE_RULES = """
Reference rules for the canonical requirements above:
- Never redefine an id -- each id above has exactly the meaning shown, nothing else.
- Never invent another meaning for an existing id.
- Only reference ids listed above (FR-xx / NFR-xx); never invent a new requirement id.
- If no requirement genuinely applies, use [] for that reference list.
- Do not force a relationship just to increase coverage -- a wrong reference is worse
  than no reference.
"""


@dataclass
class SectionCallResult:
    """
    The complete, self-contained outcome of ONE section's provider-chain
    attempt, returned by a worker running on the bounded ThreadPoolExecutor.
    Deliberately carries everything the caller needs (including provenance
    and timing) so nothing about a concurrent section call is ever read
    from or written to shared `self.*` instance state -- see
    _call_section_concurrent_safe, which builds this purely from local
    values and its own arguments.
    """

    section_key: str
    launch_order: int  # position in the canonical-order launch sequence (1-based) -- the ordered-queue analog of a fixed "wave number"
    success: bool
    data: Optional[Dict[str, Any]]
    provider: Optional[str]
    model: Optional[str]
    provenance: str  # "provider" | "fallback"
    error_code: Optional[str]
    error_message: Optional[str]
    start_time: float  # time.monotonic()
    end_time: float
    duration: float
    configured_timeout: float
    effective_timeout: float
    remaining_writer_budget_at_start: float


class WriterBudgetExceededError(Exception):
    """
    Raised when the Writer's reserved deadline (writer_deadline = global
    deadline - the semantic-review reserve, see routers/se_documentation.py)
    is reached before every queued core section could even be ATTEMPTED --
    not a per-section content/provider failure (that still falls back to
    per-section deterministic content, caught downstream by the router's
    existing strict core-fallback rejection), but a genuine "ran out of time
    to even try" condition.

    Deliberately a typed exception rather than a mutable agent-instance
    flag (e.g. self.writer_budget_exceeded = True) -- an instance attribute
    would be unsafe to read/write if this agent were ever reused across
    concurrent requests; this exception is caught ONLY by the SE
    Documentation router (see se_documentation.py), which must reject the
    candidate outright: no core deterministic fallback, no partial
    document, no persistence, previous accepted document preserved.
    """

    def __init__(self, missing_sections: List[str], completed_sections: List[str]):
        self.missing_sections = missing_sections
        self.completed_sections = completed_sections
        super().__init__(
            f"Writer budget exceeded before {missing_sections} could be attempted "
            f"(completed: {completed_sections})."
        )


class SEDocumentationOrchestratorAgent:
    def __init__(self):
        # Switched from "high" (anthropic/claude-opus-4-8, expensive) to a
        # dedicated "se_documentation" tier -- same model as "standard"
        # (meta-llama/Llama-3.3-70B-Instruct-Turbo, kept for cost), but with
        # its own DeepInfra per-call timeout (180s, see
        # _DEEPINFRA_TIER_TIMING["se_documentation"] in llm_provider.py) sized
        # for this agent's actual ~6500-token sections instead of "standard"'s
        # shared 60s default. Project Roadmap and the Idea Generator still use
        # "high"; market needs/project DNA/market footprint still use
        # "standard" unchanged.
        self.provider_chain = ProviderChain(tier="se_documentation")
        self.last_llm_used = False
        self.last_error: Optional[str] = None
        self.last_raw_llm_response: Optional[str] = None
        self.last_provider: Optional[str] = None
        self.last_model_used: Optional[str] = None
        # Per-section provenance ("provider" | "fallback") for the most
        # recent generate() call -- lets a single failed/rate-limited
        # section fall back to detailed, project-specific deterministic
        # content for JUST that section instead of discarding every other
        # section that DID succeed (the verified "one failed call collapses
        # the whole document to a 4-FR generic fallback" bug).
        self.section_provenance: Dict[str, str] = {}

    def generate(
        self,
        request: SEDocumentationRequest,
        *,
        deadline: Optional[float] = None,
    ) -> SEDocumentationDto:
        """
        ``deadline`` is an absolute time.monotonic() timestamp, not a
        duration. When supplied (the router computes ONE deadline and passes
        it here AND into ReviewPipeline.run(), see routers/se_documentation.py)
        it governs every section call directly -- it is never recomputed or
        reset partway through. When omitted (e.g. a direct/test caller),
        falls back to a fresh _SECTIONS_TIME_BUDGET_SECONDS-wide budget
        starting now, matching the previous behavior exactly.
        """
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None
        self.section_provenance = {}

        facts = build_project_facts(request)

        try:
            llm_sections = self._generate_llm_sections(request, facts, deadline=deadline)
        except WriterBudgetExceededError:
            # Must reach the router uncaught -- a genuine "ran out of time
            # to even attempt every core section" is never masked as a
            # normal per-section provider failure/fallback. See
            # WriterBudgetExceededError's docstring for the full
            # propagation path (nothing between here and the router
            # catches broad Exception around this call chain).
            raise
        except Exception as e:
            self.last_error = str(e)
            llm_sections = {}

        # used_fallback now means "not even one section reached a real
        # provider" -- a partial result (some sections from a provider, some
        # from fallback) is still real, usable, LLM-assisted output, just
        # honestly labeled per-section via sectionProvenance below.
        self.last_llm_used = any(status == "provider" for status in self.section_provenance.values())
        used_fallback = not self.last_llm_used

        return self._assemble_documentation(request, facts, llm_sections, used_fallback=used_fallback)

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(self, request: SEDocumentationRequest) -> SEDocumentationDto:
        """
        Public entry point for the deterministic fallback documentation --
        the same template-based path generate() already falls back to
        internally when any LLM section call fails, exposed publicly so
        routers never reach into a private method (matches
        ProjectRoadmapAgent.build_safe_fallback).
        """
        facts = build_project_facts(request)
        return self._assemble_documentation(request, facts, {}, used_fallback=True)

    def generate_candidate(
        self,
        request: SEDocumentationRequest,
        *,
        deadline: Optional[float] = None,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate() end to
        end (sequential LLM section calls -> deterministic assembly) rather
        than duplicating it, then wraps the result as an LLMResult so it can
        flow through guarded_call like any other LLM stage.

        ``deadline`` here is the WRITER's own deadline (writer_deadline =
        global_deadline - the semantic-review reserve), NOT the same value
        passed to ReviewPipeline.run() -- the router computes both from one
        shared global_deadline and passes writer_deadline here, global_deadline
        unchanged to the pipeline. See routers/se_documentation.py.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        generate() had to fall back internally (self.last_llm_used is False,
        meaning at least one of the section calls failed), since in that
        case there is no real candidate to review; the router should use
        build_safe_fallback() directly instead.

        Propagates WriterBudgetExceededError uncaught (see that exception's
        docstring) -- neither this method nor generate() catches it; it must
        reach the router, which is the only place that decides how to
        respond to a genuine budget exhaustion (reject, never fall back).
        """
        result = self.generate(request, deadline=deadline)

        if not self.last_llm_used:
            return None

        return LLMResult(
            ok=True,
            provider=self.last_provider or "unknown",
            model=self.last_model_used,
            text="",
            data=result.model_dump(),
        )

    # =========================================================================
    # LLM section generation -- every call shares the same canonical
    # ProjectFacts context block, so every section reads the same facts about
    # the SELECTED project instead of each call independently reinterpreting
    # the raw request fields.
    # =========================================================================

    # Fallback-only default WRITER budget, used solely when no external
    # deadline is supplied (e.g. a direct/test caller) -- the normal
    # production path never reads this: the router always computes
    # writer_deadline = global_deadline (1200s) - the semantic-review
    # reserve (300s) = 900s from request start, and passes it in explicitly.
    # 900 is used here to match that real production value.
    #
    # History: 180s -> 600s -> 960s (a single deadline shared by section
    # generation AND review) as real measurements came in -- after moving to
    # the "se_documentation" tier (Llama-3.3-70B via DeepInfra), a single
    # ~6500-token section was measured live at 121s, and a LIVE END-TO-END
    # RUN with the OLD single-960s-deadline design measured the Writer stage
    # alone (7 sequential sections, 2 of which needed a full 180s DeepInfra
    # timeout before falling back to Groq) taking ~967s -- already past the
    # 960s deadline before ReviewPipeline's semantic Reviewer was ever
    # called (confirmed live: the persisted result was status=
    # "review_unavailable" with reviewer_provider/reviewer_model both null).
    # This is the exact defect the ordered bounded queue (max 2 concurrent
    # section calls) plus the separate 300s semantic-review RESERVE fixes --
    # see _generate_llm_sections and routers/se_documentation.py.
    _SECTIONS_TIME_BUDGET_SECONDS = 900.0

    def _generate_llm_sections(
        self,
        request: SEDocumentationRequest,
        facts: ProjectFacts,
        *,
        deadline: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Two-phase generation, NOT the fully-independent single bounded queue
        this method used to run: "requirements" is generated FIRST, alone,
        and every other section's prompt is only built AFTER requirements
        resolves, once its FR/NFR ids and meanings are frozen into a
        RequirementsRegistry (see that class' docstring for the exact defect
        this closes -- e.g. testingSecurity independently guessing "FR-02 =
        Login" while requirements had actually defined "FR-02 = Symptom
        Description Submission", both syntactically valid so structural
        validation never caught it). Phase 2 (useCases, modulesArchitecture,
        database, uiApi, testingSecurity, and conditionally aiReport) then
        runs through the SAME ordered-bounded-queue mechanism as before (at
        most _MAX_CONCURRENT_SECTION_CALLS (2) calls in flight at once, next
        queued section starts the instant either active call finishes),
        just starting from a registry-aware prompt set instead of the
        static one. Final aggregation still always follows
        _CORE_SECTION_QUEUE's canonical order regardless of completion
        order.

        ``deadline`` here is the WRITER's own deadline (writer_deadline =
        global_deadline - the semantic-review reserve; computed once by the
        router in routers/se_documentation.py and passed here unchanged --
        never the pipeline's overall deadline directly, and never
        recomputed partway through). When omitted (e.g. a direct/test
        caller), falls back to a fresh _SECTIONS_TIME_BUDGET_SECONDS-wide
        budget starting now.

        Raises WriterBudgetExceededError when writer_deadline is reached
        before every queued section could even be ATTEMPTED (see that
        exception's docstring) -- a section that WAS attempted and failed
        for a genuine content/provider reason still falls back to
        per-section deterministic content exactly as before, unchanged.
        """
        context = facts_context_text(facts)
        writer_deadline = (
            deadline if deadline is not None else time.monotonic() + self._SECTIONS_TIME_BUDGET_SECONDS
        )
        # No concurrently-running worker ever reads this -- every worker
        # receives writer_deadline as an explicit argument instead. Kept
        # only as a convenience for any future single-threaded caller.
        self._sections_deadline = writer_deadline

        queue_order = list(_CORE_SECTION_QUEUE)
        if facts.ai_involved:
            queue_order.append("aiReport")

        results: Dict[str, SectionCallResult] = {}
        launch_counter = 0

        # ------------------------------------------------------------------
        # Phase 1: requirements alone, blocking. Every other section's
        # prompt depends on this section's resolved output (via the
        # registry built right after), so nothing else may be launched
        # until this single call returns (or the deadline is already gone).
        # ------------------------------------------------------------------
        requirements_prompt_text, requirements_max_tokens = self._build_section_prompts(context, facts)["requirements"]

        pending = ["requirements"]
        if writer_deadline - time.monotonic() >= _MIN_SECONDS_PER_SECTION_ATTEMPT:
            launch_counter += 1
            results["requirements"] = self._call_section_concurrent_safe(
                "requirements", requirements_prompt_text, requirements_max_tokens,
                writer_deadline, launch_counter,
            )
            pending = []

        if pending:
            logger.warning(
                "se_documentation.writer_budget_exceeded missing=%s completed=%s overrun_seconds=%.1f",
                pending, [], time.monotonic() - writer_deadline,
            )
            raise WriterBudgetExceededError(missing_sections=list(queue_order), completed_sections=[])

        # ------------------------------------------------------------------
        # Freeze the registry from whichever requirements content is about
        # to be used -- real provider output, or deterministic fallback
        # content if that single call itself failed -- so every dependent
        # prompt below always sees a coherent, immutable id->meaning
        # mapping, never an empty/ambiguous one.
        # ------------------------------------------------------------------
        requirements_record = results["requirements"]
        if requirements_record.success:
            requirements_raw = requirements_record.data or {}
        else:
            requirements_raw = {}
        frs_for_registry = self._requirements_or_fallback(
            requirements_raw.get("functionalRequirements"),
            self._fallback_functional_requirements(facts),
        )
        nfrs_for_registry = self._requirements_or_fallback(
            requirements_raw.get("nonFunctionalRequirements"),
            self._fallback_nonfunctional_requirements(facts),
        )
        registry = self._build_requirements_registry(frs_for_registry, nfrs_for_registry)

        prompts = self._build_section_prompts(context, facts, registry=registry)

        # ------------------------------------------------------------------
        # Phase 2: every remaining section, through the same bounded-queue
        # mechanism as before, now registry-aware. launch_counter continues
        # from Phase 1 so provenance/logging ordering stays monotonic.
        # ------------------------------------------------------------------
        pending = [key for key in queue_order if key != "requirements"]
        in_flight: Dict[Future, str] = {}

        def _launch_next_if_possible(executor: ThreadPoolExecutor) -> None:
            nonlocal launch_counter
            while pending and len(in_flight) < _MAX_CONCURRENT_SECTION_CALLS:
                remaining = writer_deadline - time.monotonic()
                if remaining < _MIN_SECONDS_PER_SECTION_ATTEMPT:
                    break
                key = pending.pop(0)
                launch_counter += 1
                prompt_text, max_tokens = prompts[key]
                future = executor.submit(
                    self._call_section_concurrent_safe,
                    key, prompt_text, max_tokens, writer_deadline, launch_counter,
                )
                in_flight[future] = key

        with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SECTION_CALLS) as executor:
            _launch_next_if_possible(executor)
            while in_flight:
                done, _still_pending = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                for finished in done:
                    key = in_flight.pop(finished)
                    results[key] = finished.result()
                _launch_next_if_possible(executor)
            # Exiting the `with` block joins/awaits any last stragglers
            # (there should be none left once `in_flight` is empty) before
            # the executor's own threads are torn down.

        if pending:
            # writer_deadline was reached before these could even be
            # attempted -- distinct from a section that WAS attempted and
            # failed for a real content/provider reason (handled below,
            # unchanged from before). No new task is launched past this
            # point; nothing here waits further, since a "pending" section
            # by definition was never submitted to the executor at all.
            completed = [key for key in queue_order if key in results and results[key].success]
            logger.warning(
                "se_documentation.writer_budget_exceeded missing=%s completed=%s overrun_seconds=%.1f",
                pending, completed, time.monotonic() - writer_deadline,
            )
            raise WriterBudgetExceededError(missing_sections=list(pending), completed_sections=completed)

        self._update_last_fields_from_results(results, queue_order)

        sections: Dict[str, Any] = {}
        for key in queue_order:
            record = results.get(key)
            if record is not None and record.success:
                sections[key] = record.data
                self.section_provenance[key] = "provider"
            else:
                self.section_provenance[key] = "fallback"
                if record is not None and record.error_message:
                    self.last_error = self.last_error or record.error_message

            if record is not None:
                logger.info(
                    "se_documentation.section_result key=%s launch_order=%d provenance=%s "
                    "provider=%s model=%s duration=%.1fs configured_timeout=%.1fs "
                    "effective_timeout=%.1fs remaining_writer_budget_at_start=%.1fs",
                    key, record.launch_order, "provider" if record.success else "fallback",
                    record.provider, record.model, record.duration,
                    record.configured_timeout, record.effective_timeout,
                    record.remaining_writer_budget_at_start,
                )

        return sections

    def _build_requirements_registry(
        self, frs: List[RequirementDto], nfrs: List[RequirementDto],
    ) -> RequirementsRegistry:
        """Freezes the FR/NFR id -> meaning mapping from whichever
        requirements content is about to be used (real provider output, or
        deterministic fallback content if that call itself failed/timed
        out -- either way, dependent sections get a coherent, immutable
        registry, never an empty/ambiguous one). See RequirementsRegistry's
        own docstring for why this exists."""
        entries = tuple(
            RequirementsRegistryEntry(
                id=req.id,
                title=req.title,
                description=(req.description or "")[:160],
                priority=req.priority,
                sourceClassification=req.sourceClassification,
                acceptanceCriteria=tuple(req.acceptanceCriteria[:4]),
            )
            for req in (*frs, *nfrs)
        )
        return RequirementsRegistry(entries=entries)

    def _build_section_prompts(
        self, context: str, facts: ProjectFacts,
        *, registry: Optional[RequirementsRegistry] = None,
    ) -> Dict[str, "tuple[str, int]"]:
        """
        Every prompt string below is UNCHANGED text from the previous
        sequential implementation, aside from the `{registry_block}` /
        `{registry_block_with_ac}` insertions this method now splices into
        every DEPENDENT section's prompt (useCases, modulesArchitecture,
        database, uiApi, testingSecurity) when `registry` is supplied --
        never into "requirements" itself (which DEFINES the ids) or
        "aiReport" (which never references a requirement id at all).
        `registry=None` (the default, and every call before this parameter
        existed) renders both blocks as empty strings, leaving every prompt
        byte-identical to before. Returns {key: (prompt, max_tokens)}.
        """
        prompts: Dict[str, "tuple[str, int]"] = {}
        registry_block = f"\n{registry.render_compact()}\n{_REGISTRY_REFERENCE_RULES}\n" if registry is not None else ""
        registry_block_with_ac = (
            f"\n{registry.render_with_acceptance_criteria()}\n{_REGISTRY_REFERENCE_RULES}\n"
            if registry is not None else ""
        )

        prompts["requirements"] = (f"""
Return ONLY valid JSON.

{context}

Generate software engineering requirements for THIS project only.

JSON shape:
{{
  "functionalRequirements": [
    {{
      "id": "FR-01", "title": "", "description": "", "rationale": "",
      "primaryActor": "", "preconditions": [], "trigger": "", "inputs": [],
      "systemBehavior": "", "outputs": [], "businessRules": [], "validationRules": [],
      "priority": "High", "acceptanceCriteria": [], "relatedUseCaseIds": [],
      "relatedModuleIds": [], "sourceClassification": "confirmed", "source": "Selected idea"
    }}
  ],
  "nonFunctionalRequirements": [
    {{
      "id": "NFR-01", "title": "", "description": "", "category": "performance",
      "measurableTarget": "Proposed acceptance target: ...", "rationale": "",
      "verificationMethod": "", "priority": "High", "relatedComponents": [],
      "sourceClassification": "confirmed", "source": "Software quality"
    }}
  ]
}}

Rules:
- Generate between 12 and 20 functional requirements, scaled to this project's actual
  scope and complexity -- do not pad with unrelated or duplicate requirements. Cover every
  distinct confirmed feature/capability, not just authentication and generic record CRUD.
- Generate between 8 and 14 non-functional requirements covering the categories most
  relevant to this project (performance, availability, security, privacy, usability,
  maintainability, scalability, reliability, accessibility, compatibility, recoverability,
  auditability, and -- if this project has an AI component -- AI safety/explainability) --
  only include categories that make sense for this project.
- Every requirement must be atomic, testable, and specific to this project. Avoid vague
  wording like "user-friendly interface" -- state an observable, testable behavior instead.
- acceptanceCriteria must contain multiple (2+) measurable checks, never empty.
- Every measurableTarget must be a concrete number/threshold. If no target was confirmed by
  the student, prefix it with "Proposed acceptance target:" rather than presenting it as fact.
- Do not invent unrelated features (multilingual support, support tickets, external APIs,
  university credential integration, human escalation, etc.) unless implied by the project
  facts above; if you do include one of these, set sourceClassification to "assumption".
""", 13000)

        prompts["useCases"] = (f"""
Return ONLY valid JSON.

{context}
{registry_block}
Generate detailed use cases and edge cases for THIS project only.

JSON shape:
{{
  "useCases": [
    {{
      "id": "UC-01", "title": "", "actor": "", "goal": "", "trigger": "",
      "supportingActors": [], "preconditions": [], "mainFlow": ["1. ...", "2. ...", "3. ..."],
      "alternativeFlow": [], "exceptionFlows": [], "postconditions": [], "dataUsed": [],
      "securityConsiderations": "", "importance": "High", "sourceClassification": "confirmed",
      "relatedRequirements": ["FR-01"]
    }}
  ],
  "edgeCases": [
    {{
      "id": "EC-01", "scenario": "", "expectedHandling": "", "severity": "Medium",
      "recoveryAction": "", "userMessage": "", "loggingRequirement": "",
      "testScenario": "", "affectedUseCases": ["UC-01"], "relatedRequirement": "FR-01",
      "sourceClassification": "confirmed"
    }}
  ]
}}

Rules:
- Generate 8 to 15 use cases and 10 to 18 edge cases, scaled to this project's scope.
- The actor for every use case must be an external user or external system -- never the
  system's own AI/chat component itself acting as the actor.
- mainFlow must normally contain 5 to 12 meaningful numbered steps (not a single line).
- alternativeFlow must not be empty when a realistic alternative exists.
- exceptionFlows must explain what failed, what the actor sees, what is logged, and
  whether retry is possible.
- relatedRequirements / relatedRequirement must only reference requirement ids that make
  sense for this project (FR-xx or NFR-xx).
- Do not repeat the same edge case scenario under multiple ids.
""", 13000)

        prompts["modulesArchitecture"] = (f"""
Return ONLY valid JSON.

{context}
{registry_block}
Generate system modules and architecture details for THIS project only.

JSON shape:
{{
  "systemModules": [
    {{
      "id": "MOD-01", "name": "", "responsibility": "", "inputs": [], "outputs": [],
      "dependencies": [], "exposedInterfaces": [], "failureBehavior": "",
      "sourceClassification": "confirmed", "relatedRequirements": ["FR-01"]
    }}
  ],
  "architecture": {{
    "components": ["short name: responsibility"],
    "communicationPaths": ["Component A -> Component B: what is exchanged"],
    "trustBoundaries": [],
    "dataFlow": "",
    "authenticationFlow": "",
    "errorHandling": "",
    "deploymentAssumptions": "",
    "scalabilityConsiderations": "",
    "explanation": ""
  }}
}}

Rules:
- Generate 6 to 12 implementation modules (backend/service-level components), not UI screens.
- Only reference the technologies listed as confirmed/assumed in the project facts above --
  never assume a technology stack that was not listed.
- Explain real responsibilities and data flow (e.g. "the query-processing module validates
  input, calls the AI/NLP service for intent classification, and retrieves the matching
  knowledge article") -- do not use a vague one-line architecture description.
""", 9000)

        prompts["database"] = (f"""
Return ONLY valid JSON.

{context}
{registry_block}
Generate a detailed database design for THIS project only.

JSON shape:
{{
  "databaseEntities": [
    {{
      "entityId": "ENT-01", "name": "", "purpose": "",
      "fields": [
        {{
          "name": "Id", "dataType": "int", "nullable": false, "defaultValue": "",
          "description": "", "constraints": "primary key", "isSensitive": false,
          "isPrimaryKey": true, "isForeignKey": false, "referencedEntity": "", "referencedField": ""
        }}
      ],
      "primaryKey": "Id", "foreignKeys": ["ExampleFkField -> OtherEntity.Id"], "uniqueConstraints": [], "indexes": [],
      "validationConstraints": [], "sensitiveFields": [],
      "relatedRequirementIds": ["FR-01"], "sourceClassification": "confirmed"
    }}
  ],
  "entityRelationships": [
    {{"fromEntity": "OtherEntity", "toEntity": "ExampleEntity", "type": "one-to-many", "description": "One OtherEntity has many ExampleEntity records."}}
  ]
}}

Rules:
- Generate 7 to 15 entities that are actually justified by this project's confirmed
  features -- if the project involves a knowledge base/FAQ, intents, training phrases,
  support tickets, feedback, unanswered-query review, roles, or configurable settings,
  include the corresponding entity.
- Every entity needs at least 3 meaningful, domain-specific fields (unless it is a pure
  many-to-many junction table) and exactly one primary key. Never leave "fields" empty,
  and never use only generic filler fields like Id/CreatedAt/Description when the entity
  clearly needs real domain fields (e.g. Conversation needs Status/StartedAt/LastMessageAt;
  Message needs SenderType/Content/DetectedIntent/ConfidenceScore).
- Every foreign key must reference a field on another entity in this same list, and the
  referenced field's dataType must match. For EVERY foreign key: mark the referencing
  field's isForeignKey=true with referencedEntity/referencedField set, AND add a matching
  "ColumnName -> Entity.Field" string to that entity's own top-level "foreignKeys" list
  (do not leave "foreignKeys" as an empty list when the entity has any FK field), AND add
  a corresponding row to the top-level "entityRelationships" array. All three must agree
  with each other for the same relationship -- never populate only one of them.
- Password fields must be named "PasswordHash" (a hash), never "Password" or a raw value.
- Every field needs a clear purpose -- no filler fields.
- Set isSensitive true for passwords/personal data and list them in sensitiveFields.
- relatedRequirementIds must reference real FR/NFR ids this entity supports.
""", 12000)

        prompts["uiApi"] = (f"""
Return ONLY valid JSON.

{context}
{registry_block}
Generate the user-facing screens (never development/backend modules) and, only if this
project involves external APIs or third-party integrations, the API integration points.

JSON shape:
{{
  "uiScreens": [
    {{
      "screenId": "UI-01", "name": "", "authorizedRoles": [], "purpose": "",
      "mainComponents": [], "userActions": [], "validationRules": [],
      "loadingState": "", "emptyState": "", "errorState": "", "successState": "",
      "accessibilityNotes": "", "relatedUseCases": ["UC-01"], "relatedRequirements": ["FR-01"],
      "sourceClassification": "confirmed"
    }}
  ],
  "apiIntegrationPoints": [
    {{
      "apiId": "API-01", "name": "", "method": "POST",
      "endpoint": "(conceptual name, not a guessed exact route)",
      "purpose": "", "requestSummary": "", "responseSummary": "", "authentication": "",
      "requestFields": [], "responseFields": [], "timeoutBehavior": "",
      "relatedRequirements": ["FR-01"], "sourceClassification": "assumption"
    }}
  ]
}}

Rules:
- uiScreens must be real user-facing screens/pages (Login, Dashboard, Chat, etc.) --
  never a backend module, service, or "Database Module"/"Integration Module"/"Testing Module".
- Generate 6 to 14 screens covering every confirmed user-facing feature (including
  administrative/management screens like knowledge-base management, ticket tracking,
  feedback, analytics, configuration, or user/role management when those features are
  confirmed for this project). Each screen's states (loading/empty/error/success) must
  describe what actually happens for THIS screen, not a generic placeholder.
- Only generate apiIntegrationPoints entries if the project facts mention external
  APIs/integrations; otherwise return an empty apiIntegrationPoints list.
- Do not guess an exact route path with confidence -- use a conceptual endpoint name and
  set sourceClassification to "assumption" unless the exact route was confirmed.
- relatedRequirements must reference real FR/NFR ids.
""", 12000)

        prompts["testingSecurity"] = (f"""
Return ONLY valid JSON.

{context}
{registry_block_with_ac}
Generate a testing plan and security/privacy requirements for THIS project only.

JSON shape:
{{
  "testingPlan": [
    {{
      "id": "TC-01", "title": "", "type": "Unit", "preconditions": [],
      "testData": ["concrete example input value"],
      "steps": ["1. ...", "2. ...", "3. ..."], "expectedResult": "", "passCriteria": "",
      "negativeCase": false, "automationCandidate": true, "priority": "High",
      "relatedRequirements": ["FR-01"], "relatedUseCaseIds": ["UC-01"],
      "sourceClassification": "confirmed"
    }}
  ],
  "securityAndPrivacy": [
    {{"category": "authentication", "requirement": "", "rationale": ""}}
  ]
}}

Rules:
- Generate 16 to 28 test cases and ensure EVERY functional requirement listed in the
  requirements context above is covered by at least one test case's relatedRequirements.
- Every high-priority functional requirement should have both a positive test and a
  negative-case test (set negativeCase true for the negative one).
- Never write a generic expectedResult like "works correctly" or "meets standards" --
  state the specific observable outcome, and always fill passCriteria with the concrete
  condition that makes the test pass.
- testData must contain a concrete example value, never be left empty.
- Cover unit, integration, API, database, security, usability, performance, and failure
  recovery categories as relevant; add AI evaluation tests (intent accuracy, answer
  groundedness, low-confidence behavior, provider fallback) only if this project has a
  confirmed/inferred AI component.
- Generate 5 to 10 security/privacy requirements covering authentication, authorization,
  input validation, secret management, session management, and data protection as relevant
  to this project. Do not claim GDPR/HIPAA compliance unless the project facts require it.
""", 14000)

        if facts.ai_involved:
            prompts["aiReport"] = (f"""
Return ONLY valid JSON.

{context}

This project has a confirmed or inferred AI/ML/NLP/data-science component. Generate an
AI/Data Science technical report for THIS project only.

JSON shape:
{{
  "aiTechnicalReport": {{
    "problemDefinition": "", "taskType": "", "inputData": "", "output": "",
    "modelOrApproach": "", "trainingVsInference": "", "retrievalStrategy": "",
    "fallbackStrategy": "", "confidenceHandling": "", "evaluationMetrics": [],
    "datasetNeeds": "", "biasAndSafetyRisks": "", "hallucinationMitigation": "",
    "monitoring": "", "limitations": ""
  }}
}}

Rules:
- Every field must be filled with real content -- never leave a field empty. If a detail is
  genuinely unresolved, state that explicitly in the field's own text (e.g.
  "Not yet confirmed; treat as pending student decision.") rather than leaving it blank.
- Your "approach"/"modelOrApproach" MUST match the canonical AI approach given above
  ({facts.technical_profile.ai_approach}) -- do not describe a different approach.
- If AI provider type is "external_api", say so explicitly in "trainingVsInference" and
  do NOT call API prompting "model training" or "fine-tuning".
- If training mode is "local_supervised_training", describe it as local supervised
  training/model fitting, never as calling a third-party API.
- Do not mention retrieval-augmented generation (RAG) or vector databases unless the
  canonical AI approach above is "rag" or "hybrid".
- evaluationMetrics must be relevant to the actual AI task type described.
""", 6000)

        return prompts

    def _call_section_concurrent_safe(
        self,
        key: str,
        prompt: str,
        max_tokens: int,
        writer_deadline: float,
        launch_order: int,
    ) -> SectionCallResult:
        """
        Runs on a worker thread from the bounded ThreadPoolExecutor.
        THREAD-SAFETY CONTRACT: never reads or writes self.last_provider /
        self.last_model_used / self.last_raw_llm_response / self.last_error
        / self._sections_deadline, and never raises -- every outcome
        (success, provider failure, or unexpected exception) is captured
        into the returned SectionCallResult, which the main orchestrator
        thread aggregates AFTER every wave/queue step. self.provider_chain
        and its underlying provider objects ARE shared across concurrent
        calls, but they are safe to call concurrently: each generate_json
        call builds a fresh SDK client per attempt and never mutates
        instance state on the provider objects themselves.
        """
        start = time.monotonic()
        remaining_at_start = writer_deadline - start
        configured_timeout = self._primary_provider_configured_timeout()
        effective_timeout = max(0.0, min(configured_timeout, remaining_at_start))

        try:
            result = self.provider_chain.generate_json(
                prompt,
                use_search=False,
                max_tokens=max_tokens,
                deadline=writer_deadline,
                cap_timeout_to_deadline=True,
            )
        except Exception as e:  # never propagate -- always return a result
            end = time.monotonic()
            return SectionCallResult(
                section_key=key, launch_order=launch_order, success=False, data=None,
                provider=None, model=None, provenance="fallback",
                error_code="unexpected_exception", error_message=str(e),
                start_time=start, end_time=end, duration=end - start,
                configured_timeout=configured_timeout, effective_timeout=effective_timeout,
                remaining_writer_budget_at_start=remaining_at_start,
            )

        end = time.monotonic()

        if result.ok and isinstance(result.data, dict):
            return SectionCallResult(
                section_key=key, launch_order=launch_order, success=True, data=result.data,
                provider=(result.provider if result.provider != "none" else None),
                model=result.model, provenance="provider", error_code=None, error_message=None,
                start_time=start, end_time=end, duration=end - start,
                configured_timeout=configured_timeout, effective_timeout=effective_timeout,
                remaining_writer_budget_at_start=remaining_at_start,
            )

        return SectionCallResult(
            section_key=key, launch_order=launch_order, success=False, data=None,
            provider=(result.provider if result.provider != "none" else None),
            model=result.model, provenance="fallback",
            error_code=result.error_category, error_message=result.error,
            start_time=start, end_time=end, duration=end - start,
            configured_timeout=configured_timeout, effective_timeout=effective_timeout,
            remaining_writer_budget_at_start=remaining_at_start,
        )

    def _primary_provider_configured_timeout(self) -> float:
        """The primary (first-tried) provider's own configured timeout --
        read-only, safe to call concurrently. Used purely for instrumentation/
        effective_timeout math, never mutated."""
        providers = getattr(self.provider_chain, "providers", [])
        if providers:
            return float(getattr(providers[0], "timeout_seconds", 180.0) or 180.0)
        return 180.0

    def _update_last_fields_from_results(
        self, results: Dict[str, SectionCallResult], queue_order: List[str],
    ) -> None:
        """
        Runs ONLY on the main orchestrator thread, AFTER every concurrent
        section call has already completed and been collected -- never
        called while a ThreadPoolExecutor worker could still be running.
        Picks the LAST-in-canonical-order successful result, matching the
        previous sequential implementation's "whichever section ran last
        wins" semantics as closely as possible for these backward-compatible
        aggregate fields.
        """
        chosen: Optional[SectionCallResult] = None
        for key in queue_order:
            record = results.get(key)
            if record is not None and record.success:
                chosen = record

        if chosen is not None:
            self.last_provider = chosen.provider
            self.last_model_used = chosen.model
            if chosen.data is not None:
                self.last_raw_llm_response = json.dumps(chosen.data, ensure_ascii=False)[:3000]

    # =========================================================================
    # Deterministic assembly -- every field below is derived from ProjectFacts
    # (the SELECTED project) and the LLM sections, never hardcoded to describe
    # FYPilot itself.
    # =========================================================================

    def _assemble_documentation(
        self,
        request: SEDocumentationRequest,
        facts: ProjectFacts,
        sections: Dict[str, Any],
        used_fallback: bool
    ) -> SEDocumentationDto:
        profile = request.studentProfile or SEDocStudentProfile()

        requirements = sections.get("requirements", {})
        use_case_section = sections.get("useCases", {})
        modules_section = sections.get("modulesArchitecture", {})
        database_section = sections.get("database", {})
        ui_api_section = sections.get("uiApi", {})
        testing_section = sections.get("testingSecurity", {})
        ai_section = sections.get("aiReport", {})

        if used_fallback:
            frs = self._fallback_functional_requirements(facts)
            nfrs = self._fallback_nonfunctional_requirements(facts)
            use_cases = self._fallback_use_cases(facts)
            edge_cases = self._fallback_edge_cases(facts)
            modules = self._fallback_modules(facts)
            entities = self._fallback_entities(facts)
            relationships = self._fallback_relationships(entities)
            tests = self._fallback_tests(facts)
            ui_screens = self._fallback_ui_screens(facts)
            api_points: List[ApiPointDto] = []
            security_items = self._fallback_security(facts)
            architecture = self._fallback_architecture(facts)
        else:
            frs = self._requirements_or_fallback(
                requirements.get("functionalRequirements"),
                self._fallback_functional_requirements(facts),
            )
            nfrs = self._requirements_or_fallback(
                requirements.get("nonFunctionalRequirements"),
                self._fallback_nonfunctional_requirements(facts),
            )
            use_cases = self._use_cases_or_fallback(use_case_section.get("useCases"), facts)
            edge_cases = self._edge_cases_or_fallback(use_case_section.get("edgeCases"), facts)
            modules = self._modules_or_fallback(modules_section.get("systemModules"), facts)
            entities = self._entities_or_fallback(database_section.get("databaseEntities"), facts)
            relationships = self._relationships_or_fallback(
                database_section.get("entityRelationships"), entities
            )
            tests = self._tests_or_fallback(testing_section.get("testingPlan"), facts)
            ui_screens = self._ui_screens_or_fallback(ui_api_section.get("uiScreens"), facts)
            api_points = self._api_points_or_default(ui_api_section.get("apiIntegrationPoints"), facts)
            security_items = self._security_or_fallback(testing_section.get("securityAndPrivacy"), facts)
            architecture = self._architecture_or_fallback(modules_section.get("architecture"), facts)

        # Each LLM section above is generated by an INDEPENDENT call (or
        # independently falls back to deterministic content if just that
        # call fails), so two sections can disagree on id scheme even in a
        # single "used_fallback=False" run. This pass makes ids unique within
        # each list and repairs any cross-section reference that doesn't
        # actually exist, deterministically, so the referential-integrity
        # checks in app/review/registry.py's SEDocumentationCandidateSchema
        # always pass on this agent's own output.
        frs = self._ensure_unique_ids(frs, "FR")
        nfrs = self._ensure_unique_ids(nfrs, "NFR")
        use_cases = self._ensure_unique_ids(use_cases, "UC")
        edge_cases = self._ensure_unique_ids(edge_cases, "EC")
        modules = self._assign_canonical_ids(modules, "MOD")
        tests = self._ensure_unique_ids(tests, "TC")
        entities = self._ensure_unique_entity_names(entities)

        requirement_ids = {req.id for req in frs} | {req.id for req in nfrs}
        self._reconcile_requirement_references(requirement_ids, use_cases, edge_cases, modules, tests)
        self._reconcile_screen_references(requirement_ids, ui_screens)
        self._reconcile_entity_and_api_references(requirement_ids, entities, api_points)
        self._dedupe_reference_lists(use_cases, modules, entities, ui_screens, api_points, tests)
        self._rebuild_requirement_reverse_references(frs, nfrs, use_cases, modules)

        # Deterministic coverage guarantees (section 6/13 of the stabilization
        # spec): a confirmed feature (knowledge base, escalation, feedback,
        # roles, configuration, ...) always gets its required entity/screen
        # even if the independent LLM call for that section forgot it.
        requirement_text = " ".join(fr.description for fr in frs) + " " + " ".join(uc.goal for uc in use_cases)
        entities = self._ensure_entity_coverage(entities, facts, requirement_text, requirement_ids)
        entities = self._normalize_entities(entities)
        entities = self._assign_entity_ids(entities)
        ui_screens = self._ensure_screen_coverage(ui_screens, facts, requirement_text, requirement_ids)
        ui_screens = self._assign_canonical_ids(ui_screens, "UI", id_field="screenId")
        api_points = self._assign_api_ids(api_points)
        tests = self._ensure_test_coverage(tests, frs)

        architecture = self._sanitize_architecture(architecture, facts)
        architecture, ai_report_raw = self._sanitize_ai_and_auth_text(architecture, ai_section.get("aiTechnicalReport"), facts)

        traceability = self._build_traceability(frs, nfrs, use_cases, modules, entities, tests, ui_screens, api_points)

        ai_report = None
        ai_applicable = facts.ai_involved
        if ai_applicable:
            ai_report = self._ai_report_or_fallback(ai_report_raw, facts)

        assumptions = self._collect_assumptions(
            facts, frs, nfrs, use_cases, edge_cases, modules, entities, ui_screens,
            api_points, tests, ai_report, used_fallback,
        )

        stakeholders = [facts.primary_actor] + facts.supporting_actors

        risks = self._build_risks(facts, used_fallback)
        outcomes = self._build_outcomes(facts)
        scope = self._build_scope(facts)

        section_keys = ["requirements", "useCases", "modulesArchitecture", "database", "uiApi", "testingSecurity"]
        if facts.ai_involved:
            section_keys.append("aiReport")

        if used_fallback:
            # Whole-document fallback (build_safe_fallback(), or generate()
            # when literally zero sections reached a provider) -- every
            # expected section is fallback regardless of whatever
            # self.section_provenance currently holds.
            section_provenance = {key: "fallback" for key in section_keys}
        else:
            section_provenance = {key: self.section_provenance.get(key, "fallback") for key in section_keys}

        fallback_section_names = [key for key, status in section_provenance.items() if status == "fallback"]

        warnings = []
        if used_fallback:
            warnings.append(
                "No AI provider returned valid JSON for this project, so every section used "
                "detailed deterministic fallback content instead of AI-generated content."
            )
        elif fallback_section_names:
            warnings.append(
                f"The following section(s) used deterministic fallback content because the AI "
                f"provider did not return valid JSON for them after a retry: {', '.join(fallback_section_names)}. "
                f"All other sections reflect real AI provider output."
            )
        if not profile.skills:
            warnings.append("Student skills were missing, so the documentation used general assumptions for this domain.")
        warnings.extend(self._detect_traceability_integrity_issues(frs, nfrs, use_cases, tests))

        mermaid_erd = self._build_erd(entities, relationships)
        mermaid_class = self._build_class_diagram(entities)
        primary_use_case = self._select_primary_use_case(facts, frs, use_cases)
        activity_diagram = self._build_activity_diagram(facts, primary_use_case, edge_cases)
        sequence_diagram = self._build_sequence_diagram(facts, architecture, primary_use_case, frs, modules, api_points)

        diagram_validation = self._validate_diagrams(
            mermaid_erd, mermaid_class, activity_diagram, sequence_diagram, facts,
        )

        quality = self._compute_quality_assessment(
            facts=facts,
            frs=frs, nfrs=nfrs, use_cases=use_cases, edge_cases=edge_cases,
            modules=modules, entities=entities, ui_screens=ui_screens, tests=tests,
            traceability=traceability, architecture=architecture,
            assumptions=assumptions, used_fallback=used_fallback,
            diagram_validation=diagram_validation,
            ai_report=ai_report, ai_applicable=ai_applicable,
        )

        return SEDocumentationDto(
            projectTitle=facts.title,
            projectOverview=(
                f"{facts.title} is a software system for {facts.primary_actor.lower() if facts.primary_actor else 'its target users'} "
                f"in the {facts.domain} domain. {facts.solution}"
            ),
            problemStatement=facts.problem,
            objectives=facts.objectives,
            stakeholders=stakeholders,
            scope=scope,
            functionalRequirements=frs,
            nonFunctionalRequirements=nfrs,
            useCases=use_cases,
            edgeCases=edge_cases,
            systemModules=modules,
            databaseEntities=entities,
            entityRelationships=relationships,
            mermaidERD=mermaid_erd,
            mermaidClassDiagram=mermaid_class,
            activityDiagram=activity_diagram,
            sequenceDiagram=sequence_diagram,
            architecture=architecture,
            apiIntegrationPoints=api_points,
            uiScreens=ui_screens,
            securityAndPrivacy=security_items,
            testingPlan=tests,
            traceabilityMatrix=traceability,
            risksAndLimitations=risks,
            expectedOutcomes=outcomes,
            assumptions=assumptions,
            aiTechnicalReport=ai_report,
            aiTechnicalReportApplicable=ai_applicable,
            documentationQualityScore=quality.overallScore,
            qualityAssessment=quality,
            consistencyWarnings=warnings,
            sectionProvenance=section_provenance,
        )

    # -------------------------------------------------------------------
    # Scope / risks / outcomes -- project-specific, not FYPilot's own
    # -------------------------------------------------------------------

    def _build_scope(self, facts: ProjectFacts) -> ScopeDto:
        in_scope = [f"Deliver: {item}" for item in facts.objectives]
        if not in_scope:
            in_scope = [f"Deliver the core functionality of {facts.title} for {facts.primary_actor}."]

        return ScopeDto(
            inScope=in_scope,
            outOfScope=[
                "Features not explicitly confirmed in the project's objectives or roadmap.",
                "Production-grade infrastructure hardening beyond what is required for a final year project demo.",
            ],
            futureWork=[
                "Extend the confirmed feature set based on user feedback after initial delivery.",
                "Revisit any item currently labeled as an assumption once the student confirms it.",
            ],
        )

    def _build_risks(self, facts: ProjectFacts, used_fallback: bool) -> List[str]:
        risks = [
            f"AI-generated documentation for {facts.title} requires human (student/supervisor) review before academic submission.",
            "Sections built from assumptions rather than confirmed project facts may need correction once more details are available.",
        ]
        if facts.ai_involved:
            risks.append("AI/LLM-based behavior can be non-deterministic; outputs should be treated as recommendations, not guaranteed-correct answers.")
        if used_fallback:
            risks.append("This version was generated with deterministic fallback content because no AI provider was available; regenerate once a provider is reachable for richer detail.")
        return risks

    def _build_outcomes(self, facts: ProjectFacts) -> List[str]:
        return [
            f"{facts.primary_actor} receives the confirmed functionality described in the objectives.",
            "Requirements are traceable to use cases, modules, database entities, UI screens, and test cases.",
            f"The {facts.title} project has a structured SRS/SDD baseline for supervisor review and further development.",
        ]

    # -------------------------------------------------------------------
    # LLM-output -> DTO conversion (or fallback), per section
    # -------------------------------------------------------------------

    def _use_cases_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[UseCaseDto]:
        try:
            result = [UseCaseDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_use_cases(facts)
        except Exception:
            return self._fallback_use_cases(facts)

    def _edge_cases_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[EdgeCaseDto]:
        try:
            result = [EdgeCaseDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_edge_cases(facts)
        except Exception:
            return self._fallback_edge_cases(facts)

    def _modules_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[ModuleDto]:
        try:
            result = [ModuleDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_modules(facts)
        except Exception:
            return self._fallback_modules(facts)

    def _entities_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[EntityDto]:
        try:
            result = [EntityDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_entities(facts)
        except Exception:
            return self._fallback_entities(facts)

    def _relationships_or_fallback(self, raw: Any, entities: List[EntityDto]) -> List[RelationshipDto]:
        try:
            result = [RelationshipDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_relationships(entities)
        except Exception:
            return self._fallback_relationships(entities)

    def _tests_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[TestCaseDto]:
        try:
            result = [TestCaseDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_tests(facts)
        except Exception:
            return self._fallback_tests(facts)

    def _ui_screens_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[UiScreenDto]:
        try:
            result = [UiScreenDto.model_validate(x) for x in (raw or [])]
            result = [screen for screen in result if not self._looks_like_dev_module(screen.name)]
            return result or self._fallback_ui_screens(facts)
        except Exception:
            return self._fallback_ui_screens(facts)

    def _api_points_or_default(self, raw: Any, facts: ProjectFacts) -> List[ApiPointDto]:
        try:
            return [ApiPointDto.model_validate(x) for x in (raw or [])]
        except Exception:
            return []

    def _security_or_fallback(self, raw: Any, facts: ProjectFacts) -> List[SecurityItemDto]:
        try:
            result = [SecurityItemDto.model_validate(x) for x in (raw or [])]
            return result or self._fallback_security(facts)
        except Exception:
            return self._fallback_security(facts)

    def _architecture_or_fallback(self, raw: Any, facts: ProjectFacts) -> ArchitectureDto:
        try:
            if not raw:
                return self._fallback_architecture(facts)
            data = dict(raw)
            data.setdefault("style", "Layered application architecture")
            data.setdefault("frontend", self._pick_layer(facts, _FRONTEND_KEYWORDS, "Frontend (not specified)"))
            data.setdefault("backend", self._pick_layer(facts, _BACKEND_KEYWORDS, "Backend (not specified)"))
            data.setdefault("database", self._pick_layer(facts, _DB_KEYWORDS, "Database (not specified)"))
            data.setdefault("aiService", ai_service_label(facts.technical_profile))
            data.setdefault("externalServices", [])
            data.setdefault("explanation", data.get("explanation") or "")
            return ArchitectureDto.model_validate(data)
        except Exception:
            return self._fallback_architecture(facts)

    def _ai_report_or_fallback(self, raw: Any, facts: ProjectFacts) -> AiTechnicalReportDto:
        try:
            if raw:
                validated = AiTechnicalReportDto.model_validate(raw)
                if any(v not in (None, "", []) for v in validated.model_dump().values()):
                    return validated
        except Exception:
            pass
        return self._fallback_ai_report(facts)

    def _fallback_ai_report(self, facts: ProjectFacts) -> AiTechnicalReportDto:
        """
        A complete, honest AI technical report built entirely from
        facts.technical_profile's deterministic classification -- every
        field gets a real value (or an explicit "unresolved" statement),
        never an empty placeholder, even when no AI provider is reachable.
        """
        profile = facts.technical_profile

        approach_text = {
            "intent_classification": "Supervised intent classification with knowledge-base lookup for the matched intent.",
            "retrieval_based": "Retrieval of a verified answer from the knowledge base based on the query text.",
            "rag": "Retrieval-augmented generation: relevant knowledge is retrieved and passed to a generative model to compose the answer.",
            "llm_api": "Inference through a pretrained external LLM API; no project-specific model is trained.",
            "hybrid": "A hybrid approach combining retrieval with a generative/external model for answer composition.",
            "rule_based": "Rule-based processing without a learned model.",
            "unresolved": "Not yet confirmed by the student -- viable candidates are intent classification, knowledge-base retrieval, or an external LLM API; none is presented as decided.",
            "none": "Not applicable.",
        }.get(profile.ai_approach, "Not yet confirmed.")

        training_text = {
            "no_local_training": "No project-specific model training occurs; inference uses a pretrained model/API as-is.",
            "local_supervised_training": "The intent classifier is trained/fitted locally on the project's own training-phrase data.",
            "unresolved": "Whether any local training/fitting occurs has not been confirmed by the student.",
        }.get(profile.training_mode, "Not yet confirmed.")

        retrieval_text = {
            "knowledge_base_lookup": "Answers are retrieved from a curated, verified knowledge base rather than generated freely.",
            "vector_retrieval": "Relevant content is retrieved via vector similarity search over an embedded knowledge store.",
            "none": "Not applicable -- this project's AI approach does not retrieve from an external knowledge store.",
            "unresolved": "Not yet confirmed by the student.",
        }.get(profile.retrieval_mode, "Not yet confirmed.")

        return AiTechnicalReportDto(
            problemDefinition=f"{facts.title} needs to interpret an actor's natural-language input and produce a relevant, trustworthy response.",
            taskType="Intent classification / knowledge retrieval" if profile.ai_approach in ("intent_classification", "retrieval_based") else profile.ai_approach.replace("_", " ").title(),
            inputData="The actor's submitted question/query text, and (when applicable) recent conversation context.",
            output="An answer, a clarification request, or an escalation result, together with a confidence indicator.",
            modelOrApproach=approach_text,
            trainingVsInference=training_text,
            retrievalStrategy=retrieval_text,
            fallbackStrategy="When confidence is below the configured threshold or the provider is unavailable, the system asks a clarifying question or escalates rather than guessing.",
            confidenceHandling="Responses below the configured confidence threshold are treated as uncertain and are not presented as a confident answer.",
            evaluationMetrics=["Intent/answer accuracy", "Unresolved-query rate", "Actor feedback rating", "Response time"],
            datasetNeeds="A representative set of past questions/training phrases and their correct intents/answers." if profile.training_mode == "local_supervised_training" else "A curated, verified knowledge base covering the project's domain.",
            biasAndSafetyRisks="Answers are only as good as the underlying knowledge base/training data; gaps or bias there can produce incomplete or skewed responses.",
            hallucinationMitigation="Answers are grounded in the verified knowledge base rather than freely generated where possible; low-confidence cases trigger clarification/escalation instead of a fabricated answer.",
            monitoring="Query logs and unresolved-query review support ongoing quality monitoring.",
            limitations=(
                "This AI technical report reflects the canonical AI approach derived from the project's confirmed text; "
                + ("the exact approach is still unresolved and requires student confirmation." if profile.ai_approach == "unresolved" else "implementation details beyond this canonical profile still require student confirmation.")
            ),
        )

    def _requirements_or_fallback(self, raw: Any, fallback: List[RequirementDto]) -> List[RequirementDto]:
        try:
            result = [RequirementDto.model_validate(x) for x in (raw or [])]
            return result or fallback
        except Exception:
            return fallback

    # -------------------------------------------------------------------
    # Fallback content generators -- deterministic, derived from
    # ProjectFacts (the SELECTED project), never hardcoded FYPilot content.
    # -------------------------------------------------------------------

    def _fallback_features(self, facts: ProjectFacts) -> List[CanonicalFeature]:
        """Cached per-call so every fallback generator (FR/UC/modules/
        entities/screens/tests) derives from the exact same feature list."""
        return derive_canonical_features(facts)

    def _feature_priority(self, feature: CanonicalFeature) -> str:
        return {"core": "High", "supporting": "Medium", "optional": "Low", "proposed": "Low", "out_of_scope": "Low"}.get(feature.scopeStatus, "Medium")

    def _fallback_functional_requirements(self, facts: ProjectFacts) -> List[RequirementDto]:
        features = self._fallback_features(facts)
        requirements: List[RequirementDto] = []

        for index, feature in enumerate(features, start=1):
            actor = feature.actors[0] if feature.actors else facts.primary_actor
            steps = feature.processing or [f"Execute {feature.name.lower()}."]
            requirements.append(
                RequirementDto(
                    id=f"FR-{index:02d}",
                    title=feature.name,
                    description=(
                        f"After {('an authenticated ' + actor.lower()) if actor else 'the actor'} triggers {feature.name.lower()}, "
                        f"the system shall: {'; '.join(steps)}."
                    ),
                    rationale=feature.description,
                    primaryActor=actor,
                    preconditions=["Actor is authenticated."] if feature.name != "Authenticate and Authorize Users" else ["Actor has a registered account."],
                    inputs=feature.inputs or [],
                    systemBehavior="; ".join(steps),
                    outputs=feature.outputs or ["Confirmation of the completed action."],
                    businessRules=feature.businessRules or [],
                    priority=self._feature_priority(feature),
                    source="Assumption" if feature.sourceClassification != "confirmed" else "Confirmed project context",
                    acceptanceCriteria=[
                        f"{step.rstrip('.').capitalize()} is observed." for step in steps[:3]
                    ] or [f"{feature.name} completes without error."],
                    sourceClassification=feature.sourceClassification,
                )
            )

        return requirements

    def _fallback_nonfunctional_requirements(self, facts: ProjectFacts) -> List[RequirementDto]:
        nfrs = [
            RequirementDto(
                id="NFR-01", title="Security", category="security",
                description=f"{facts.primary_actor}s must only access their own data and generated artifacts.",
                measurableTarget="Proposed acceptance target: no cross-account data access in security testing.",
                verificationMethod="Manual authorization test per role.",
                priority="High", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-02", title="Usability", category="usability",
                description="The interface should be clear and require no external training for a first-time user.",
                measurableTarget="Proposed acceptance target: a first-time user completes the primary task without external help.",
                verificationMethod="Usability walkthrough with a representative user.",
                priority="Medium", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-03", title="Reliability", category="reliability",
                description="The system should degrade gracefully (clear error message, no crash) when a dependent service is unavailable.",
                measurableTarget="Proposed acceptance target: 0 unhandled exceptions surfaced to the user during a dependency outage.",
                verificationMethod="Fault-injection test against the dependency.",
                priority="High", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-04", title="Performance", category="performance",
                description="Common user actions should complete within an acceptable response time.",
                measurableTarget="Proposed acceptance target: p95 response time below 3 seconds under normal load.",
                verificationMethod="Load test against the primary endpoint.",
                priority="Medium", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-05", title="Maintainability", category="maintainability",
                description="The codebase should separate UI, services, data access, and any AI/ML component into distinct layers.",
                measurableTarget="Proposed acceptance target: no direct database access from UI code.",
                verificationMethod="Code review against the layering rule.",
                priority="Medium", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-06", title="Auditability", category="auditability",
                description="Security-relevant actions (login, authorization failures, escalations) should be logged with enough context to investigate later.",
                measurableTarget="Proposed acceptance target: every authorization failure produces a log entry with actor id and timestamp.",
                verificationMethod="Log review after a simulated authorization failure.",
                priority="Medium", source="Assumption", sourceClassification="assumption",
            ),
            RequirementDto(
                id="NFR-07", title="Recoverability", category="recoverability",
                description="Persisted data should be recoverable after an unexpected outage without silent data loss.",
                measurableTarget="Proposed acceptance target: no committed transaction is lost after a simulated crash.",
                verificationMethod="Crash/restart test against the database.",
                priority="Medium", source="Assumption", sourceClassification="assumption",
            ),
        ]

        if facts.ai_involved:
            nfrs.extend([
                RequirementDto(
                    id="NFR-08", title="AI provider failure handling", category="AI safety",
                    description="When the configured AI/NLP provider is unavailable or times out, the system shall degrade to a clear fallback message instead of failing silently.",
                    measurableTarget="Proposed acceptance target: 100% of provider timeouts surface a user-facing fallback message within the configured timeout.",
                    verificationMethod="Simulate a provider timeout and confirm fallback behavior.",
                    priority="High", source="Assumption", sourceClassification="assumption",
                ),
                RequirementDto(
                    id="NFR-09", title="Response confidence handling", category="explainability",
                    description="Responses produced with confidence below the configured threshold shall be treated as uncertain (clarification/escalation) rather than presented as a confident answer.",
                    measurableTarget="Proposed acceptance target: 100% of below-threshold responses trigger clarification/escalation instead of a direct answer.",
                    verificationMethod="Test with a query engineered to produce low confidence.",
                    priority="High", source="Assumption", sourceClassification="assumption",
                ),
                RequirementDto(
                    id="NFR-10", title="Unsafe-output prevention", category="AI safety",
                    description="AI-generated content shall be screened before being shown to an actor to reduce the risk of unsafe or inappropriate output.",
                    measurableTarget="Proposed acceptance target: no flagged unsafe response reaches the actor unmodified.",
                    verificationMethod="Adversarial-input test suite.",
                    priority="Medium", source="Assumption", sourceClassification="assumption",
                ),
            ])

        return nfrs

    def _fallback_use_cases(self, facts: ProjectFacts) -> List[UseCaseDto]:
        features = self._fallback_features(facts)
        use_cases: List[UseCaseDto] = []

        for index, feature in enumerate(features, start=1):
            if feature.scopeStatus == "out_of_scope":
                continue

            actor = feature.actors[0] if feature.actors else facts.primary_actor
            steps = feature.processing or [f"Actor triggers {feature.name.lower()}.", "System processes the request.", "System returns the result."]
            main_flow = [f"{i}. {step.rstrip('.')}." for i, step in enumerate(steps, start=1)]

            use_cases.append(
                UseCaseDto(
                    id=f"UC-{index:02d}",
                    title=feature.name,
                    actor=actor,
                    goal=feature.description,
                    trigger=f"{actor} initiates {feature.name.lower()}.",
                    preconditions=["Actor is authenticated."] if feature.name != "Authenticate and Authorize Users" else [f"{actor} has a registered account."],
                    mainFlow=main_flow,
                    alternativeFlow=[f"{len(main_flow)}a. If a precondition is not met, the system shows a specific message and does not proceed."],
                    postconditions=[f"{'; '.join(feature.outputs)} is recorded." if feature.outputs else "The action is recorded."],
                    dataUsed=feature.dataEntities,
                    securityConsiderations="; ".join(feature.securityRequirements) or "Standard authorization checks apply.",
                    importance="High" if feature.scopeStatus == "core" else "Medium",
                    relatedRequirements=[f"FR-{index:02d}"],
                    sourceClassification=feature.sourceClassification,
                )
            )

        return use_cases

    def _fallback_edge_cases(self, facts: ProjectFacts) -> List[EdgeCaseDto]:
        features = {f.name for f in self._fallback_features(facts)}
        cases = [
            EdgeCaseDto(id="EC-01", scenario="Actor submits an empty required field.", expectedHandling="Reject the submission and show a field-specific message.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Actor corrects the field and resubmits.", userMessage="This field is required.", loggingRequirement="Log validation failure at debug level.", testScenario="Submit form with the field blank."),
            EdgeCaseDto(id="EC-02", scenario="Database is temporarily unavailable.", expectedHandling="Show a generic service-unavailable message and avoid data loss.", relatedRequirement="NFR-03", severity="High", recoveryAction="Retry after the database recovers; queue the action if applicable.", userMessage="The service is temporarily unavailable. Please try again shortly.", loggingRequirement="Log the exception with a correlation id.", testScenario="Simulate a database outage during a write."),
            EdgeCaseDto(id="EC-03", scenario="Unauthorized actor attempts to access another actor's record.", expectedHandling="Reject the request with an authorization error.", relatedRequirement="NFR-01", severity="High", recoveryAction="No recovery; request is denied.", userMessage="You do not have permission to view this record.", loggingRequirement="Log the unauthorized attempt with actor id.", testScenario="Request another actor's record id directly."),
            EdgeCaseDto(id="EC-04", scenario="Session expires mid-action.", expectedHandling="Redirect to login and preserve unsaved input where possible.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Actor re-authenticates and resumes.", userMessage="Your session has expired. Please log in again.", loggingRequirement="Log session expiry event.", testScenario="Let the session token expire before submitting a form."),
            EdgeCaseDto(id="EC-05", scenario="Actor submits a duplicate request (e.g. double-click submit).", expectedHandling="Detect the duplicate and avoid creating a second record/transaction.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Return the original result instead of creating a duplicate.", userMessage="This request was already submitted.", loggingRequirement="Log the duplicate submission attempt.", testScenario="Submit the same request twice in quick succession."),
        ]

        if "Submit and Process User Query" in features:
            cases.extend([
                EdgeCaseDto(id="EC-06", scenario="Actor submits an empty question.", expectedHandling="Reject before processing and prompt for real input.", relatedRequirement="FR-01", severity="Low", recoveryAction="Actor enters a real question.", userMessage="Please enter a question.", loggingRequirement="Log at debug level only.", testScenario="Submit a blank/whitespace-only question."),
                EdgeCaseDto(id="EC-07", scenario="Actor submits an extremely long question.", expectedHandling="Reject or truncate with a clear message rather than failing silently.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Actor shortens the question.", userMessage="Your question is too long; please shorten it.", loggingRequirement="Log the rejected length.", testScenario="Submit a question far exceeding the configured length limit."),
                EdgeCaseDto(id="EC-08", scenario="Question contains an unsafe prompt-injection attempt.", expectedHandling="The firewall/screening layer blocks or sanitizes the attempt before it reaches the AI provider.", relatedRequirement="FR-01", severity="High", recoveryAction="Reject the message; do not forward to the AI provider unmodified.", userMessage="This message could not be processed.", loggingRequirement="Log the blocked attempt for security review.", testScenario="Submit a known prompt-injection pattern."),
                EdgeCaseDto(id="EC-09", scenario="No knowledge-base entry matches the query.", expectedHandling="Return a clarification request or an honest no-match response instead of a fabricated answer.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Offer to escalate or rephrase.", userMessage="I couldn't find a confident answer to that.", loggingRequirement="Log the query as unanswered for review.", testScenario="Ask a question outside the knowledge base's coverage."),
            ])

        if facts.ai_involved:
            cases.extend([
                EdgeCaseDto(id=f"EC-{len(cases)+1:02d}", scenario="The AI provider times out.", expectedHandling="Return a clear degraded-service message instead of hanging.", relatedRequirement="NFR-08", severity="High", recoveryAction="Retry once, then present a fallback message.", userMessage="The assistant is taking longer than expected. Please try again.", loggingRequirement="Log the timeout with the provider name.", testScenario="Simulate a provider timeout."),
                EdgeCaseDto(id=f"EC-{len(cases)+2:02d}", scenario="The AI provider returns a malformed/non-JSON response.", expectedHandling="Detect the malformed response and fall back gracefully rather than crashing.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Retry once, then fall back.", userMessage="Something went wrong processing your request.", loggingRequirement="Log the malformed response for diagnostics (not the raw content to the user).", testScenario="Inject a malformed provider response in a test double."),
            ])

        if "Escalate Unresolved Query to Support Staff" in features:
            cases.append(
                EdgeCaseDto(id=f"EC-{len(cases)+1:02d}", scenario="No support staff are currently available to receive an escalation.", expectedHandling="Queue the ticket and confirm receipt rather than failing the escalation.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Ticket remains queued until a staff member is assigned.", userMessage="Your request has been submitted and will be reviewed shortly.", loggingRequirement="Log the queued escalation.", testScenario="Escalate with no staff currently assigned.")
            )

        return cases

    def _fallback_modules(self, facts: ProjectFacts) -> List[ModuleDto]:
        features = self._fallback_features(facts)
        modules: List[ModuleDto] = []

        for index, feature in enumerate(features, start=1):
            modules.append(
                ModuleDto(
                    id=f"MOD-{index:02d}",
                    name=f"{feature.name} Module",
                    responsibility=feature.description,
                    inputs=feature.inputs or ["Actor request"],
                    outputs=feature.outputs or ["Processed result"],
                    relatedRequirements=[f"FR-{index:02d}"],
                    dependencies=feature.dataEntities,
                    failureBehavior="Returns a clear error/fallback response without persisting invalid state.",
                    sourceClassification=feature.sourceClassification,
                )
            )

        return modules

    def _fallback_entities(self, facts: ProjectFacts) -> List[EntityDto]:
        features = self._fallback_features(facts)
        seen: Dict[str, EntityDto] = {}

        for feature_index, feature in enumerate(features, start=1):
            fr_id = f"FR-{feature_index:02d}"
            for entity_name in feature.dataEntities:
                if entity_name in seen:
                    if fr_id not in seen[entity_name].relatedRequirementIds:
                        seen[entity_name].relatedRequirementIds.append(fr_id)
                    continue

                field_templates = _ENTITY_FIELD_TEMPLATES.get(entity_name)
                if field_templates:
                    fields = [EntityFieldDto(**dict(t, nullable=t.get("nullable", False))) for t in field_templates]
                else:
                    # Should not normally happen given the curated feature
                    # list, but never render an entity with an empty field
                    # list -- generate a minimal, still-meaningful template.
                    fields = [
                        EntityFieldDto(name="Id", dataType="int", description="Primary key.", constraints="primary key", isPrimaryKey=True),
                        EntityFieldDto(name="Name", dataType="string", description=f"{entity_name} identifying name."),
                        EntityFieldDto(name="CreatedAt", dataType="datetime", description="Creation timestamp."),
                    ]

                primary_key_field = next((f.name for f in fields if f.isPrimaryKey), "Id")
                foreign_keys = [f"{f.name} -> {f.referencedEntity}.{f.referencedField}" for f in fields if f.isForeignKey]
                sensitive_fields = [f.name for f in fields if f.isSensitive]

                seen[entity_name] = EntityDto(
                    name=entity_name,
                    purpose=_ENTITY_PURPOSE.get(entity_name, f"Stores {entity_name} records for this project."),
                    importantFields=[f.name for f in fields],
                    fields=fields,
                    primaryKey=primary_key_field,
                    foreignKeys=foreign_keys,
                    uniqueConstraints=[f.name for f in fields if "unique" in (f.constraints or "").lower()],
                    indexes=[f.name for f in fields if f.isForeignKey],
                    sensitiveFields=sensitive_fields,
                    relationships=[f"References {fk.split(' -> ')[1]}" for fk in foreign_keys],
                    relatedRequirementIds=[fr_id],
                    sourceClassification=feature.sourceClassification,
                )

        return list(seen.values())

    def _fallback_relationships(self, entities: List[EntityDto]) -> List[RelationshipDto]:
        """
        Derives entityRelationships (used for the ER diagram and, when this
        module's own relationships are absent, by rebuild_mermaid_erd in
        se_documentation_structural_invariants.py) from TWO independent FK
        sources on the model's own response, normalized and validated the
        same way regardless of which one supplied them:

          (a) entity.foreignKeys -- top-level "Column -> Entity.Field" strings.
          (b) entity.fields[*] where isForeignKey=True -- referencedEntity/
              referencedField set directly on the field (required on every
              field by this agent's own prompt, and the actual source of the
              human-readable "(FK: ...)" captions rendered in the Database
              Design section).

        Field-level data (b) is the more reliable source in practice:
        observed live 2026-08-07, every entity's top-level foreignKeys list
        came back empty despite correct field-level FK data, so the OLD
        version of this method (which only ever read source (a)) found
        nothing and fabricated a relationship between the first two entities
        in the list ("Patient owns Administrator" -- no such relationship
        existed). That fabrication path is gone: this method now returns an
        EMPTY list rather than inventing a relationship when neither source
        yields anything real. Never rely on the Writer to follow the
        prompt's new instruction to populate both sources consistently --
        this deterministic extraction from field-level metadata is the
        actual guarantee.

        Every candidate relationship is validated before being returned:
        the source entity must exist, the target entity must exist (a
        dangling/fabricated referencedEntity is dropped, never guessed), the
        source field must exist on the source entity when a field name is
        available, and the referenced field must exist on the target entity
        WHEN one was explicitly provided (an omitted referencedField is not
        an error -- only an explicitly wrong one is rejected). Self-
        referencing FKs (an entity referencing itself, e.g. a parent/child
        hierarchy) are valid and preserved. The same underlying relationship
        described by both sources at once (or more than once within one
        source) collapses to a single entry; distinct FKs from the same
        entity to different targets are all preserved.
        """
        entities_by_name = {entity.name: entity for entity in entities if entity.name}
        fields_by_entity: dict[str, set[str]] = {
            name: {field.name for field in entity.fields if field.name}
            for name, entity in entities_by_name.items()
        }

        def _is_valid(
            referencing_entity: str, source_field: str | None,
            target_entity: str, target_field: str | None,
        ) -> bool:
            if not referencing_entity or referencing_entity not in entities_by_name:
                return False
            if not target_entity or target_entity not in entities_by_name:
                return False  # dangling/fabricated target -- never guessed
            if source_field and source_field not in fields_by_entity.get(referencing_entity, set()):
                return False
            if target_field and target_field not in fields_by_entity.get(target_entity, set()):
                return False  # explicitly-provided referenced field does not exist -- rejected
            return True

        seen_pairs: set[tuple[str, str]] = set()
        relationships: List[RelationshipDto] = []

        def _add(target_entity: str, referencing_entity: str) -> None:
            pair = (target_entity, referencing_entity)
            if pair in seen_pairs:
                return
            seen_pairs.add(pair)
            relationships.append(
                RelationshipDto(
                    fromEntity=target_entity, toEntity=referencing_entity, type="one-to-many",
                    description=f"A {target_entity} is referenced by many {referencing_entity} records.",
                )
            )

        for entity in entities:
            if not entity.name:
                continue

            # Source (b): field-level FK metadata -- checked first as the
            # more reliable source (see docstring).
            for field in entity.fields:
                if not field.isForeignKey or not field.referencedEntity:
                    continue
                target_field = field.referencedField or None
                if _is_valid(entity.name, field.name or None, field.referencedEntity, target_field):
                    _add(field.referencedEntity, entity.name)

            # Source (a): top-level "Column -> Entity.Field" strings.
            for fk in entity.foreignKeys:
                if " -> " not in fk:
                    continue
                source_field, target = fk.split(" -> ", 1)
                source_field = source_field.strip() or None
                target_entity, _, target_field = target.strip().partition(".")
                if _is_valid(entity.name, source_field, target_entity, target_field or None):
                    _add(target_entity, entity.name)

        return relationships

    def _fallback_tests(self, facts: ProjectFacts) -> List[TestCaseDto]:
        features = self._fallback_features(facts)
        tests: List[TestCaseDto] = []

        for index, feature in enumerate(features, start=1):
            fr_id = f"FR-{index:02d}"
            steps = feature.processing or [f"Trigger {feature.name.lower()}."]
            numbered_steps = [f"{i}. {step.rstrip('.')}." for i, step in enumerate(steps, start=1)]

            tests.append(
                TestCaseDto(
                    id=f"TC-{len(tests)+1:02d}", title=f"{feature.name} succeeds with valid input", type="Functional",
                    preconditions=["Actor is authenticated." if feature.name != "Authenticate and Authorize Users" else "Actor has a registered account."],
                    steps=numbered_steps, testData=["A representative valid input value for this feature."],
                    expectedResult=f"{feature.outputs[0] if feature.outputs else 'The expected result'} is produced and persisted where applicable.",
                    passCriteria="The documented outputs are observed and no error is logged.",
                    relatedRequirements=[fr_id], priority=self._feature_priority(feature),
                    sourceClassification=feature.sourceClassification,
                )
            )

            if feature.scopeStatus == "core":
                tests.append(
                    TestCaseDto(
                        id=f"TC-{len(tests)+1:02d}", title=f"{feature.name} rejects invalid input", type="Functional",
                        preconditions=["Actor is authenticated."],
                        steps=["1. Trigger the feature with missing/invalid required input.", "2. Observe the system's response."],
                        testData=["An empty or malformed input value."],
                        expectedResult="The system rejects the request with a specific, actionable message and persists no invalid state.",
                        passCriteria="A validation error is shown and no invalid record/transaction is persisted.",
                        negativeCase=True, relatedRequirements=[fr_id], priority="High",
                        sourceClassification=feature.sourceClassification,
                    )
                )

        tests.append(
            TestCaseDto(
                id=f"TC-{len(tests)+1:02d}", title="Unauthorized actor cannot access another actor's data", type="Security",
                preconditions=["Two distinct actor accounts exist."],
                steps=["1. Authenticate as actor A.", "2. Attempt to request actor B's data by id."],
                testData=["Actor B's record id."],
                expectedResult="The request is denied with an authorization error.",
                passCriteria="A 401/403-equivalent outcome is returned and logged; no data is exposed.",
                relatedRequirements=["NFR-01"], priority="High",
            )
        )
        tests.append(
            TestCaseDto(
                id=f"TC-{len(tests)+1:02d}", title="Service degrades gracefully during a dependency outage", type="Failure recovery",
                preconditions=["A dependent service (database or AI provider) can be simulated as unavailable."],
                steps=["1. Simulate the dependency outage.", "2. Trigger a feature that depends on it."],
                testData=["N/A - fault injection."],
                expectedResult="A clear service-unavailable message is shown; no partial/corrupt data is persisted.",
                passCriteria="No unhandled exception reaches the actor and no partial write is committed.",
                relatedRequirements=["NFR-03"], priority="Medium",
            )
        )

        if facts.ai_involved:
            tests.append(
                TestCaseDto(
                    id=f"TC-{len(tests)+1:02d}", title="Low-confidence response triggers clarification instead of a guess", type="AI evaluation",
                    preconditions=["A query engineered to produce a low-confidence classification/match is available."],
                    steps=["1. Submit the low-confidence query.", "2. Observe the system's response."],
                    testData=["An ambiguous or out-of-scope query."],
                    expectedResult="The system asks a clarifying question or escalates rather than presenting an unconfident answer as fact.",
                    passCriteria="No response below the confidence threshold is presented as a confident answer.",
                    relatedRequirements=["NFR-09"],
                    priority="High",
                )
            )

        return tests

    def _fallback_ui_screens(self, facts: ProjectFacts) -> List[UiScreenDto]:
        features = self._fallback_features(facts)
        actor = facts.primary_actor
        seen: Dict[str, UiScreenDto] = {}

        for feature_index, feature in enumerate(features, start=1):
            related_fr = f"FR-{feature_index:02d}"
            for screen_name in feature.uiScreens:
                if screen_name in seen:
                    seen[screen_name].relatedRequirements.append(related_fr)
                    continue

                template = _SCREEN_TEMPLATES.get(screen_name, {
                    "purpose": feature.description,
                    "mainComponents": ["Primary content area"],
                    "userActions": ["View content"],
                    "validationRules": [],
                    "loadingState": "Shows a loading indicator while data loads.",
                    "emptyState": "Shows guidance when no data exists yet.",
                    "errorState": "Shows a retry option on load failure.",
                    "successState": "Shows the loaded content.",
                })

                roles = feature.actors or [actor]
                seen[screen_name] = UiScreenDto(
                    screenId=f"UI-{len(seen)+1:02d}",
                    name=screen_name,
                    authorizedRoles=roles,
                    purpose=template["purpose"],
                    mainComponents=template["mainComponents"],
                    userActions=template["userActions"],
                    validationRules=template["validationRules"],
                    loadingState=template["loadingState"],
                    emptyState=template["emptyState"],
                    errorState=template["errorState"],
                    successState=template["successState"],
                    relatedRequirements=[related_fr],
                    sourceClassification=feature.sourceClassification,
                )

        return list(seen.values())

    def _fallback_security(self, facts: ProjectFacts) -> List[SecurityItemDto]:
        items = [
            SecurityItemDto(category="authentication", requirement="Passwords must be stored using a strong one-way hash (e.g. bcrypt/argon2), never in plain text.", rationale="Protects credentials if the database is compromised."),
            SecurityItemDto(category="authorization", requirement="Every data-access operation must verify the requesting actor owns or is authorized for the target record.", rationale="Prevents cross-account data access."),
            SecurityItemDto(category="input validation", requirement="All user-submitted input must be validated server-side, not only client-side.", rationale="Client-side validation can be bypassed."),
            SecurityItemDto(category="session management", requirement="Sessions must expire after a configured inactivity period.", rationale="Limits exposure from an unattended, authenticated device."),
            SecurityItemDto(category="secret management", requirement="API keys and connection strings must be stored in environment configuration, never committed to source control.", rationale="Prevents credential leakage."),
        ]

        if facts.ai_involved:
            items.extend([
                SecurityItemDto(category="prompt injection", requirement="User-submitted text must be screened before being forwarded to the AI provider, and any instruction-like content in it must never override system instructions.", rationale="Prevents prompt-injection attacks from manipulating the AI component."),
                SecurityItemDto(category="rate limiting", requirement="Requests to the AI/query-processing endpoint must be rate-limited per actor.", rationale="Protects against abuse and provider quota exhaustion."),
                SecurityItemDto(category="privacy", requirement="Personal data included in a query must not be forwarded to an external AI provider beyond what is strictly required to answer it.", rationale="Limits exposure of personal data to third parties."),
            ])

        return items

    def _fallback_architecture(self, facts: ProjectFacts) -> ArchitectureDto:
        confirmed = facts.confirmed_technology_names()
        frontend = self._pick_layer(facts, _FRONTEND_KEYWORDS, "Frontend (not confirmed)")
        backend = self._pick_layer(facts, _BACKEND_KEYWORDS, "Backend (not confirmed)")
        database = self._pick_layer(facts, _DB_KEYWORDS, "Database (not confirmed)")
        features = self._fallback_features(facts)
        feature_names = [f.name for f in features if f.scopeStatus == "core"]

        ai_service = ai_service_label(facts.technical_profile)

        return ArchitectureDto(
            style="Layered client-server architecture" if confirmed else "Layered client-server architecture (assumed; technology stack not confirmed)",
            frontend=frontend, backend=backend, database=database,
            aiService=ai_service,
            externalServices=[],
            explanation=(
                f"{facts.title} follows a layered architecture. {frontend} renders the actor-facing screens for "
                f"{facts.primary_actor}. {backend} implements the core feature set: {', '.join(feature_names) or 'the confirmed features'}. "
                + (f"{ai_service} handles the AI/NLP-dependent steps of query processing. " if facts.ai_involved else "")
                + f"{database} persists the entities described in this document. Authentication uses "
                f"{facts.technical_profile.authentication_mechanism}."
            ),
            components=[
                f"{frontend}: presentation layer for {facts.primary_actor}",
                f"{backend}: implements {', '.join(feature_names[:4]) or 'the core features'}",
            ] + ([f"{ai_service}: intent/answer processing"] if facts.ai_involved else []) + [
                f"{database}: persistence layer",
            ],
            dataFlow=(
                f"{facts.primary_actor} interacts with {frontend}, which calls {backend}. "
                + (f"{backend} delegates query-processing steps to {ai_service} and " if facts.ai_involved else f"{backend} ")
                + f"reads/writes {database}."
            ),
            authenticationFlow=f"Authentication uses {facts.technical_profile.authentication_mechanism}.",
        )

    # -------------------------------------------------------------------
    # Small deterministic helpers
    # -------------------------------------------------------------------

    def _pick_layer(self, facts: ProjectFacts, keywords: List[str], default: str) -> str:
        for name in facts.technology_names():
            lowered = name.lower()
            if any(keyword in lowered for keyword in keywords):
                return name
        return default

    def _looks_like_dev_module(self, name: str) -> bool:
        lowered = name.lower()
        return any(
            lowered.endswith(suffix) or suffix in lowered
            for suffix in ["module", "service layer", "repository", "engine", "middleware", "pipeline", "worker"]
        ) and "screen" not in lowered and "page" not in lowered and "dashboard" not in lowered

    def _sanitize_architecture(self, architecture: ArchitectureDto, facts: ProjectFacts) -> ArchitectureDto:
        """Never claim a technology outside this project's confirmed/assumed stack."""
        confirmed = {name.lower() for name in facts.technology_names()}
        if not confirmed:
            return architecture

        def _ok(value: str) -> bool:
            if not value or "not confirmed" in value.lower() or "not specified" in value.lower() or "not applicable" in value.lower():
                return True
            return any(token in value.lower() or value.lower() in token for token in confirmed)

        data = architecture.model_dump()
        for field_name in ("frontend", "backend", "database"):
            if not _ok(data.get(field_name, "")):
                data[field_name] = f"{data.get(field_name)} (not in this project's confirmed technology list; treat as an assumption)"
        return ArchitectureDto.model_validate(data)

    def _assign_canonical_ids(self, items: List[Any], prefix: str, id_field: str = "id") -> List[Any]:
        """Unconditionally reassigns prefix-01, prefix-02, ... to every item,
        regardless of whether the LLM/fallback already returned unique ids.
        Used for modules (MOD-) and UI screens (UI-) specifically, because
        the verified bug was an LLM (or a stale prompt example) returning
        legacy-format ids like "M-01"/"SC-01" that were already unique
        among themselves and so survived _ensure_unique_ids's "only
        renumber on duplicate" preservation logic untouched."""
        for index, item in enumerate(items, start=1):
            setattr(item, id_field, f"{prefix}-{index:02d}")
        return items

    def _ensure_unique_ids(self, items: List[Any], prefix: str, id_field: str = "id") -> List[Any]:
        """Renumber the id field (`id`, or `screenId` for UiScreenDto) to
        prefix-01, prefix-02, ... if a duplicate is present OR any item is
        missing an id (e.g. a deterministically-injected coverage item),
        preserving every item's content and order otherwise."""
        ids = [getattr(item, id_field, None) for item in items]

        if len(ids) == len(set(ids)) and all(ids):
            return items

        for index, item in enumerate(items, start=1):
            setattr(item, id_field, f"{prefix}-{index:02d}")

        return items

    def _ensure_unique_entity_names(self, entities: List[EntityDto]) -> List[EntityDto]:
        seen: Dict[str, int] = {}

        for entity in entities:
            base_name = entity.name
            count = seen.get(base_name, 0) + 1
            seen[base_name] = count

            if count > 1:
                entity.name = f"{base_name} ({count})"

        return entities

    def _dedupe_reference_lists(
        self,
        use_cases: List[UseCaseDto],
        modules: List[ModuleDto],
        entities: List[EntityDto],
        ui_screens: List[UiScreenDto],
        api_points: List[ApiPointDto],
        tests: List[TestCaseDto],
    ) -> None:
        """Collapses exact duplicate ids within a single item's own
        relatedRequirements/relatedRequirementIds/relatedUseCaseIds list
        (order-preserving), e.g. a test whose relatedRequirements came back
        as ["FR-01", "FR-01"]. Mutates in place; never removes a
        non-duplicate reference."""
        def _dedupe(ids: List[str]) -> List[str]:
            seen: set = set()
            result: List[str] = []
            for item_id in ids:
                if item_id not in seen:
                    seen.add(item_id)
                    result.append(item_id)
            return result

        for use_case in use_cases:
            use_case.relatedRequirements = _dedupe(use_case.relatedRequirements)
        for module in modules:
            module.relatedRequirements = _dedupe(module.relatedRequirements)
        for entity in entities:
            entity.relatedRequirementIds = _dedupe(entity.relatedRequirementIds)
        for screen in ui_screens:
            screen.relatedRequirements = _dedupe(screen.relatedRequirements)
        for api in api_points:
            api.relatedRequirements = _dedupe(api.relatedRequirements)
        for test in tests:
            test.relatedRequirements = _dedupe(test.relatedRequirements)
            test.relatedUseCaseIds = _dedupe(test.relatedUseCaseIds)

    def _detect_traceability_integrity_issues(
        self,
        frs: List[RequirementDto],
        nfrs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        tests: List[TestCaseDto],
    ) -> List[str]:
        """Lightweight, deterministic disagreement detection -- NOT full NL
        semantic reasoning, and never a hard blocker (the semantic Reviewer
        in the review pipeline remains responsible for nuanced meaning; this
        agent only flags obvious, structurally-detectable drift as a
        non-blocking consistencyWarnings entry). At this point every
        reference has already been reconciled against real ids and
        deduplicated (see _reconcile_requirement_references,
        _dedupe_reference_lists) and RequirementDto's own reverse references
        have already been rebuilt from the same child data (see
        _rebuild_requirement_reverse_references) -- so this only catches the
        one disagreement that isn't already structurally impossible by
        construction: a test case citing both an FR and a use case that
        doesn't actually implement that FR."""
        warnings: List[str] = []
        use_cases_by_id = {uc.id: uc for uc in use_cases}

        for test in tests:
            if not test.relatedUseCaseIds:
                continue
            linked_use_cases = [use_cases_by_id[uc_id] for uc_id in test.relatedUseCaseIds if uc_id in use_cases_by_id]
            if not linked_use_cases:
                continue
            for fr_id in test.relatedRequirements:
                if not any(fr_id in uc.relatedRequirements for uc in linked_use_cases):
                    warnings.append(
                        f"{test.id} references {fr_id}, but none of its linked use case(s) "
                        f"({', '.join(uc.id for uc in linked_use_cases)}) reference {fr_id} -- "
                        f"possible mismatched requirement traceability."
                    )

        return warnings

    def _rebuild_requirement_reverse_references(
        self,
        frs: List[RequirementDto],
        nfrs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        modules: List[ModuleDto],
    ) -> None:
        """Rebuilds every RequirementDto.relatedUseCaseIds/relatedModuleIds
        deterministically from use_cases[*].relatedRequirements and
        modules[*].relatedRequirements -- the dependent objects are the
        single source of truth here, never the requirements section's own
        Writer-generated reverse-reference fields (which come from a
        SEPARATE, independent LLM call and can drift from what useCases/
        modulesArchitecture actually declared, or simply go stale after
        _reconcile_requirement_references drops a dangling forward
        reference). Call AFTER use_cases/modules have their final,
        reconciled relatedRequirements -- never before.
        """
        for req in (*frs, *nfrs):
            req.relatedUseCaseIds = [uc.id for uc in use_cases if req.id in uc.relatedRequirements]
            req.relatedModuleIds = [m.id for m in modules if req.id in m.relatedRequirements]

    def _reconcile_requirement_references(
        self,
        requirement_ids: set,
        use_cases: List[UseCaseDto],
        edge_cases: List[EdgeCaseDto],
        modules: List[ModuleDto],
        tests: List[TestCaseDto],
    ) -> None:
        """Drops any relatedRequirements/relatedRequirement reference that
        doesn't correspond to a real FR/NFR id -- can happen when one
        section's content came from the LLM (its own id scheme) while
        another fell back to hardcoded ids, or the LLM itself hallucinated a
        reference. Mutates in place.

        Never substitutes a fabricated default (this method previously
        replaced a dangling/empty reference with "the first requirement id
        in sorted order" -- exactly the kind of arbitrary, meaning-blind
        link this project's traceability-honesty requirement forbids: a
        wrong link is worse than no link, and silently pointing every
        unresolved reference at the same "first" id is indistinguishable
        from a real relationship once rendered in the matrix). A dangling
        reference is simply removed, leaving [] (or "" for the singular
        edge-case field) when nothing valid remains -- see
        _build_traceability, which already treats an empty reference list
        as "genuinely not linked" rather than needing a placeholder id to
        iterate over.
        """
        if not requirement_ids:
            for use_case in use_cases:
                use_case.relatedRequirements = []
            for edge_case in edge_cases:
                edge_case.relatedRequirement = ""
            for module in modules:
                module.relatedRequirements = []
            for test in tests:
                test.relatedRequirements = []
            return

        for use_case in use_cases:
            use_case.relatedRequirements = [r for r in use_case.relatedRequirements if r in requirement_ids]

        for edge_case in edge_cases:
            if edge_case.relatedRequirement not in requirement_ids:
                edge_case.relatedRequirement = ""

        for module in modules:
            module.relatedRequirements = [r for r in module.relatedRequirements if r in requirement_ids]

        for test in tests:
            test.relatedRequirements = [r for r in test.relatedRequirements if r in requirement_ids]

    def _reconcile_screen_references(self, requirement_ids: set, ui_screens: List[UiScreenDto]) -> None:
        for screen in ui_screens:
            screen.relatedRequirements = [r for r in screen.relatedRequirements if r in requirement_ids]

    def _reconcile_entity_and_api_references(
        self, requirement_ids: set, entities: List[EntityDto], api_points: List[ApiPointDto]
    ) -> None:
        for entity in entities:
            entity.relatedRequirementIds = [r for r in entity.relatedRequirementIds if r in requirement_ids]
        for api in api_points:
            api.relatedRequirements = [r for r in api.relatedRequirements if r in requirement_ids]

    # -------------------------------------------------------------------
    # Deterministic coverage guarantees -- a confirmed feature always gets
    # its required database entity / UI screen / test case even if the
    # independent LLM call for that section omitted it.
    # -------------------------------------------------------------------

    def _ensure_entity_coverage(
        self, entities: List[EntityDto], facts: ProjectFacts, requirement_text: str, requirement_ids: set
    ) -> List[EntityDto]:
        existing_names = {e.name.lower() for e in entities}
        blob = " ".join([facts.problem, facts.solution, facts.final_deliverables, requirement_text])
        default_req = next(iter(sorted(requirement_ids))) if requirement_ids else ""

        for name, purpose in required_entities_for_text(blob):
            if name.lower() in existing_names:
                continue
            entities.append(
                EntityDto(
                    name=name, purpose=purpose,
                    fields=[
                        EntityFieldDto(name="Id", dataType="int", nullable=False, description="Primary key.", constraints="primary key", isPrimaryKey=True),
                        EntityFieldDto(name="CreatedAt", dataType="datetime", nullable=False, description="Creation timestamp."),
                        EntityFieldDto(name="Description", dataType="string", nullable=True, description="Descriptive content for this record."),
                    ],
                    primaryKey="Id",
                    relatedRequirementIds=[default_req] if default_req else [],
                    sourceClassification="inferred",
                )
            )
            existing_names.add(name.lower())

        return entities

    def _normalize_entities(self, entities: List[EntityDto]) -> List[EntityDto]:
        """Hard rules from the stabilization spec: no entity may render with
        an empty field list, every entity needs >=3 fields (unless it's a
        junction table) and exactly one primary key, and password data is
        always represented as PasswordHash, never a raw Password field."""
        entity_names = {e.name for e in entities}

        for entity in entities:
            for field in entity.fields:
                if field.name.strip().lower() in ("password", "rawpassword", "plaintextpassword"):
                    field.name = "PasswordHash"
                    field.dataType = "string"
                    field.isSensitive = True
                    field.constraints = (field.constraints + "; hashed, sensitive").strip("; ")

            is_junction = len(entity.foreignKeys) >= 2 and len(entity.fields) <= 3

            if not entity.fields:
                entity.fields = [
                    EntityFieldDto(name="Id", dataType="int", nullable=False, description="Primary key.", constraints="primary key", isPrimaryKey=True),
                    EntityFieldDto(name="CreatedAt", dataType="datetime", nullable=False, description="Creation timestamp."),
                    EntityFieldDto(name="Description", dataType="string", nullable=True, description=f"Descriptive content for {entity.name}."),
                ]

            if not is_junction and len(entity.fields) < 3:
                existing_names = {f.name.lower() for f in entity.fields}
                for filler_name, filler_type, filler_desc in (
                    ("CreatedAt", "datetime", "Creation timestamp."),
                    ("UpdatedAt", "datetime", "Last update timestamp."),
                    ("Status", "string", "Current status of this record."),
                ):
                    if len(entity.fields) >= 3:
                        break
                    if filler_name.lower() not in existing_names:
                        entity.fields.append(EntityFieldDto(name=filler_name, dataType=filler_type, nullable=False, description=filler_desc))
                        existing_names.add(filler_name.lower())

            if not any(f.isPrimaryKey for f in entity.fields):
                pk_field = next((f for f in entity.fields if f.name.lower() == entity.primaryKey.lower()), None)
                if pk_field:
                    pk_field.isPrimaryKey = True
                else:
                    entity.fields.insert(0, EntityFieldDto(name=entity.primaryKey or "Id", dataType="int", nullable=False, description="Primary key.", constraints="primary key", isPrimaryKey=True))
                    entity.primaryKey = entity.primaryKey or "Id"

            entity.sensitiveFields = list(dict.fromkeys(
                entity.sensitiveFields + [f.name for f in entity.fields if f.isSensitive]
            ))

            # Foreign keys must reference an entity that actually exists in
            # this document -- drop any that don't rather than leaving a
            # dangling reference.
            entity.foreignKeys = [
                fk for fk in entity.foreignKeys
                if not ("->" in fk) or fk.split("->")[-1].strip().split(".")[0].strip() in entity_names
            ]

        return entities

    def _assign_entity_ids(self, entities: List[EntityDto]) -> List[EntityDto]:
        for index, entity in enumerate(entities, start=1):
            if not entity.entityId:
                entity.entityId = f"ENT-{index:02d}"
        ids = [e.entityId for e in entities]
        if len(ids) != len(set(ids)):
            for index, entity in enumerate(entities, start=1):
                entity.entityId = f"ENT-{index:02d}"
        return entities

    def _assign_api_ids(self, api_points: List[ApiPointDto]) -> List[ApiPointDto]:
        for index, api in enumerate(api_points, start=1):
            if not api.apiId:
                api.apiId = f"API-{index:02d}"
        ids = [a.apiId for a in api_points]
        if len(ids) != len(set(ids)):
            for index, api in enumerate(api_points, start=1):
                api.apiId = f"API-{index:02d}"
        return api_points

    def _ensure_screen_coverage(
        self, ui_screens: List[UiScreenDto], facts: ProjectFacts, requirement_text: str, requirement_ids: set
    ) -> List[UiScreenDto]:
        existing_names = {s.name.lower() for s in ui_screens}
        blob = " ".join([facts.problem, facts.solution, facts.final_deliverables, requirement_text])
        default_req = next(iter(sorted(requirement_ids))) if requirement_ids else ""

        for name, purpose in required_screens_for_text(blob):
            if name.lower() in existing_names:
                continue
            ui_screens.append(
                UiScreenDto(
                    screenId="", name=name, authorizedRoles=[facts.primary_actor],
                    purpose=purpose, mainComponents=[], userActions=[],
                    relatedRequirements=[default_req] if default_req else [],
                    sourceClassification="inferred",
                )
            )
            existing_names.add(name.lower())

        return ui_screens

    def _ensure_test_coverage(self, tests: List[TestCaseDto], frs: List[RequirementDto]) -> List[TestCaseDto]:
        """Every functional requirement must map to at least one test case --
        auto-generate a minimal but concrete test for any FR that has none,
        instead of silently leaving it untested."""
        covered = {ref for test in tests for ref in test.relatedRequirements}
        next_index = len(tests) + 1

        for fr in frs:
            if fr.id in covered:
                continue
            tests.append(
                TestCaseDto(
                    id=f"TC-{next_index:02d}",
                    title=f"Verify: {fr.title}",
                    type="Functional",
                    preconditions=fr.preconditions or [f"{fr.primaryActor or 'The actor'} meets the preconditions for {fr.title}."],
                    testData=["Representative valid input for this requirement."],
                    steps=[
                        f"1. Set up preconditions for '{fr.title}'.",
                        f"2. Perform the action described in FR '{fr.title}'.",
                        "3. Observe the system's response.",
                    ],
                    expectedResult=(fr.acceptanceCriteria[0] if fr.acceptanceCriteria else f"The system behaves as described by {fr.id}: {fr.description}"),
                    passCriteria=(fr.acceptanceCriteria[0] if fr.acceptanceCriteria else "The observed behavior matches the requirement description exactly."),
                    relatedRequirements=[fr.id],
                    priority=fr.priority or "Medium",
                    sourceClassification="inferred",
                )
            )
            covered.add(fr.id)
            next_index += 1

        return tests

    # -------------------------------------------------------------------
    # Canonical AI-approach / authentication sanitization -- strips the
    # specific contradicting phrases the stabilization batch flagged (JWT
    # mentioned when only ASP.NET Identity is confirmed; RAG/fine-tuning
    # mentioned when the canonical approach is intent classification; API
    # calls described as "training") from the two fields most likely to
    # contain free narrative text.
    # -------------------------------------------------------------------

    def _sanitize_ai_and_auth_text(
        self, architecture: ArchitectureDto, ai_report_raw: Any, facts: ProjectFacts
    ) -> tuple[ArchitectureDto, Any]:
        profile = facts.technical_profile
        data = architecture.model_dump()

        def _strip_jwt(text: str) -> str:
            if "jwt" not in profile.authentication_mechanism.lower() and "jwt" in text.lower():
                return re.sub(r"[^.]*\bJWT\b[^.]*\.", "", text, flags=re.IGNORECASE).strip() or (
                    f"Authentication uses {profile.authentication_mechanism}."
                )
            return text

        data["authenticationFlow"] = _strip_jwt(data.get("authenticationFlow") or f"Authentication uses {profile.authentication_mechanism}.")
        data["explanation"] = _strip_jwt(data.get("explanation") or "")

        architecture = ArchitectureDto.model_validate(data)

        if isinstance(ai_report_raw, dict) and profile.ai_approach not in ("rag", "hybrid"):
            for field_name in ("modelOrApproach", "retrievalStrategy"):
                value = ai_report_raw.get(field_name) or ""
                if isinstance(value, str) and ("retrieval-augmented" in value.lower() or "rag" in value.lower().split()):
                    ai_report_raw[field_name] = f"Knowledge-base lookup (approach: {profile.ai_approach})."

        if isinstance(ai_report_raw, dict) and profile.ai_provider_type == "external_api":
            value = ai_report_raw.get("trainingVsInference") or ""
            if isinstance(value, str) and ("train" in value.lower() or "fine-tun" in value.lower()):
                ai_report_raw["trainingVsInference"] = (
                    "No project-specific training; requests are processed using a pretrained external model API."
                )

        return architecture, ai_report_raw

    def _collect_assumptions(
        self,
        facts: ProjectFacts,
        frs: List[RequirementDto],
        nfrs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        edge_cases: List[EdgeCaseDto],
        modules: List[ModuleDto],
        entities: List[EntityDto],
        ui_screens: List[UiScreenDto],
        api_points: List[ApiPointDto],
        tests: List[TestCaseDto],
        ai_report: Optional["AiTechnicalReportDto"],
        used_fallback: bool,
    ) -> List[AssumptionDto]:
        """Scans every section's own sourceClassification instead of only
        the project-fact-level defaults, so the assumptions section can
        never falsely claim 'no assumptions were required' while individual
        items are actually marked inferred/assumption/proposed."""
        non_confirmed = {"inferred", "assumption", "proposed_target", "unknown", "unresolved"}
        assumptions: List[AssumptionDto] = [
            AssumptionDto(item=item, classification="assumption") for item in facts.assumptions
        ]

        def _add(label: str, item_id: str, classification: str) -> None:
            if classification in non_confirmed:
                assumptions.append(
                    AssumptionDto(
                        item=f"{item_id}: {label} is classified as '{classification}' rather than confirmed.",
                        classification=classification,
                    )
                )

        for fr in frs:
            _add(fr.title, fr.id, fr.sourceClassification)
            if fr.measurableTarget and fr.measurableTarget.lower().startswith("proposed acceptance target"):
                _add(f"the measurable target '{fr.measurableTarget}'", fr.id, "proposed_target")
        for nfr in nfrs:
            _add(nfr.title, nfr.id, nfr.sourceClassification)
            if nfr.measurableTarget and nfr.measurableTarget.lower().startswith("proposed acceptance target"):
                _add(f"the measurable target '{nfr.measurableTarget}'", nfr.id, "proposed_target")
        for use_case in use_cases:
            _add(use_case.title, use_case.id, use_case.sourceClassification)
        for edge_case in edge_cases:
            _add(edge_case.scenario, edge_case.id, edge_case.sourceClassification)
        for module in modules:
            _add(module.name, module.id, module.sourceClassification)
        for entity in entities:
            _add(f"database entity '{entity.name}'", entity.entityId or entity.name, entity.sourceClassification)
        for screen in ui_screens:
            _add(f"UI screen '{screen.name}'", screen.screenId, screen.sourceClassification)
        for api in api_points:
            _add(f"API integration '{api.name}'", api.apiId, api.sourceClassification)
        for test in tests:
            _add(test.title, test.id, test.sourceClassification)

        for decision in facts.technical_profile.unresolved_technical_decisions:
            assumptions.append(AssumptionDto(item=decision, classification="unresolved"))

        if used_fallback:
            assumptions.append(
                AssumptionDto(
                    item=(
                        "This document was generated using deterministic fallback content "
                        "(no AI provider returned valid JSON), so requirements, use cases, "
                        "modules, entities, and screens follow a generic template rather than "
                        "an AI-generated interpretation of this project."
                    ),
                    classification="assumption",
                )
            )

        # De-duplicate while preserving order and assign stable A-01, A-02, ... ids.
        seen: set = set()
        deduped: List[AssumptionDto] = []
        for a in assumptions:
            if a.item in seen:
                continue
            seen.add(a.item)
            deduped.append(a)

        return deduped

    def _build_traceability(
        self,
        frs: List[RequirementDto],
        nfrs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        modules: List[ModuleDto],
        entities: List[EntityDto],
        tests: List[TestCaseDto],
        ui_screens: List[UiScreenDto],
        api_points: List[ApiPointDto],
    ) -> List[TraceabilityDto]:
        """Requirement-centric: one row per functional requirement, built
        from each section's own real relatedRequirements/relatedRequirementIds
        back-references -- never a positional zip that silently drops every
        requirement past the shortest section's length."""
        rows: List[TraceabilityDto] = []

        for fr in frs:
            matched_use_cases = [uc.id for uc in use_cases if fr.id in uc.relatedRequirements]
            matched_modules = [m.id for m in modules if fr.id in m.relatedRequirements]
            matched_entities = [e.entityId for e in entities if fr.id in e.relatedRequirementIds]
            matched_screens = [s.screenId for s in ui_screens if fr.id in s.relatedRequirements]
            matched_apis = [a.apiId for a in api_points if fr.id in a.relatedRequirements]
            matched_tests = [t.id for t in tests if fr.id in t.relatedRequirements]

            notes = []
            if not matched_entities:
                notes.append("No database entity applies to this requirement (N/A).")
            if not matched_screens:
                notes.append("No UI screen applies to this requirement (N/A).")
            if not matched_apis:
                notes.append("No API/integration applies to this requirement (N/A).")

            if matched_tests and (matched_use_cases or fr.category):
                coverage_status = "covered"
            elif matched_tests:
                coverage_status = "partially_covered"
            else:
                coverage_status = "uncovered"

            rows.append(
                TraceabilityDto(
                    requirementId=fr.id,
                    useCaseId=matched_use_cases[0] if matched_use_cases else "",
                    moduleId=matched_modules[0] if matched_modules else "",
                    entity=matched_entities[0] if matched_entities else "",
                    testCaseId=matched_tests[0] if matched_tests else "",
                    screenId=matched_screens[0] if matched_screens else "",
                    apiId=matched_apis[0] if matched_apis else "",
                    useCaseIds=matched_use_cases,
                    moduleIds=matched_modules,
                    entityIds=matched_entities,
                    screenIds=matched_screens,
                    apiIds=matched_apis,
                    testCaseIds=matched_tests,
                    coverageStatus=coverage_status,
                    notes="; ".join(notes),
                )
            )

        return rows

    def _build_erd(self, entities: List[EntityDto], relationships: List[RelationshipDto]) -> str:
        lines = ["erDiagram"]

        entity_names = {entity.name for entity in entities}

        for rel in relationships:
            if rel.fromEntity not in entity_names or rel.toEntity not in entity_names:
                continue
            left = self._mermaid_name(rel.fromEntity)
            right = self._mermaid_name(rel.toEntity)
            symbol = "||--o{" if "many" in rel.type else "||--||"
            lines.append(f"    {left} {symbol} {right} : relates")

        if len(lines) == 1 and len(entities) >= 2:
            lines.append(f"    {self._mermaid_name(entities[0].name)} ||--o{{ {self._mermaid_name(entities[1].name)} : owns")

        return "\n".join(lines)

    def _build_class_diagram(self, entities: List[EntityDto]) -> str:
        lines = ["classDiagram"]

        for entity in entities:
            name = self._mermaid_name(entity.name)
            lines.append(f"    class {name} {{")
            field_names = [f.name for f in entity.fields] or entity.importantFields
            for field in field_names[:6]:
                safe_field = re.sub(r"[^A-Za-z0-9_]", "", str(field))
                lines.append(f"      string {safe_field or 'Field'}")
            lines.append("    }")

        return "\n".join(lines)

    def _select_primary_use_case(
        self, facts: ProjectFacts, frs: List[RequirementDto], use_cases: List[UseCaseDto],
    ) -> Optional[UseCaseDto]:
        """
        Deterministically picks the use case that actually represents this
        project's CENTRAL workflow, instead of defaulting to useCases[0]
        (the OLD behavior -- see _build_activity_diagram/_build_sequence_
        diagram's prior implementation, which took `use_cases[0]` directly
        and silently rendered whatever use case a section call happened to
        list first, e.g. account registration/login for a medical-triage
        project whose real central workflow is symptom submission).

        Scored generically (never a project-type check like "if medical
        project then choose triage") from evidence any FYP's own generated
        content provides:
          1. how many of THIS project's own functional requirements the use
             case is explicitly linked to (relatedRequirements) -- a use
             case implementing more of the confirmed feature set is more
             central than one implementing a single, often-incidental,
             requirement (e.g. login);
          2. how many of those linked requirements are themselves High
             priority (a project-agnostic "core requirement" signal already
             present on every RequirementDto, never invented here);
          3. whether the use case's own actor matches this project's
             confirmed primary actor (see _normalize_actor_key);
          4. when facts.ai_involved, whether the use case (or a requirement
             it implements) uses generic AI/ML vocabulary (classification,
             recommendation, inference, ...) -- a project-agnostic
             AI-centrality signal, never a hardcoded domain keyword.

        Ties are broken by original list order (Python's max() keeps the
        first maximal element), which only matters when no evidence
        differentiates use cases at all -- i.e. exactly the "no clearly
        dominant use case exists" case the task calls out, where falling
        back to the first listed candidate is an honest, deterministic
        choice rather than a fabricated one.
        """
        if not use_cases:
            return None

        high_priority_fr_ids = {fr.id for fr in frs if (fr.priority or "").strip().lower() == "high"}
        fr_by_id = {fr.id: fr for fr in frs}
        primary_actor_key = _normalize_actor_key(facts.primary_actor)

        def _score(uc: UseCaseDto) -> int:
            linked_fr_ids = set(uc.relatedRequirements)
            score = len(linked_fr_ids) * 3
            score += len(linked_fr_ids & high_priority_fr_ids) * 2
            if primary_actor_key and _normalize_actor_key(uc.actor) == primary_actor_key:
                score += 2
            if facts.ai_involved:
                linked_text = " ".join(
                    f"{fr_by_id[fr_id].title} {fr_by_id[fr_id].description}"
                    for fr_id in linked_fr_ids if fr_id in fr_by_id
                ).lower()
                own_text = f"{uc.title} {uc.goal}".lower()
                if any(keyword in linked_text or keyword in own_text for keyword in _AI_CENTRALITY_KEYWORDS):
                    score += 2
            return score

        return max(use_cases, key=_score)

    def _build_activity_diagram(
        self, facts: ProjectFacts, primary: Optional[UseCaseDto], edge_cases: Optional[List[EdgeCaseDto]] = None,
    ) -> str:
        """Deterministically derived from the project's own SELECTED primary
        use case (see _select_primary_use_case -- never simply "the first
        use case in the list"). Labels are routed through
        mermaid_utils.safe_label so no node is a raw, overly long use-case
        sentence and none is truncated mid-word."""
        lines = ["flowchart TD"]
        node_id = "A"

        def _next_id(current: str) -> str:
            return chr(ord(current) + 1)

        start_label = f"{facts.primary_actor} starts: {primary.goal}" if primary and primary.goal else f"{facts.primary_actor} opens {facts.title}"
        lines.append(f"    {node_id}[{safe_label(start_label)}]")
        prev = node_id

        steps = primary.mainFlow if primary and primary.mainFlow else [
            f"{facts.primary_actor} performs the primary action",
            "System validates the request",
            "System persists/updates the relevant data",
            "System confirms the result",
        ]

        for step in steps[:8]:
            node_id = _next_id(node_id)
            lines.append(f"    {prev} --> {node_id}[{safe_label(step)}]")
            prev = node_id

        # A decision point is only added when the project's OWN edge-case
        # data explicitly documents a confidence/uncertainty branch for the
        # selected primary use case -- never invented (e.g. never a
        # fabricated "low confidence?" branch for a project with no such
        # edge case on record).
        if primary and edge_cases:
            linked_fr_ids = set(primary.relatedRequirements)
            for edge_case in edge_cases:
                if edge_case.relatedRequirement not in linked_fr_ids:
                    continue
                if "confiden" not in edge_case.scenario.lower() and "confiden" not in edge_case.expectedHandling.lower():
                    continue
                decision_id = _next_id(node_id)
                lines.append(f"    {prev} --> {decision_id}{{{safe_label(edge_case.scenario)}}}")
                if edge_case.recoveryAction:
                    outcome_id = _next_id(decision_id)
                    lines.append(f"    {decision_id} -->|Yes| {outcome_id}[{safe_label(edge_case.recoveryAction)}]")
                    node_id = outcome_id
                else:
                    node_id = decision_id
                prev = node_id
                break

        return "\n".join(lines)

    def _build_sequence_diagram(
        self,
        facts: ProjectFacts,
        architecture: ArchitectureDto,
        primary: Optional[UseCaseDto],
        frs: Optional[List[RequirementDto]] = None,
        modules: Optional[List[ModuleDto]] = None,
        api_points: Optional[List[ApiPointDto]] = None,
    ) -> str:
        """Deterministically derived from this project's architecture
        components, confirmed API integration points/modules, and SELECTED
        primary use case (see _select_primary_use_case). Participants are
        declared exactly once each via a _ParticipantRegistry keyed by
        normalized identity, so two architecture fields that happen to
        resolve to the SAME real component (the verified live bug: frontend
        and backend both resolving to "ASP.NET Core Razor Pages" because
        that technology name matches both the frontend and backend keyword
        lists in _pick_layer) collapse into one participant instead of
        producing a duplicate `participant ... as ...` declaration and a
        meaningless X->>X self-call."""
        frs = frs or []
        modules = modules or []
        api_points = api_points or []
        registry = _ParticipantRegistry()

        actor_names = split_combined_actor(facts.primary_actor) or [facts.primary_actor or "User"]
        actor = registry.register(_normalize_actor_key(actor_names[0]) or "actor", actor_names[0], is_actor=True)

        frontend = registry.register(architecture.frontend, architecture.frontend)
        backend = registry.register(architecture.backend, architecture.backend)
        database = registry.register(architecture.database, architecture.database)

        linked_fr_ids = set(primary.relatedRequirements) if primary else set()

        # Prefer a confirmed API integration point actually tied to the
        # primary workflow's own requirements over a generic "AI Service"
        # label -- e.g. a real "FastAPI Triage Service" apiIntegrationPoints
        # entry, never a fabricated generic name, when this project confirms
        # one.
        ai_participant = None
        if facts.ai_involved:
            matching_apis = sorted(
                (api for api in api_points if set(api.relatedRequirements) & linked_fr_ids),
                key=lambda api: api.apiId,
            )
            if matching_apis:
                ai_participant = registry.register(matching_apis[0].name, matching_apis[0].name)
            elif len(api_points) == 1:
                ai_participant = registry.register(api_points[0].name, api_points[0].name)
            elif architecture.aiService and architecture.aiService.lower() not in ("not applicable", ""):
                ai_participant = registry.register(architecture.aiService, architecture.aiService)

        # Real intermediate processing components (e.g. preprocessing/
        # classification modules) confirmed by this project's own
        # modulesArchitecture output and actually tied to the primary
        # workflow's requirements -- capped at 2 so the diagram stays
        # bounded and readable.
        processing_participants = []
        if ai_participant is not None:
            for module in modules:
                if len(processing_participants) >= 2:
                    break
                if not set(module.relatedRequirements) & linked_fr_ids:
                    continue
                lowered = module.name.lower()
                if not any(keyword in lowered for keyword in _AI_CENTRALITY_KEYWORDS):
                    continue
                processing_participants.append(registry.register(module.name, module.name))

        lines = ["sequenceDiagram"]
        lines.extend(registry.render())

        trigger = primary.trigger if primary and primary.trigger else f"Perform primary action in {facts.title}"
        lines.append(f"    {actor.mermaid_id}->>{frontend.mermaid_id}: {safe_label(trigger)}")

        # A duplicate/collapsed frontend+backend participant (same
        # registered identity) must never emit a self-call -- go straight
        # to whatever comes next instead of "X -> X: Submit request".
        current = frontend
        if backend.key != frontend.key:
            lines.append(f"    {frontend.mermaid_id}->>{backend.mermaid_id}: Submit request")
            current = backend

        if ai_participant is not None and ai_participant.key != current.key:
            chain = [current] + processing_participants + [ai_participant]
            for step_from, step_to in zip(chain, chain[1:]):
                lines.append(f"    {step_from.mermaid_id}->>{step_to.mermaid_id}: Process request")
            for step_from, step_to in zip(reversed(chain), reversed(chain[:-1])):
                lines.append(f"    {step_from.mermaid_id}-->>{step_to.mermaid_id}: Return result")
            # The reversed loop above already walks the chain back to
            # chain[0] (the participant we entered this block with) -- the
            # last "Return result" line ends at chain[0], so `current`
            # stays exactly where it was, never an intermediate module.

        if database.key != current.key:
            lines.append(f"    {current.mermaid_id}->>{database.mermaid_id}: Read/write data")
            lines.append(f"    {database.mermaid_id}-->>{current.mermaid_id}: Return data")

        if backend.key != frontend.key:
            lines.append(f"    {backend.mermaid_id}-->>{frontend.mermaid_id}: Return response")
        lines.append(f"    {frontend.mermaid_id}-->>{actor.mermaid_id}: Display result")

        return "\n".join(lines)

    def _mermaid_name(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
        return cleaned.upper() or "ENTITY"

    def _validate_diagrams(
        self, erd: str, class_diagram: str, activity: str, sequence: str, facts: ProjectFacts
    ) -> Dict[str, Any]:
        """Runs every diagram through mermaid_utils.validate_mermaid once, so
        the quality score can never claim diagramValidity=100 while a
        diagram is actually malformed. Since all four diagrams are built
        deterministically (never LLM-authored), "regenerating" on failure
        just means rebuilding from the same safe deterministic inputs --
        there is no free-text LLM diagram to retry."""
        known_names = [facts.primary_actor] + facts.supporting_actors

        erd_ok, erd_issues = validate_mermaid(erd, expected_header="erDiagram")
        class_ok, class_issues = validate_mermaid(class_diagram, expected_header="classDiagram")
        activity_ok, activity_issues = validate_mermaid(activity, expected_header="flowchart", known_names=known_names)
        sequence_ok, sequence_issues = validate_mermaid(sequence, expected_header="sequenceDiagram", known_names=known_names)

        all_ok = erd_ok and class_ok and activity_ok and sequence_ok
        issues = erd_issues + class_issues + activity_issues + sequence_issues

        return {"ok": all_ok, "issues": issues}

    # -------------------------------------------------------------------
    # Deterministic documentation quality score (section 19 of the spec) --
    # replaces the previous hardcoded "88 if not used_fallback else 82".
    #
    # This method is now a THIN WRAPPER around the module-level
    # compute_quality_assessment function below -- extracted so the exact
    # same criteria/weights/caps can be recomputed later, from a FINAL
    # (post-repair/post-rebuild) candidate, without a second competing
    # scoring formula. See compute_quality_assessment's own docstring and
    # app/agents/se_documentation/quality_recomputation.py, which is the
    # dict-based entrypoint that calls this same function after final
    # review. Kept as a method (rather than removed) purely so every
    # existing call site (`self._compute_quality_assessment(...)`) and
    # every existing test keeps working unchanged.
    # -------------------------------------------------------------------

    def _compute_quality_assessment(
        self,
        facts: ProjectFacts,
        frs: List[RequirementDto],
        nfrs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        edge_cases: List[EdgeCaseDto],
        modules: List[ModuleDto],
        entities: List[EntityDto],
        ui_screens: List[UiScreenDto],
        tests: List[TestCaseDto],
        traceability: List[TraceabilityDto],
        architecture: ArchitectureDto,
        assumptions: List[AssumptionDto],
        used_fallback: bool,
        diagram_validation: Dict[str, Any],
        ai_report: Optional[AiTechnicalReportDto] = None,
        ai_applicable: bool = False,
    ) -> QualityAssessmentDto:
        return compute_quality_assessment(
            facts, frs, nfrs, use_cases, edge_cases, modules, entities, ui_screens, tests,
            traceability, architecture, assumptions, used_fallback, diagram_validation,
            ai_report=ai_report, ai_applicable=ai_applicable,
        )


def compute_quality_assessment(
    facts: ProjectFacts,
    frs: List[RequirementDto],
    nfrs: List[RequirementDto],
    use_cases: List[UseCaseDto],
    edge_cases: List[EdgeCaseDto],
    modules: List[ModuleDto],
    entities: List[EntityDto],
    ui_screens: List[UiScreenDto],
    tests: List[TestCaseDto],
    traceability: List[TraceabilityDto],
    architecture: ArchitectureDto,
    assumptions: List[AssumptionDto],
    used_fallback: bool,
    diagram_validation: Dict[str, Any],
    *,
    ai_report: Optional[AiTechnicalReportDto] = None,
    ai_applicable: bool = False,
) -> QualityAssessmentDto:
    """
    The SINGLE authoritative deterministic base-quality calculator for SE
    Documentation -- byte-for-byte the same criteria/weights/caps
    SEDocumentationOrchestratorAgent._compute_quality_assessment has always
    used at Writer-assembly time (that method is now a thin wrapper around
    this function, see its own docstring), extracted to a module-level
    function so it can ALSO be called again, unchanged, from a FINAL
    (post-repair/post-rebuild) candidate -- see
    app/agents/se_documentation/quality_recomputation.py's
    compute_documentation_quality, the dict-based entrypoint used after
    ReviewPipeline.run() returns. Never a second competing formula: both
    callers hit this exact function.
    """
    failed_checks: List[str] = []
    warnings: List[str] = []
    missing_information: List[str] = []
    critical_issues_count = 0

    # Completeness (18%)
    required_sections = [
        ("functionalRequirements", frs), ("nonFunctionalRequirements", nfrs),
        ("useCases", use_cases), ("edgeCases", edge_cases), ("systemModules", modules),
        ("databaseEntities", entities), ("uiScreens", ui_screens), ("testingPlan", tests),
    ]
    present = sum(1 for _, items in required_sections if items)
    completeness = round((present / len(required_sections)) * 100)
    for name, items in required_sections:
        if not items:
            failed_checks.append(f"{name} is empty.")
            missing_information.append(name)
            critical_issues_count += 1

    entities_with_empty_fields = [e.name for e in entities if not e.fields]
    if entities_with_empty_fields:
        completeness = min(completeness, 70)
        failed_checks.append(f"Entities with no field details: {', '.join(entities_with_empty_fields)}.")
        critical_issues_count += 1

    # Requirement testability (12%): fraction of FRs with non-empty acceptanceCriteria
    testable = sum(1 for fr in frs if fr.acceptanceCriteria) if frs else 0
    testability = round((testable / len(frs)) * 100) if frs else 0
    if frs and testable < len(frs):
        warnings.append(f"{len(frs) - testable} functional requirement(s) are missing acceptance criteria.")

    # Cross-section consistency (18%): referential integrity ratio plus
    # AI-approach / authentication consistency (a sanitization pass
    # having to correct either is itself evidence the sections
    # disagreed before the fix was applied).
    requirement_ids = {fr.id for fr in frs} | {nfr.id for nfr in nfrs}
    ref_total = 0
    ref_valid = 0
    for use_case in use_cases:
        for ref in use_case.relatedRequirements:
            ref_total += 1
            ref_valid += 1 if ref in requirement_ids else 0
    for test in tests:
        for ref in test.relatedRequirements:
            ref_total += 1
            ref_valid += 1 if ref in requirement_ids else 0
    consistency = round((ref_valid / ref_total) * 100) if ref_total else 100
    if ref_total and ref_valid < ref_total:
        failed_checks.append("Some use case/test requirement references do not point to a real requirement id.")

    text_blob_for_ai_auth = " ".join(
        [architecture.explanation, architecture.authenticationFlow]
        + [fr.description for fr in frs]
    ).lower()
    ai_conflict = facts.technical_profile.ai_approach not in ("rag", "hybrid") and (
        "retrieval-augmented" in text_blob_for_ai_auth or "fine-tun" in text_blob_for_ai_auth
    )
    auth_conflict = "jwt" not in facts.technical_profile.authentication_mechanism.lower() and "jwt" in text_blob_for_ai_auth
    if ai_conflict or auth_conflict:
        consistency = min(consistency, 60)
        failed_checks.append("AI approach or authentication mechanism is described inconsistently across sections.")
        critical_issues_count += 1

    # Traceability coverage (18%): every FR must appear with real
    # use-case/module/test mappings, not merely appear as a row.
    traced_ids = {row.requirementId for row in traceability}
    fully_covered = sum(1 for row in traceability if row.coverageStatus == "covered")
    traceability_coverage = round((fully_covered / len(frs)) * 100) if frs else 0
    untraced_count = len(frs) - len(traced_ids & {fr.id for fr in frs}) if frs else 0
    if frs and len(traced_ids) < len(frs):
        warnings.append("Not every functional requirement appears in the traceability matrix.")
        critical_issues_count += 1
    if untraced_count >= 4:
        failed_checks.append(f"{untraced_count} functional requirements are missing from traceability.")

    # Project specificity (12%)
    specificity = 100
    title_lower = facts.title.lower()
    if "fypilot" in title_lower or "final year project" in title_lower and "pilot" in title_lower:
        pass  # this genuinely is FYPilot; no penalty
    else:
        text_blob = " ".join(
            [architecture.explanation]
            + [fr.description for fr in frs[:5]]
            + [uc.goal for uc in use_cases[:5]]
        ).lower()
        if "fypilot" in text_blob:
            specificity = 0
            failed_checks.append("Generated content references FYPilot instead of the selected project.")
            critical_issues_count += 1
        elif facts.title and title_lower not in text_blob and facts.domain.lower() not in text_blob:
            specificity = 60
            warnings.append("Some sections do not clearly reference this project's title or domain.")

    # Diagram validity (8%): actually validated via mermaid_utils, never
    # assumed true.
    diagrams_ok = diagram_validation.get("ok", False)
    diagram_issues = diagram_validation.get("issues", [])
    if not diagrams_ok:
        warnings.extend(f"Diagram warning: {issue}" for issue in diagram_issues)
        failed_checks.append("One or more Mermaid diagrams failed structural validation.")

    # Assumption transparency (7%)
    assumption_transparency = 100
    if (used_fallback or facts.assumptions) and not assumptions:
        assumption_transparency = 0
        failed_checks.append("Assumptions were used but not disclosed in the assumptions section.")

    # Content depth (10%, content-depth batch): distinguishes genuinely
    # detailed content (multiple acceptance criteria/processing steps per
    # FR, multi-step use-case flows, entities with real domain fields,
    # fully-stated screen states, tests with real numbered steps, a
    # complete AI report) from shallow one-line filler -- the previous
    # score treated a 4-FR, one-sentence-each fallback identically to a
    # detailed one as long as the section was merely non-empty.
    def _avg(values: List[int]) -> float:
        return (sum(values) / len(values)) if values else 0.0

    avg_acceptance_criteria = _avg([len(fr.acceptanceCriteria) for fr in frs])
    avg_processing_steps = _avg([len([s for s in fr.systemBehavior.split(";") if s.strip()]) for fr in frs])
    avg_mainflow_steps = _avg([len(uc.mainFlow) for uc in use_cases])
    pct_entities_rich = (sum(1 for e in entities if len(e.fields) >= 5) / len(entities) * 100) if entities else 100
    pct_screens_full_states = (
        sum(1 for s in ui_screens if s.loadingState and s.emptyState and s.errorState and s.successState) / len(ui_screens) * 100
    ) if ui_screens else 100
    pct_tests_real_steps = (sum(1 for t in tests if len(t.steps) >= 2) / len(tests) * 100) if tests else 100

    ai_report_completeness = 100.0
    if ai_applicable:
        if ai_report is None:
            ai_report_completeness = 0.0
            failed_checks.append("AI technical report is applicable but missing.")
            critical_issues_count += 1
        else:
            report_values = ai_report.model_dump()
            filled = sum(1 for v in report_values.values() if v not in (None, "", []))
            ai_report_completeness = round((filled / len(report_values)) * 100) if report_values else 0
            if ai_report_completeness < 80:
                failed_checks.append("AI technical report has empty/placeholder fields.")

    depth_components = [
        min(100.0, (avg_acceptance_criteria / 2) * 100),
        min(100.0, (avg_processing_steps / 3) * 100),
        min(100.0, (avg_mainflow_steps / 5) * 100) if use_cases else 100.0,
        pct_entities_rich,
        pct_screens_full_states,
        pct_tests_real_steps,
        ai_report_completeness,
    ]
    content_depth = round(sum(depth_components) / len(depth_components))
    if content_depth < 60:
        warnings.append("Generated content is shallow (few acceptance criteria/processing steps/flow steps per item).")

    # Database quality (7%): PK presence, no plaintext password, valid FKs
    database_quality = 100
    entities_without_pk = [e.name for e in entities if not any(f.isPrimaryKey for f in e.fields)]
    if entities_without_pk:
        database_quality = min(database_quality, 50)
        failed_checks.append(f"Entities without a primary key: {', '.join(entities_without_pk)}.")
        critical_issues_count += 1
    plaintext_password_entities = [
        e.name for e in entities if any(f.name.strip().lower() == "password" for f in e.fields)
    ]
    if plaintext_password_entities:
        database_quality = min(database_quality, 40)
        failed_checks.append(f"Plaintext 'Password' field found on: {', '.join(plaintext_password_entities)}.")
        critical_issues_count += 1
    if entities_with_empty_fields:
        database_quality = min(database_quality, 40)

    criterion_values = {
        "completeness": completeness,
        "requirementTestability": testability,
        "crossSectionConsistency": consistency,
        "traceabilityCoverage": traceability_coverage,
        "projectSpecificity": specificity,
        "diagramValidity": 100 if diagrams_ok else 60,
        "assumptionTransparency": assumption_transparency,
        "databaseQuality": database_quality,
        "contentDepth": content_depth,
    }

    overall = round(sum(
        criterion_values[name] * weight for name, weight in QUALITY_CRITERION_WEIGHTS.items()
    ))
    if used_fallback:
        overall = min(overall, 70)
    if untraced_count >= 4:
        overall = min(overall, 75)
    if critical_issues_count > 0:
        overall = min(overall, 70)

    coverage_statistics = {
        "requirementsCoveredByUseCases": sum(1 for row in traceability if row.useCaseIds),
        "requirementsCoveredByTests": sum(1 for row in traceability if row.testCaseIds),
        "requirementsCoveredByModules": sum(1 for row in traceability if row.moduleIds),
        "requirementsCoveredByScreens": sum(1 for row in traceability if row.screenIds),
        "requirementsCoveredByEntities": sum(1 for row in traceability if row.entityIds),
        "requirementsCoveredByApis": sum(1 for row in traceability if row.apiIds),
        "totalFunctionalRequirements": len(frs),
    }

    return QualityAssessmentDto(
        overallScore=max(0, min(100, overall)),
        criterionScores={name: int(score) for name, score in criterion_values.items()},
        failedChecks=failed_checks,
        warnings=warnings,
        missingInformation=missing_information,
        assumptionsCount=len(assumptions),
        criticalIssuesCount=critical_issues_count,
        coverageStatistics=coverage_statistics,
    )


_FRONTEND_KEYWORDS = ["razor", "react", "angular", "vue", "blazor", "flutter", "android", "ios", "swift", "kotlin", "html", "css", "bootstrap", "tailwind", "next.js", "nextjs"]
_BACKEND_KEYWORDS = ["asp.net", "fastapi", "django", "flask", "node", "express", "spring", "laravel", ".net", "nestjs"]
_DB_KEYWORDS = ["postgres", "postgresql", "mysql", "sql server", "mongodb", "sqlite", "mariadb", "firebase", "redis", "oracle"]
