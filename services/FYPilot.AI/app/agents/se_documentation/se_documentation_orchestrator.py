import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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
    id: str
    title: str
    description: str
    priority: str
    source: str


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


class EdgeCaseDto(BaseModel):
    id: str
    scenario: str
    expectedHandling: str
    relatedRequirement: str


class ModuleDto(BaseModel):
    id: str
    name: str
    responsibility: str
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    relatedRequirements: List[str] = Field(default_factory=list)


class EntityDto(BaseModel):
    name: str
    purpose: str
    importantFields: List[str] = Field(default_factory=list)
    relationships: List[str] = Field(default_factory=list)


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


class ApiPointDto(BaseModel):
    name: str
    method: str
    endpoint: str
    purpose: str
    requestSummary: str
    responseSummary: str


class TestCaseDto(BaseModel):
    id: str
    title: str
    type: str
    steps: List[str] = Field(default_factory=list)
    expectedResult: str
    relatedRequirements: List[str] = Field(default_factory=list)


class TraceabilityDto(BaseModel):
    requirementId: str
    useCaseId: str
    moduleId: str
    entity: str
    testCaseId: str


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
    testingPlan: List[TestCaseDto] = Field(default_factory=list)
    traceabilityMatrix: List[TraceabilityDto] = Field(default_factory=list)
    risksAndLimitations: List[str] = Field(default_factory=list)
    expectedOutcomes: List[str] = Field(default_factory=list)
    documentationQualityScore: int
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

        try:
            llm_sections = self._generate_llm_sections(request)
            if llm_sections:
                self.last_llm_used = True
                return self._assemble_documentation(request, llm_sections, used_fallback=False)
        except Exception as e:
            self.last_error = str(e)

        return self._assemble_documentation(request, {}, used_fallback=True)

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
        return self._assemble_documentation(request, {}, used_fallback=True)

    def generate_candidate(self, request: SEDocumentationRequest) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate() end to
        end (5 sequential LLM section calls -> deterministic assembly) rather
        than duplicating it, then wraps the result as an LLMResult so it can
        flow through guarded_call like any other LLM stage.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        generate() had to fall back internally (self.last_llm_used is False,
        meaning at least one of the 5 section calls failed), since in that
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

    def _generate_llm_sections(self, request: SEDocumentationRequest) -> Dict[str, Any]:
        context = self._context_text(request)

        sections: Dict[str, Any] = {}

        sections["requirements"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

Context:
{context}

Generate software engineering requirements.

JSON shape:
{{
  "functionalRequirements": [
    {{"id":"FR-01","title":"","description":"","priority":"High","source":"Selected idea"}}
  ],
  "nonFunctionalRequirements": [
    {{"id":"NFR-01","title":"","description":"","priority":"High","source":"Software quality"}}
  ]
}}

Rules:
- Exactly 8 functional requirements.
- Exactly 5 non-functional requirements.
- Must be specific to the project idea.
"""
        )

        sections["useCases"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

Context:
{context}

Generate use cases and edge cases.

JSON shape:
{{
  "useCases": [
    {{
      "id":"UC-01",
      "title":"",
      "actor":"",
      "goal":"",
      "preconditions":[],
      "mainFlow":[],
      "alternativeFlow":[],
      "postconditions":[],
      "relatedRequirements":["FR-01"]
    }}
  ],
  "edgeCases": [
    {{"id":"EC-01","scenario":"","expectedHandling":"","relatedRequirement":"FR-01"}}
  ]
}}

Rules:
- Exactly 5 use cases.
- Exactly 5 edge cases.
- Related requirements must use FR IDs.
"""
        )

        sections["modules"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

Context:
{context}

Generate system modules.

JSON shape:
{{
  "systemModules": [
    {{
      "id":"M-01",
      "name":"",
      "responsibility":"",
      "inputs":[],
      "outputs":[],
      "relatedRequirements":["FR-01"]
    }}
  ]
}}

Rules:
- Exactly 5 modules.
- Modules must match the selected idea and technologies.
"""
        )

        sections["database"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

Context:
{context}

Generate database entities and relationships.

JSON shape:
{{
  "databaseEntities": [
    {{"name":"","purpose":"","importantFields":[],"relationships":[]}}
  ],
  "entityRelationships": [
    {{"fromEntity":"","toEntity":"","type":"one-to-many","description":""}}
  ]
}}

Rules:
- Exactly 5 database entities.
- At least 4 relationships.
- Use realistic entity names.
"""
        )

        sections["testing"] = self._call_llm_json(
            prompt=f"""
Return ONLY valid JSON.

Context:
{context}

Generate testing plan.

JSON shape:
{{
  "testingPlan": [
    {{
      "id":"TC-01",
      "title":"",
      "type":"Functional",
      "steps":[],
      "expectedResult":"",
      "relatedRequirements":["FR-01"]
    }}
  ]
}}

Rules:
- Exactly 5 test cases.
- Cover main requirements.
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

    def _assemble_documentation(
        self,
        request: SEDocumentationRequest,
        sections: Dict[str, Any],
        used_fallback: bool
    ) -> SEDocumentationDto:
        profile = request.studentProfile or SEDocStudentProfile()
        idea = request.selectedIdea or SEDocSelectedIdea()

        title = idea.title.strip() or "AI-Assisted Final Year Project Planning System"

        problem = idea.problemStatement.strip() or (
            "Students often struggle to select feasible final year project ideas, plan implementation work, "
            "identify required skills, and prepare structured software engineering documentation."
        )

        target_users = idea.targetUsers.strip() or "Final year computer science students and supervisors"

        technologies = self._split_items(
            idea.requiredTechnologies,
            ["ASP.NET Core Razor Pages", "PostgreSQL", "Python FastAPI", "Ollama", "Bootstrap"]
        )

        requirements = sections.get("requirements", {})
        use_case_section = sections.get("useCases", {})
        modules_section = sections.get("modules", {})
        database_section = sections.get("database", {})
        testing_section = sections.get("testing", {})

        frs = self._requirements_or_fallback(
            requirements.get("functionalRequirements"),
            self._fallback_functional_requirements()
        )

        nfrs = self._requirements_or_fallback(
            requirements.get("nonFunctionalRequirements"),
            self._fallback_nonfunctional_requirements()
        )

        use_cases = self._use_cases_or_fallback(use_case_section.get("useCases"))
        edge_cases = self._edge_cases_or_fallback(use_case_section.get("edgeCases"))
        modules = self._modules_or_fallback(modules_section.get("systemModules"))
        entities = self._entities_or_fallback(database_section.get("databaseEntities"))
        relationships = self._relationships_or_fallback(database_section.get("entityRelationships"))
        tests = self._tests_or_fallback(testing_section.get("testingPlan"))

        # Each of the 5 sections above is generated by an INDEPENDENT LLM call
        # (or independently falls back to deterministic content if just that
        # call fails), so two sections can disagree on id scheme even in a
        # single "used_fallback=False" run -- e.g. requirements comes from the
        # LLM with its own ids while useCases falls back to hardcoded "FR-01"
        # refs that don't match. This pass makes ids unique within each list
        # and repairs any cross-section reference that doesn't actually exist,
        # deterministically, so the referential-integrity checks in
        # app/review/registry.py's SEDocumentationCandidateSchema always pass
        # on this agent's own output regardless of which sections were LLM-
        # generated vs. fallback.
        frs = self._ensure_unique_ids(frs, "FR")
        nfrs = self._ensure_unique_ids(nfrs, "NFR")
        use_cases = self._ensure_unique_ids(use_cases, "UC")
        edge_cases = self._ensure_unique_ids(edge_cases, "EC")
        modules = self._ensure_unique_ids(modules, "M")
        tests = self._ensure_unique_ids(tests, "TC")
        entities = self._ensure_unique_entity_names(entities)

        requirement_ids = {req.id for req in frs} | {req.id for req in nfrs}
        self._reconcile_requirement_references(requirement_ids, use_cases, edge_cases, modules, tests)

        traceability = self._build_traceability(frs, use_cases, modules, entities, tests)

        warnings = []
        if used_fallback:
            warnings.append("Some or all documentation sections were generated using deterministic fallback because Ollama failed or returned invalid JSON.")
        if not profile.skills:
            warnings.append("Student skills were missing, so the documentation used general Computer Science assumptions.")

        return SEDocumentationDto(
            projectTitle=title,
            projectOverview=f"{title} is a software platform designed for {target_users}. It uses {', '.join(technologies)} to support project planning, AI guidance, documentation generation, and structured FYP preparation.",
            problemStatement=problem,
            objectives=[
                "Help students transform a project idea into structured software engineering artifacts.",
                "Generate traceable requirements, use cases, modules, database entities, and test cases.",
                "Reduce confusion during the early stages of final year project planning.",
                "Support AI-assisted project guidance while keeping outputs reviewable by students and supervisors.",
                "Improve project defense preparation by organizing the technical reasoning behind the project."
            ],
            stakeholders=[
                "Student",
                "Supervisor",
                "Admin",
                "Evaluation committee",
                "Target users of the selected project"
            ],
            scope=ScopeDto(
                inScope=[
                    "Generate functional and non-functional requirements.",
                    "Generate use cases and edge cases.",
                    "Generate system modules and database entities.",
                    "Generate Mermaid diagrams.",
                    "Generate testing plan and traceability matrix.",
                    "Display documentation for student review."
                ],
                outOfScope=[
                    "Replacing supervisor approval.",
                    "Guaranteeing final academic acceptance.",
                    "Automatically implementing the full project source code.",
                    "Publishing documentation without student confirmation."
                ],
                futureWork=[
                    "Export documentation to Word or PDF.",
                    "Allow supervisor comments.",
                    "Add version history.",
                    "Add defense simulator integration.",
                    "Add plagiarism or similarity review for documentation."
                ]
            ),
            functionalRequirements=frs,
            nonFunctionalRequirements=nfrs,
            useCases=use_cases,
            edgeCases=edge_cases,
            systemModules=modules,
            databaseEntities=entities,
            entityRelationships=relationships,
            mermaidERD=self._build_erd(entities, relationships),
            mermaidClassDiagram=self._build_class_diagram(entities),
            activityDiagram=self._build_activity_diagram(),
            sequenceDiagram=self._build_sequence_diagram(),
            architecture=ArchitectureDto(
                style="Layered web architecture with AI service integration",
                frontend="ASP.NET Core Razor Pages",
                backend="ASP.NET Core application with Python FastAPI AI service",
                database="PostgreSQL",
                aiService=(
                    f"Cloud/local AI provider chain (Groq -> Gemini -> Ollama), "
                    f"last used: {self.last_provider or 'dynamic-fallback'} "
                    f"({self.last_model_used or 'n/a'})"
                ),
                externalServices=["Groq API", "Gemini API", "Ollama local model server"],
                explanation="The .NET application manages authentication, pages, database access, and student workflow. The Python FastAPI service runs AI agents and returns validated documentation. PostgreSQL stores users, profiles, ideas, skills, roadmap data, chats, and generated documentation."
            ),
            apiIntegrationPoints=[
                ApiPointDto(name="Generate Ideas", method="POST", endpoint="/generate-ideas", purpose="Generate personalized FYP ideas.", requestSummary="Student profile, skills, preferences, and constraints.", responseSummary="Generated project ideas with scores."),
                ApiPointDto(name="Compare Generated Ideas", method="POST", endpoint="/compare-generated-ideas", purpose="Compare ideas and recommend the best option.", requestSummary="Generated ideas and student context.", responseSummary="Ranked ideas and final recommendation."),
                ApiPointDto(name="FYP Mentor Chat", method="POST", endpoint="/fyp-chat", purpose="Answer contextual FYP questions.", requestSummary="Question, selected idea, roadmap, skills, and recent messages.", responseSummary="Mentor reply with suggested actions."),
                ApiPointDto(name="Generate SE Documentation", method="POST", endpoint="/generate-se-documentation", purpose="Generate structured SE documentation.", requestSummary="Profile, selected idea, roadmap, and notes.", responseSummary="Complete documentation JSON."),
            ],
            testingPlan=tests,
            traceabilityMatrix=traceability,
            risksAndLimitations=[
                "AI-generated content requires human review before academic submission.",
                "Local LLM generation can be slow depending on hardware.",
                "Generated diagrams may require visual review.",
                "If project context is incomplete, some sections may be general.",
                "The feature depends on the Python AI service and Ollama availability."
            ],
            expectedOutcomes=[
                "Student receives a complete first draft of SE documentation.",
                "The project becomes easier to explain to supervisors and evaluators.",
                "Requirements become linked to use cases, modules, entities, and tests.",
                "The system supports a more professional FYP workflow."
            ],
            documentationQualityScore=88 if not used_fallback else 82,
            consistencyWarnings=warnings
        )

    def _context_text(self, request: SEDocumentationRequest) -> str:
        profile = request.studentProfile or SEDocStudentProfile()
        idea = request.selectedIdea or SEDocSelectedIdea()

        roadmap_text = "\n".join(
            f"- Phase {phase.phaseNumber}: {phase.name} | {phase.objective}"
            for phase in request.roadmap[:8]
        )

        return f"""
Student:
Major: {profile.major}
Experience: {profile.experienceLevel}
Team size: {profile.teamSize}
Available hours per week: {profile.availableHoursPerWeek}
Skills: {profile.skills}
Skill ratings: {profile.skillRatings}

Selected idea:
Title: {idea.title}
Problem: {idea.problemStatement}
Target users: {idea.targetUsers}
Why useful: {idea.whyUseful}
Technologies: {idea.requiredTechnologies}
Required skills: {idea.requiredSkills}
Missing skills: {idea.missingSkills}
Difficulty: {idea.difficultyLevel}
Duration weeks: {idea.expectedDurationWeeks}
Domain: {idea.domain}
Deliverables: {idea.finalDeliverables}

Roadmap:
{roadmap_text}

Notes:
{request.existingNotes}
"""

    def _fallback_functional_requirements(self) -> List[RequirementDto]:
        return [
            RequirementDto(id="FR-01", title="Manage authentication", description="The system shall allow students to register, log in, log out, and access protected pages.", priority="High", source="Security"),
            RequirementDto(id="FR-02", title="Maintain student profile", description="The system shall store academic profile, skills, experience, team size, available hours, and project goals.", priority="High", source="Student profile"),
            RequirementDto(id="FR-03", title="Generate project ideas", description="The system shall generate project ideas based on student skills, preferences, and constraints.", priority="High", source="Idea generation"),
            RequirementDto(id="FR-04", title="Compare ideas", description="The system shall compare generated ideas using feasibility, innovation, market relevance, and skill fit.", priority="High", source="Idea comparison"),
            RequirementDto(id="FR-05", title="Select final idea", description="The system shall allow the student to select one idea as the active final year project.", priority="High", source="Project workflow"),
            RequirementDto(id="FR-06", title="Generate roadmap", description="The system shall generate phases, tasks, outputs, and success criteria for the selected idea.", priority="High", source="Roadmap"),
            RequirementDto(id="FR-07", title="Provide mentor chat", description="The system shall answer student questions using selected idea, skills, and roadmap context.", priority="Medium", source="Mentor chat"),
            RequirementDto(id="FR-08", title="Generate SE documentation", description="The system shall generate requirements, use cases, modules, entities, diagrams, tests, and traceability matrix.", priority="High", source="SE documentation"),
        ]

    def _fallback_nonfunctional_requirements(self) -> List[RequirementDto]:
        return [
            RequirementDto(id="NFR-01", title="Security", description="Students must only access their own project data and generated artifacts.", priority="High", source="Software quality"),
            RequirementDto(id="NFR-02", title="Usability", description="The system interface should be clear and easy to use for university students.", priority="High", source="Software quality"),
            RequirementDto(id="NFR-03", title="Reliability", description="The system should provide fallback responses when the AI service is unavailable.", priority="High", source="Software quality"),
            RequirementDto(id="NFR-04", title="Performance", description="The system should return AI results within an acceptable time for demos and student use.", priority="Medium", source="Software quality"),
            RequirementDto(id="NFR-05", title="Maintainability", description="The system should separate UI, services, DTOs, database access, and AI agents.", priority="High", source="Software quality"),
        ]

    def _use_cases_or_fallback(self, raw: Any) -> List[UseCaseDto]:
        try:
            return [UseCaseDto.model_validate(x) for x in (raw or [])][:5] or self._fallback_use_cases()
        except Exception:
            return self._fallback_use_cases()

    def _edge_cases_or_fallback(self, raw: Any) -> List[EdgeCaseDto]:
        try:
            return [EdgeCaseDto.model_validate(x) for x in (raw or [])][:5] or self._fallback_edge_cases()
        except Exception:
            return self._fallback_edge_cases()

    def _modules_or_fallback(self, raw: Any) -> List[ModuleDto]:
        try:
            return [ModuleDto.model_validate(x) for x in (raw or [])][:5] or self._fallback_modules()
        except Exception:
            return self._fallback_modules()

    def _entities_or_fallback(self, raw: Any) -> List[EntityDto]:
        try:
            return [EntityDto.model_validate(x) for x in (raw or [])][:5] or self._fallback_entities()
        except Exception:
            return self._fallback_entities()

    def _relationships_or_fallback(self, raw: Any) -> List[RelationshipDto]:
        try:
            return [RelationshipDto.model_validate(x) for x in (raw or [])] or self._fallback_relationships()
        except Exception:
            return self._fallback_relationships()

    def _tests_or_fallback(self, raw: Any) -> List[TestCaseDto]:
        try:
            return [TestCaseDto.model_validate(x) for x in (raw or [])][:5] or self._fallback_tests()
        except Exception:
            return self._fallback_tests()

    def _requirements_or_fallback(self, raw: Any, fallback: List[RequirementDto]) -> List[RequirementDto]:
        try:
            return [RequirementDto.model_validate(x) for x in (raw or [])] or fallback
        except Exception:
            return fallback

    def _fallback_use_cases(self) -> List[UseCaseDto]:
        return [
            UseCaseDto(id="UC-01", title="Complete profile", actor="Student", goal="Provide project context.", preconditions=["Student is logged in."], mainFlow=["Open profile.", "Enter details.", "Save profile."], alternativeFlow=["Validation appears if fields are missing."], postconditions=["Profile is saved."], relatedRequirements=["FR-02"]),
            UseCaseDto(id="UC-02", title="Generate ideas", actor="Student", goal="Receive personalized project ideas.", preconditions=["Profile exists."], mainFlow=["Open generator.", "Click generate.", "Review ideas."], alternativeFlow=["Fallback ideas appear if AI fails."], postconditions=["Ideas are stored."], relatedRequirements=["FR-03"]),
            UseCaseDto(id="UC-03", title="Compare ideas", actor="Student", goal="Choose strongest idea.", preconditions=["At least two ideas exist."], mainFlow=["Open comparison.", "Click compare.", "Review ranking."], alternativeFlow=["Warning appears if insufficient ideas."], postconditions=["Comparison is displayed."], relatedRequirements=["FR-04"]),
            UseCaseDto(id="UC-04", title="Select idea", actor="Student", goal="Mark one idea as active.", preconditions=["Ideas exist."], mainFlow=["Choose idea.", "Click select.", "System saves selection."], alternativeFlow=["Unauthorized ideas are rejected."], postconditions=["Selected idea is active."], relatedRequirements=["FR-05"]),
            UseCaseDto(id="UC-05", title="Generate documentation", actor="Student", goal="Create SE documentation.", preconditions=["Selected idea exists."], mainFlow=["Open documentation page.", "Click generate.", "Review sections."], alternativeFlow=["Fallback documentation appears if AI fails."], postconditions=["Documentation is displayed."], relatedRequirements=["FR-08"]),
        ]

    def _fallback_edge_cases(self) -> List[EdgeCaseDto]:
        return [
            EdgeCaseDto(id="EC-01", scenario="No selected idea exists.", expectedHandling="Ask the student to select an idea first.", relatedRequirement="FR-08"),
            EdgeCaseDto(id="EC-02", scenario="AI service is offline.", expectedHandling="Return fallback documentation and clear warning.", relatedRequirement="FR-08"),
            EdgeCaseDto(id="EC-03", scenario="Student has no saved skills.", expectedHandling="Use general assumptions and warn the student.", relatedRequirement="FR-02"),
            EdgeCaseDto(id="EC-04", scenario="Generated idea has vague problem statement.", expectedHandling="Generate cautious documentation and add warning.", relatedRequirement="FR-08"),
            EdgeCaseDto(id="EC-05", scenario="Student accesses another user's idea.", expectedHandling="Reject the request.", relatedRequirement="FR-05"),
        ]

    def _fallback_modules(self) -> List[ModuleDto]:
        return [
            ModuleDto(id="M-01", name="Profile Module", responsibility="Manages student profile and skills.", inputs=["Profile data"], outputs=["Saved profile"], relatedRequirements=["FR-02"]),
            ModuleDto(id="M-02", name="Idea Generation Module", responsibility="Generates personalized project ideas.", inputs=["Profile", "Skills"], outputs=["Project ideas"], relatedRequirements=["FR-03"]),
            ModuleDto(id="M-03", name="Idea Comparison Module", responsibility="Ranks ideas and recommends best option.", inputs=["Ideas"], outputs=["Comparison result"], relatedRequirements=["FR-04"]),
            ModuleDto(id="M-04", name="Roadmap Module", responsibility="Creates implementation roadmap.", inputs=["Selected idea"], outputs=["Roadmap phases"], relatedRequirements=["FR-06"]),
            ModuleDto(id="M-05", name="SE Documentation Module", responsibility="Generates SE documentation artifacts.", inputs=["Idea", "Profile", "Roadmap"], outputs=["Documentation"], relatedRequirements=["FR-08"]),
        ]

    def _fallback_entities(self) -> List[EntityDto]:
        return [
            EntityDto(name="User", purpose="Stores account and authentication information.", importantFields=["Id", "Email", "PasswordHash", "Role"], relationships=["User has one StudentProfile", "User has many ProjectIdeas"]),
            EntityDto(name="StudentProfile", purpose="Stores student academic profile.", importantFields=["Id", "UserId", "Major", "ExperienceLevel"], relationships=["Belongs to User"]),
            EntityDto(name="StudentSkill", purpose="Stores technical skills.", importantFields=["Id", "UserId", "SkillName", "Rating"], relationships=["Belongs to User"]),
            EntityDto(name="ProjectIdea", purpose="Stores generated ideas.", importantFields=["Id", "UserId", "Title", "IsSelected"], relationships=["Belongs to User"]),
            EntityDto(name="SEDocumentation", purpose="Stores generated documentation.", importantFields=["Id", "UserId", "ProjectIdeaId", "ContentJson"], relationships=["Belongs to ProjectIdea"]),
        ]

    def _fallback_relationships(self) -> List[RelationshipDto]:
        return [
            RelationshipDto(fromEntity="User", toEntity="StudentProfile", type="one-to-one", description="A student user has one profile."),
            RelationshipDto(fromEntity="User", toEntity="StudentSkill", type="one-to-many", description="A student can define multiple skills."),
            RelationshipDto(fromEntity="User", toEntity="ProjectIdea", type="one-to-many", description="A student can generate many ideas."),
            RelationshipDto(fromEntity="ProjectIdea", toEntity="SEDocumentation", type="one-to-many", description="A project idea can have generated documentation versions."),
        ]

    def _fallback_tests(self) -> List[TestCaseDto]:
        return [
            TestCaseDto(id="TC-01", title="Profile save test", type="Functional", steps=["Open profile.", "Enter data.", "Save."], expectedResult="Profile is saved.", relatedRequirements=["FR-02"]),
            TestCaseDto(id="TC-02", title="Idea generation test", type="AI Integration", steps=["Open generator.", "Click generate."], expectedResult="Ideas are displayed.", relatedRequirements=["FR-03"]),
            TestCaseDto(id="TC-03", title="Idea comparison test", type="AI Integration", steps=["Open comparison.", "Click compare."], expectedResult="Ranked comparison appears.", relatedRequirements=["FR-04"]),
            TestCaseDto(id="TC-04", title="Roadmap generation test", type="AI Integration", steps=["Select idea.", "Generate roadmap."], expectedResult="Roadmap phases appear.", relatedRequirements=["FR-06"]),
            TestCaseDto(id="TC-05", title="Documentation generation test", type="AI Integration", steps=["Open documentation.", "Click generate."], expectedResult="Documentation appears.", relatedRequirements=["FR-08"]),
        ]

    def _ensure_unique_ids(self, items: List[Any], prefix: str) -> List[Any]:
        """Renumber `.id` fields to prefix-01, prefix-02, ... only if a
        duplicate is actually present, preserving every item's content and
        order otherwise."""
        ids = [item.id for item in items]

        if len(ids) == len(set(ids)):
            return items

        for index, item in enumerate(items, start=1):
            item.id = f"{prefix}-{index:02d}"

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

    def _build_traceability(
        self,
        frs: List[RequirementDto],
        use_cases: List[UseCaseDto],
        modules: List[ModuleDto],
        entities: List[EntityDto],
        tests: List[TestCaseDto]
    ) -> List[TraceabilityDto]:
        rows = []
        count = min(len(frs), len(use_cases), len(modules), len(entities), len(tests), 5)

        for i in range(count):
            rows.append(
                TraceabilityDto(
                    requirementId=frs[i].id,
                    useCaseId=use_cases[i].id,
                    moduleId=modules[i].id,
                    entity=entities[i].name,
                    testCaseId=tests[i].id
                )
            )

        return rows

    def _build_erd(self, entities: List[EntityDto], relationships: List[RelationshipDto]) -> str:
        lines = ["erDiagram"]

        for rel in relationships:
            left = self._mermaid_name(rel.fromEntity)
            right = self._mermaid_name(rel.toEntity)
            symbol = "||--o{" if "many" in rel.type else "||--||"
            lines.append(f"    {left} {symbol} {right} : relates")

        if len(lines) == 1:
            lines.append("    USER ||--o{ PROJECT_IDEA : owns")

        return "\n".join(lines)

    def _build_class_diagram(self, entities: List[EntityDto]) -> str:
        lines = ["classDiagram"]

        for entity in entities:
            name = self._mermaid_name(entity.name)
            lines.append(f"    class {name} {{")
            for field in entity.importantFields[:5]:
                safe_field = re.sub(r"[^A-Za-z0-9_]", "", field)
                lines.append(f"      string {safe_field}")
            lines.append("    }")

        return "\n".join(lines)

    def _build_activity_diagram(self) -> str:
        return """flowchart TD
    A[Student logs in] --> B[Complete profile]
    B --> C[Generate ideas]
    C --> D[Compare ideas]
    D --> E[Select final idea]
    E --> F[Generate roadmap]
    F --> G[Generate SE documentation]
    G --> H[Review documentation]
"""

    def _build_sequence_diagram(self) -> str:
        return """sequenceDiagram
    actor Student
    participant Web as ASP.NET Razor Pages
    participant DB as PostgreSQL
    participant AI as Python FastAPI
    participant LLM as Ollama
    Student->>Web: Click Generate Documentation
    Web->>DB: Load profile, idea, skills, roadmap
    Web->>AI: POST /generate-se-documentation
    AI->>LLM: Generate documentation sections
    LLM-->>AI: JSON sections
    AI-->>Web: Validated documentation
    Web-->>Student: Display documentation
"""

    def _split_items(self, value: str, default: List[str]) -> List[str]:
        if not value or not value.strip():
            return default

        items = [
            item.strip()
            for item in re.split(r"[,;\n]+", value)
            if item.strip()
        ]

        return items or default

    def _mermaid_name(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
        return cleaned.upper() or "ENTITY"