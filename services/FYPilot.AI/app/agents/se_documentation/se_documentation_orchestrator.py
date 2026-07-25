import json
import re
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
    ProjectFacts,
    TechnicalProfile,
    build_project_facts,
    facts_context_text,
    required_entities_for_text,
    required_screens_for_text,
)
from app.services.llm_provider import LLMResult, ProviderChain


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


class SEDocumentationOrchestratorAgent:
    def __init__(self):
        self.provider_chain = ProviderChain()
        self.last_llm_used = False
        self.last_error: Optional[str] = None
        self.last_raw_llm_response: Optional[str] = None
        self.last_provider: Optional[str] = None
        self.last_model_used: Optional[str] = None

    def generate(self, request: SEDocumentationRequest) -> SEDocumentationDto:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None

        facts = build_project_facts(request)

        try:
            llm_sections = self._generate_llm_sections(request, facts)
            if llm_sections:
                self.last_llm_used = True
                return self._assemble_documentation(request, facts, llm_sections, used_fallback=False)
        except Exception as e:
            self.last_error = str(e)

        return self._assemble_documentation(request, facts, {}, used_fallback=True)

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

    def generate_candidate(self, request: SEDocumentationRequest) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate() end to
        end (sequential LLM section calls -> deterministic assembly) rather
        than duplicating it, then wraps the result as an LLMResult so it can
        flow through guarded_call like any other LLM stage.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        generate() had to fall back internally (self.last_llm_used is False,
        meaning at least one of the section calls failed), since in that
        case there is no real candidate to review; the router should use
        build_safe_fallback() directly instead.
        """
        result = self.generate(request)

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

    def _generate_llm_sections(self, request: SEDocumentationRequest, facts: ProjectFacts) -> Dict[str, Any]:
        context = facts_context_text(facts)

        sections: Dict[str, Any] = {}

        sections["requirements"] = self._call_llm_json(
            prompt=f"""
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
- Generate between 10 and 16 functional requirements, scaled to this project's actual
  scope and complexity -- do not pad with unrelated or duplicate requirements.
- Generate between 6 and 10 non-functional requirements covering the categories most
  relevant to this project (performance, availability, security, privacy, usability,
  maintainability, scalability, reliability, accessibility, compatibility, recoverability,
  auditability) -- only include categories that make sense for this project.
- Every requirement must be atomic, testable, and specific to this project. Avoid vague
  wording like "user-friendly interface" -- state an observable, testable behavior instead.
- acceptanceCriteria must never be empty.
- Every measurableTarget must be a concrete number/threshold. If no target was confirmed by
  the student, prefix it with "Proposed acceptance target:" rather than presenting it as fact.
- Do not invent unrelated features (multilingual support, support tickets, external APIs,
  university credential integration, human escalation, etc.) unless implied by the project
  facts above; if you do include one of these, set sourceClassification to "assumption".
"""
        )

        sections["useCases"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

{context}

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
- Generate 5 to 9 use cases and 5 to 9 edge cases, scaled to this project's scope.
- The actor for every use case must be an external user or external system -- never the
  system's own AI/chat component itself acting as the actor.
- mainFlow must contain several meaningful numbered steps (not a single line).
- relatedRequirements / relatedRequirement must only reference requirement ids that make
  sense for this project (FR-xx or NFR-xx).
- Do not repeat the same edge case scenario under multiple ids.
"""
        )

        sections["modulesArchitecture"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

{context}

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
- Generate 5 to 9 implementation modules (backend/service-level components), not UI screens.
- Only reference the technologies listed as confirmed/assumed in the project facts above --
  never assume a technology stack that was not listed.
"""
        )

        sections["database"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

{context}

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
      "primaryKey": "Id", "foreignKeys": [], "uniqueConstraints": [], "indexes": [],
      "validationConstraints": [], "sensitiveFields": [],
      "relatedRequirementIds": ["FR-01"], "sourceClassification": "confirmed"
    }}
  ],
  "entityRelationships": [
    {{"fromEntity": "", "toEntity": "", "type": "one-to-many", "description": ""}}
  ]
}}

Rules:
- Generate 6 to 10 entities that are actually justified by this project's confirmed
  features -- if the project involves a knowledge base/FAQ, intents, training phrases,
  support tickets, feedback, unanswered-query review, roles, or configurable settings,
  include the corresponding entity.
- Every entity needs at least 3 meaningful fields (unless it is a pure many-to-many
  junction table) and exactly one primary key. Never leave "fields" empty.
- Every foreign key must reference a field on another entity in this same list, and the
  referenced field's dataType must match.
- Password fields must be named "PasswordHash" (a hash), never "Password" or a raw value.
- Every field needs a clear purpose -- no filler fields.
- Set isSensitive true for passwords/personal data and list them in sensitiveFields.
- relatedRequirementIds must reference real FR/NFR ids this entity supports.
"""
        )

        sections["uiApi"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

{context}

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
- Generate 6 to 12 screens covering every confirmed user-facing feature (including
  administrative/management screens like knowledge-base management, ticket tracking,
  feedback, analytics, configuration, or user/role management when those features are
  confirmed for this project).
- Only generate apiIntegrationPoints entries if the project facts mention external
  APIs/integrations; otherwise return an empty apiIntegrationPoints list.
- Do not guess an exact route path with confidence -- use a conceptual endpoint name and
  set sourceClassification to "assumption" unless the exact route was confirmed.
- relatedRequirements must reference real FR/NFR ids.
"""
        )

        sections["testingSecurity"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

{context}

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
- Generate 10 to 18 test cases and ensure EVERY functional requirement listed in the
  requirements context above is covered by at least one test case's relatedRequirements.
- Every high-priority functional requirement should have both a positive test and a
  negative-case test (set negativeCase true for the negative one).
- Never write a generic expectedResult like "works correctly" or "meets standards" --
  state the specific observable outcome, and always fill passCriteria with the concrete
  condition that makes the test pass.
- testData must contain a concrete example value, never be left empty.
- Cover unit, integration, API, database, security, usability, performance, and failure
  recovery categories as relevant; add an AI evaluation test only if this project has a
  confirmed/inferred AI component.
- Generate 5 to 10 security/privacy requirements covering authentication, authorization,
  input validation, secret management, session management, and data protection as relevant
  to this project. Do not claim GDPR/HIPAA compliance unless the project facts require it.
"""
        )

        if facts.ai_involved:
            sections["aiReport"] = self._call_llm_json(
                prompt=f"""
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
- Your "approach"/"modelOrApproach" MUST match the canonical AI approach given above
  ({facts.technical_profile.ai_approach}) -- do not describe a different approach.
- If AI provider type is "external_api", say so explicitly in "trainingVsInference" and
  do NOT call API prompting "model training" or "fine-tuning".
- If training mode is "local_supervised_training", describe it as local supervised
  training/model fitting, never as calling a third-party API.
- Do not mention retrieval-augmented generation (RAG) or vector databases unless the
  canonical AI approach above is "rag" or "hybrid".
- evaluationMetrics must be relevant to the actual AI task type described.
"""
            )

        return sections

    def _call_llm_json(self, prompt: str) -> Dict[str, Any]:
        result = self.provider_chain.generate_json(prompt, use_search=False)

        self.last_provider = result.provider if result.provider != "none" else None
        self.last_model_used = result.model

        if not result.ok or not isinstance(result.data, dict):
            raise RuntimeError(
                result.error or "No provider returned valid JSON for this section."
            )

        self.last_raw_llm_response = json.dumps(result.data, ensure_ascii=False)[:3000]

        return result.data

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

        warnings = []
        if used_fallback:
            warnings.append(
                "Some or all documentation sections were generated using deterministic "
                "fallback content because no AI provider returned valid JSON for this project."
            )
        if not profile.skills:
            warnings.append("Student skills were missing, so the documentation used general assumptions for this domain.")

        mermaid_erd = self._build_erd(entities, relationships)
        mermaid_class = self._build_class_diagram(entities)
        activity_diagram = self._build_activity_diagram(facts, use_cases)
        sequence_diagram = self._build_sequence_diagram(facts, architecture, use_cases)

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
            data.setdefault("aiService", "Not applicable" if not facts.ai_involved else "AI/LLM component")
            data.setdefault("externalServices", [])
            data.setdefault("explanation", data.get("explanation") or "")
            return ArchitectureDto.model_validate(data)
        except Exception:
            return self._fallback_architecture(facts)

    def _ai_report_or_fallback(self, raw: Any, facts: ProjectFacts) -> AiTechnicalReportDto:
        try:
            if raw:
                return AiTechnicalReportDto.model_validate(raw)
        except Exception:
            pass
        return AiTechnicalReportDto(
            problemDefinition=f"AI/ML/NLP component detected for {facts.title}; exact approach not yet confirmed by the student.",
            taskType="Not yet confirmed (assumption)",
            limitations="This section is a placeholder because no AI provider returned a valid technical report; regenerate once available.",
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

    def _fallback_functional_requirements(self, facts: ProjectFacts) -> List[RequirementDto]:
        actor = facts.primary_actor
        domain = facts.domain
        return [
            RequirementDto(
                id="FR-01", title="Authenticate users",
                description=f"After a {actor.lower()} submits valid credentials, the system shall grant access to their account within the configured session policy.",
                rationale="Access control is required before any project-specific feature can be used.",
                primaryActor=actor, priority="High", source="Assumption",
                acceptanceCriteria=[f"A {actor.lower()} with valid credentials can log in and reach the main screen."],
                sourceClassification="assumption",
            ),
            RequirementDto(
                id="FR-02", title=f"Manage core {domain.lower()} records",
                description=f"After an authenticated {actor.lower()} creates or edits a core {domain.lower()} record, the system shall persist the change and reflect it immediately in the relevant screen.",
                rationale=f"{facts.title} requires a persistent record of its core {domain.lower()} data.",
                primaryActor=actor, priority="High", source="Assumption",
                acceptanceCriteria=["A created/edited record is visible after a page refresh."],
                sourceClassification="assumption",
            ),
            RequirementDto(
                id="FR-03", title="Validate user input",
                description=f"When a {actor.lower()} submits a form with missing or invalid required fields, the system shall reject the submission and display a specific validation message.",
                rationale="Prevents invalid data from being persisted.",
                primaryActor=actor, priority="High", source="Assumption",
                acceptanceCriteria=["Submitting an incomplete form shows a field-specific error and does not persist data."],
                sourceClassification="assumption",
            ),
            RequirementDto(
                id="FR-04", title="Display a summary view",
                description=f"After an authenticated {actor.lower()} opens the main screen, the system shall display a summary of their most relevant {domain.lower()} information.",
                rationale="Supports the project's core objective of surfacing relevant information quickly.",
                primaryActor=actor, priority="Medium", source="Assumption",
                acceptanceCriteria=["The summary view loads within the configured performance target."],
                sourceClassification="assumption",
            ),
        ]

    def _fallback_nonfunctional_requirements(self, facts: ProjectFacts) -> List[RequirementDto]:
        return [
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
        ]

    def _fallback_use_cases(self, facts: ProjectFacts) -> List[UseCaseDto]:
        actor = facts.primary_actor
        return [
            UseCaseDto(
                id="UC-01", title="Authenticate", actor=actor, goal="Gain access to the system.",
                trigger=f"{actor} opens the application and provides credentials.",
                preconditions=[f"{actor} has a registered account."],
                mainFlow=["1. Actor opens the login screen.", "2. Actor enters credentials.", "3. System validates credentials.", "4. System grants access to the main screen."],
                alternativeFlow=["3a. If credentials are invalid, the system displays an error and remains on the login screen."],
                postconditions=["Actor has an authenticated session."],
                relatedRequirements=["FR-01"], sourceClassification="assumption",
            ),
            UseCaseDto(
                id="UC-02", title=f"Manage core {facts.domain.lower()} record", actor=actor, goal=f"Create or update a {facts.domain.lower()} record.",
                trigger=f"{actor} chooses to create or edit a record.",
                preconditions=["Actor is authenticated."],
                mainFlow=["1. Actor opens the record form.", "2. Actor enters required fields.", "3. System validates input.", "4. System persists the record.", "5. System confirms success."],
                alternativeFlow=["3a. If validation fails, the system highlights the invalid fields."],
                postconditions=["Record is persisted and visible to the actor."],
                relatedRequirements=["FR-02", "FR-03"], sourceClassification="assumption",
            ),
            UseCaseDto(
                id="UC-03", title="View summary", actor=actor, goal="Review current relevant information at a glance.",
                trigger=f"{actor} opens the main screen after authenticating.",
                preconditions=["Actor is authenticated."],
                mainFlow=["1. Actor opens the main screen.", "2. System loads the actor's records.", "3. System renders a summary view."],
                alternativeFlow=["2a. If no records exist yet, the system shows an empty state with guidance."],
                postconditions=["Actor has viewed the summary."],
                relatedRequirements=["FR-04"], sourceClassification="assumption",
            ),
        ]

    def _fallback_edge_cases(self, facts: ProjectFacts) -> List[EdgeCaseDto]:
        return [
            EdgeCaseDto(id="EC-01", scenario="Actor submits an empty required field.", expectedHandling="Reject the submission and show a field-specific message.", relatedRequirement="FR-03", severity="Medium", recoveryAction="Actor corrects the field and resubmits.", userMessage="This field is required.", loggingRequirement="Log validation failure at debug level.", testScenario="Submit form with the field blank."),
            EdgeCaseDto(id="EC-02", scenario="Database is temporarily unavailable.", expectedHandling="Show a generic service-unavailable message and avoid data loss.", relatedRequirement="NFR-03", severity="High", recoveryAction="Retry after the database recovers; queue the action if applicable.", userMessage="The service is temporarily unavailable. Please try again shortly.", loggingRequirement="Log the exception with a correlation id.", testScenario="Simulate a database outage during a write."),
            EdgeCaseDto(id="EC-03", scenario="Unauthorized actor attempts to access another actor's record.", expectedHandling="Reject the request with an authorization error.", relatedRequirement="NFR-01", severity="High", recoveryAction="No recovery; request is denied.", userMessage="You do not have permission to view this record.", loggingRequirement="Log the unauthorized attempt with actor id.", testScenario="Request another actor's record id directly."),
            EdgeCaseDto(id="EC-04", scenario="Session expires mid-action.", expectedHandling="Redirect to login and preserve unsaved input where possible.", relatedRequirement="FR-01", severity="Medium", recoveryAction="Actor re-authenticates and resumes.", userMessage="Your session has expired. Please log in again.", loggingRequirement="Log session expiry event.", testScenario="Let the session token expire before submitting a form."),
        ]

    def _fallback_modules(self, facts: ProjectFacts) -> List[ModuleDto]:
        return [
            ModuleDto(id="MOD-01", name="Authentication Module", responsibility="Handles login, session, and access control.", inputs=["Credentials"], outputs=["Authenticated session"], relatedRequirements=["FR-01"], failureBehavior="Rejects the request and returns an authentication error.", sourceClassification="assumption"),
            ModuleDto(id="MOD-02", name=f"{facts.domain} Records Module", responsibility=f"Creates, updates, and stores core {facts.domain.lower()} records.", inputs=["Record data"], outputs=["Persisted record"], relatedRequirements=["FR-02", "FR-03"], failureBehavior="Returns a validation error without persisting invalid data.", sourceClassification="assumption"),
            ModuleDto(id="MOD-03", name="Summary Module", responsibility="Aggregates and renders the actor's summary view.", inputs=["Stored records"], outputs=["Summary data"], relatedRequirements=["FR-04"], failureBehavior="Shows an empty/error state instead of crashing.", sourceClassification="assumption"),
        ]

    def _fallback_entities(self, facts: ProjectFacts) -> List[EntityDto]:
        return [
            EntityDto(
                name="User", purpose="Stores account and authentication information.",
                importantFields=["Id", "Email", "PasswordHash", "Role"],
                fields=[
                    EntityFieldDto(name="Id", dataType="int", nullable=False, description="Primary key.", constraints="primary key"),
                    EntityFieldDto(name="Email", dataType="string", nullable=False, description="Login identifier.", constraints="unique"),
                    EntityFieldDto(name="PasswordHash", dataType="string", nullable=False, description="Hashed credential.", constraints="sensitive"),
                    EntityFieldDto(name="Role", dataType="string", nullable=False, description="Access role."),
                ],
                primaryKey="Id", uniqueConstraints=["Email"], indexes=["Email"],
                relationships=[f"User has many {facts.domain}Record"], sourceClassification="assumption",
                relatedRequirementIds=["FR-01"],
            ),
            EntityDto(
                name=f"{facts.domain.replace(' ', '')}Record", purpose=f"Stores core {facts.domain.lower()} data owned by a user.",
                importantFields=["Id", "UserId", "Title", "CreatedAt"],
                fields=[
                    EntityFieldDto(name="Id", dataType="int", nullable=False, description="Primary key.", constraints="primary key"),
                    EntityFieldDto(name="UserId", dataType="int", nullable=False, description="Owning user.", constraints="foreign key -> User.Id"),
                    EntityFieldDto(name="Title", dataType="string", nullable=False, description="Record title."),
                    EntityFieldDto(name="CreatedAt", dataType="datetime", nullable=False, description="Creation timestamp."),
                ],
                primaryKey="Id", foreignKeys=["UserId -> User.Id"], indexes=["UserId"],
                relationships=["Belongs to User"], sourceClassification="assumption",
                relatedRequirementIds=["FR-02", "FR-03"],
            ),
        ]

    def _fallback_relationships(self, entities: List[EntityDto]) -> List[RelationshipDto]:
        if len(entities) >= 2:
            return [
                RelationshipDto(fromEntity=entities[0].name, toEntity=entities[1].name, type="one-to-many", description=f"A {entities[0].name} owns many {entities[1].name} records."),
            ]
        return []

    def _fallback_tests(self, facts: ProjectFacts) -> List[TestCaseDto]:
        return [
            TestCaseDto(id="TC-01", title="Login with valid credentials", type="Functional", steps=["Open login screen.", "Enter valid credentials.", "Submit."], expectedResult="Actor reaches the main screen.", relatedRequirements=["FR-01"], priority="High"),
            TestCaseDto(id="TC-02", title="Reject invalid record submission", type="Functional", steps=["Open the record form.", "Leave a required field blank.", "Submit."], expectedResult="Validation error is shown; no record is persisted.", relatedRequirements=["FR-03"], priority="High"),
            TestCaseDto(id="TC-03", title="Unauthorized access is rejected", type="Security", steps=["Authenticate as actor A.", "Request actor B's record by id."], expectedResult="Request is denied with an authorization error.", relatedRequirements=["NFR-01"], priority="High"),
            TestCaseDto(id="TC-04", title="Database outage handling", type="Failure recovery", steps=["Simulate database outage.", "Attempt to save a record."], expectedResult="A clear service-unavailable message is shown; no partial data is persisted.", relatedRequirements=["NFR-03"], priority="Medium"),
        ]

    def _fallback_ui_screens(self, facts: ProjectFacts) -> List[UiScreenDto]:
        actor = facts.primary_actor
        return [
            UiScreenDto(screenId="UI-01", name="Login", authorizedRoles=[actor], purpose="Authenticate the actor.", mainComponents=["Email field", "Password field", "Submit button"], userActions=["Enter credentials", "Submit"], validationRules=["Both fields required"], loadingState="Shows a spinner while credentials are verified.", emptyState="Not applicable.", errorState="Shows an inline error for invalid credentials.", successState="Redirects to the main screen.", relatedUseCases=["UC-01"], relatedRequirements=["FR-01"], sourceClassification="assumption"),
            UiScreenDto(screenId="UI-02", name=f"{facts.domain} Records", authorizedRoles=[actor], purpose=f"List and manage {facts.domain.lower()} records.", mainComponents=["Record list", "Create button"], userActions=["Create record", "Edit record", "Delete record"], validationRules=["Required fields enforced on the record form"], loadingState="Shows a skeleton list while records load.", emptyState="Shows guidance to create the first record.", errorState="Shows a retry option on load failure.", successState="Shows the updated record list.", relatedUseCases=["UC-02"], relatedRequirements=["FR-02", "FR-03"], sourceClassification="assumption"),
            UiScreenDto(screenId="UI-03", name="Summary Dashboard", authorizedRoles=[actor], purpose="Show the actor's current relevant information.", mainComponents=["Summary cards"], userActions=["Open a record from the summary"], validationRules=[], loadingState="Shows placeholders while summary data loads.", emptyState="Shows a call-to-action when no data exists yet.", errorState="Shows a retry option on load failure.", successState="Shows the populated summary.", relatedUseCases=["UC-03"], relatedRequirements=["FR-04"], sourceClassification="assumption"),
        ]

    def _fallback_security(self, facts: ProjectFacts) -> List[SecurityItemDto]:
        return [
            SecurityItemDto(category="authentication", requirement="Passwords must be stored using a strong one-way hash (e.g. bcrypt/argon2), never in plain text.", rationale="Protects credentials if the database is compromised."),
            SecurityItemDto(category="authorization", requirement="Every data-access operation must verify the requesting actor owns or is authorized for the target record.", rationale="Prevents cross-account data access."),
            SecurityItemDto(category="input validation", requirement="All user-submitted input must be validated server-side, not only client-side.", rationale="Client-side validation can be bypassed."),
            SecurityItemDto(category="session management", requirement="Sessions must expire after a configured inactivity period.", rationale="Limits exposure from an unattended, authenticated device."),
            SecurityItemDto(category="secret management", requirement="API keys and connection strings must be stored in environment configuration, never committed to source control.", rationale="Prevents credential leakage."),
        ]

    def _fallback_architecture(self, facts: ProjectFacts) -> ArchitectureDto:
        confirmed = facts.confirmed_technology_names()
        frontend = self._pick_layer(facts, _FRONTEND_KEYWORDS, "Frontend (not confirmed)")
        backend = self._pick_layer(facts, _BACKEND_KEYWORDS, "Backend (not confirmed)")
        database = self._pick_layer(facts, _DB_KEYWORDS, "Database (not confirmed)")

        return ArchitectureDto(
            style="Layered client-server architecture" if confirmed else "Layered client-server architecture (assumed; technology stack not confirmed)",
            frontend=frontend, backend=backend, database=database,
            aiService="AI/LLM component" if facts.ai_involved else "Not applicable",
            externalServices=[],
            explanation=(
                f"{facts.title} is expected to follow a layered architecture: a presentation layer for {facts.primary_actor}, "
                f"an application/service layer implementing the confirmed features, and a persistence layer for the entities "
                f"described in this document."
            ),
            components=[f"{frontend}: presentation layer", f"{backend}: application/service layer", f"{database}: persistence layer"],
            dataFlow=f"{facts.primary_actor} interacts with {frontend}, which calls {backend}, which reads/writes {database}.",
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

    def _reconcile_requirement_references(
        self,
        requirement_ids: set,
        use_cases: List[UseCaseDto],
        edge_cases: List[EdgeCaseDto],
        modules: List[ModuleDto],
        tests: List[TestCaseDto],
    ) -> None:
        """Repair any relatedRequirements/relatedRequirement reference that
        doesn't correspond to a real FR/NFR id -- can happen when one
        section's content came from the LLM (its own id scheme) while
        another fell back to hardcoded ids, or the LLM itself hallucinated a
        reference. Mutates in place; falls back to the first real
        requirement id rather than leaving a list empty."""
        if not requirement_ids:
            return

        default_id = next(iter(sorted(requirement_ids)))

        for use_case in use_cases:
            valid = [r for r in use_case.relatedRequirements if r in requirement_ids]
            use_case.relatedRequirements = valid or [default_id]

        for edge_case in edge_cases:
            if edge_case.relatedRequirement not in requirement_ids:
                edge_case.relatedRequirement = default_id

        for module in modules:
            valid = [r for r in module.relatedRequirements if r in requirement_ids]
            module.relatedRequirements = valid or [default_id]

        for test in tests:
            valid = [r for r in test.relatedRequirements if r in requirement_ids]
            test.relatedRequirements = valid or [default_id]

    def _reconcile_screen_references(self, requirement_ids: set, ui_screens: List[UiScreenDto]) -> None:
        if not requirement_ids:
            return
        default_id = next(iter(sorted(requirement_ids)))
        for screen in ui_screens:
            valid = [r for r in screen.relatedRequirements if r in requirement_ids]
            screen.relatedRequirements = valid or [default_id]

    def _reconcile_entity_and_api_references(
        self, requirement_ids: set, entities: List[EntityDto], api_points: List[ApiPointDto]
    ) -> None:
        if not requirement_ids:
            return
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

    def _build_activity_diagram(self, facts: ProjectFacts, use_cases: List[UseCaseDto]) -> str:
        """Deterministically derived from the project's own primary use case
        main flow, instead of a hardcoded FYPilot doc-generator pipeline.
        Labels are routed through mermaid_utils.safe_label so no node is a
        raw, overly long use-case sentence and none is truncated mid-word."""
        primary = use_cases[0] if use_cases else None

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

        return "\n".join(lines)

    def _build_sequence_diagram(self, facts: ProjectFacts, architecture: ArchitectureDto, use_cases: List[UseCaseDto]) -> str:
        """Deterministically derived from this project's architecture
        components and primary use case, instead of a hardcoded FYPilot
        Razor/FastAPI/Ollama pipeline. Participants are declared once up
        front with readable aliases (mermaid_utils.participant_declaration)
        instead of squashing a combined actor description like "University
        students and support staff" into a single illegible identifier."""
        primary = use_cases[0] if use_cases else None

        actor_names = split_combined_actor(facts.primary_actor) or [facts.primary_actor or "User"]
        primary_actor_display = actor_names[0]
        actor_id = safe_participant_id(primary_actor_display)

        frontend_id = safe_participant_id(architecture.frontend)
        backend_id = safe_participant_id(architecture.backend)
        database_id = safe_participant_id(architecture.database)

        lines = ["sequenceDiagram"]
        lines.append(participant_declaration(actor_id, primary_actor_display, is_actor=True))
        lines.append(participant_declaration(frontend_id, architecture.frontend))
        lines.append(participant_declaration(backend_id, architecture.backend))
        lines.append(participant_declaration(database_id, architecture.database))

        trigger = primary.trigger if primary and primary.trigger else f"Perform primary action in {facts.title}"

        lines.append(f"    {actor_id}->>{frontend_id}: {safe_label(trigger)}")
        lines.append(f"    {frontend_id}->>{backend_id}: Submit request")
        if facts.technical_profile.ai_enabled:
            ai_id = safe_participant_id("AIService")
            lines.append(participant_declaration(ai_id, "AI Service"))
            lines.append(f"    {backend_id}->>{ai_id}: Request AI processing")
            lines.append(f"    {ai_id}-->>{backend_id}: Return AI result")
        lines.append(f"    {backend_id}->>{database_id}: Read/write data")
        lines.append(f"    {database_id}-->>{backend_id}: Return data")
        lines.append(f"    {backend_id}-->>{frontend_id}: Return response")
        lines.append(f"    {frontend_id}-->>{actor_id}: Display result")

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
    ) -> QualityAssessmentDto:
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

        weights = {
            "completeness": (completeness, 0.18),
            "requirementTestability": (testability, 0.12),
            "crossSectionConsistency": (consistency, 0.18),
            "traceabilityCoverage": (traceability_coverage, 0.18),
            "projectSpecificity": (specificity, 0.12),
            "diagramValidity": (100 if diagrams_ok else 60, 0.08),
            "assumptionTransparency": (assumption_transparency, 0.07),
            "databaseQuality": (database_quality, 0.07),
        }

        overall = round(sum(score * weight for score, weight in weights.values()))
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
            criterionScores={name: int(score) for name, (score, _weight) in weights.items()},
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
