import time
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.agents.se_documentation.quality_outcome_policy import apply_review_outcome_to_quality
from app.agents.se_documentation.se_documentation_orchestrator import (
    QualityAssessmentDto,
    SEDocumentationOrchestratorAgent as SEDocumentationAgent,
    SEDocumentationRequest,
    SEDocStudentProfile,
    SEDocSelectedIdea,
    WriterBudgetExceededError,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.registry import get_agent_config
from app.review.response import build_review_response

router = APIRouter(tags=["SE Documentation"])


# Reserved for the semantic Reviewer / one rewrite / final re-review -- the
# Writer must never consume this. writer_deadline = global_deadline - this;
# global_deadline itself (unmodified) is what reaches ReviewPipeline. See
# SEDocumentationOrchestratorAgent._generate_llm_sections's docstring and
# registry.py's SEDocumentationAgent.max_total_seconds comment for the full
# history/measurement behind these numbers.
_SEMANTIC_REVIEW_RESERVE_SECONDS = 300.0


# The 6 core sections a real generation must actually produce -- matches
# SEDocumentationOrchestratorAgent._assemble_documentation's own
# `section_keys` list exactly (see se_documentation_orchestrator.py). Kept
# here rather than imported so this router's policy stays an explicit,
# independently-readable list rather than reaching into the orchestrator's
# internals; the shared value is the section KEY NAMES themselves (a string
# contract both sides already rely on via sectionProvenance).
_CORE_SECTION_KEYS = (
    "requirements", "useCases", "modulesArchitecture",
    "database", "uiApi", "testingSecurity",
)


def _has_core_section_fallback(documentation: Dict[str, Any]) -> bool:
    """
    True when ANY core section (or, when applicable, the AI technical
    report) used deterministic fallback content instead of real provider
    output. This is the SINGLE normalized signal the strict
    pre-presentation policy relies on -- see the stabilization task's Step
    C. .NET independently recomputes the same thing from the same
    sectionProvenance dict (defense in depth, not blind trust in this
    Python-side flag) -- see DocumentationGeneratorService.HasCoreSectionFallback.
    """
    provenance = documentation.get("sectionProvenance") or {}

    if any(provenance.get(key) == "fallback" for key in _CORE_SECTION_KEYS):
        return True

    # A document must never describe its AI technical report as
    # confirmed/complete when it is actually generic fallback text --
    # treated as an additional core-fallback condition whenever the report
    # is applicable at all.
    if documentation.get("aiTechnicalReportApplicable") and provenance.get("aiReport") == "fallback":
        return True

    return False


def _build_review_context(request: SEDocumentationRequest) -> ReviewContext:
    profile = request.studentProfile or SEDocStudentProfile()
    idea = request.selectedIdea or SEDocSelectedIdea()

    trusted_structural_context = {
        "teamSize": profile.teamSize,
        "availableHoursPerWeek": profile.availableHoursPerWeek,
        "experienceLevel": profile.experienceLevel,
        "major": profile.major,
        "skillRatings": profile.skillRatings,
        "expectedDurationWeeks": idea.expectedDurationWeeks,
        "difficultyLevel": idea.difficultyLevel,
        "roadmapPhaseCount": len(request.roadmap),
    }

    roadmap_summary = "\n".join(
        f"Phase {phase.phaseNumber}: {phase.name} - {phase.objective}"
        for phase in request.roadmap
    )

    untrusted_project_text = {
        "ideaTitle": idea.title,
        "problemStatement": idea.problemStatement,
        "targetUsers": idea.targetUsers,
        "whyUseful": idea.whyUseful,
        "requiredTechnologies": idea.requiredTechnologies,
        "requiredSkills": idea.requiredSkills,
        "missingSkills": idea.missingSkills,
        "domain": idea.domain,
        "finalDeliverables": idea.finalDeliverables,
        "studentSkills": ", ".join(profile.skills),
        "roadmapSummary": roadmap_summary,
        "existingNotes": request.existingNotes,
    }

    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions=(
            "SEDocumentationAgent: generates structured software engineering "
            "documentation (requirements, use cases, modules, database design, "
            "diagrams, testing plan, traceability matrix) for a student's "
            "selected final year project idea and roadmap. Diagram fields and "
            "documentationQualityScore are always computed deterministically, "
            "never written by the LLM."
        ),
        trusted_structural_context=trusted_structural_context,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


class SEDocumentationJobDto(BaseModel):
    jobId: str
    status: str = "queued"
    progress: int = 0
    message: str = "Job created"
    createdAt: str
    updatedAt: str
    llmUsed: bool = False
    source: str = "pending"
    ollamaError: Optional[str] = None
    documentation: Optional[Dict[str, Any]] = None


JOBS: Dict[str, SEDocumentationJobDto] = {}


@router.get("/generate-se-documentation/test")
def test_se_documentation_router():
    return {
        "message": "SE Documentation router is working",
        "availableEndpoints": [
            "POST /generate-se-documentation",
            "POST /generate-se-documentation/jobs",
            "GET /generate-se-documentation/jobs/{job_id}",
        ],
    }


@router.post("/generate-se-documentation")
def generate_se_documentation(request: SEDocumentationRequest) -> Dict[str, Any]:
    agent = SEDocumentationAgent()
    context = _build_review_context(request)
    # Reviewer/Rewrite tier must match the Writer's own tier (see
    # ReviewPipeline's constructor docstring) -- kept in sync with
    # SEDocumentationOrchestratorAgent.__init__'s "se_documentation" tier.
    pipeline = ReviewPipeline("SEDocumentationAgent", tier="se_documentation")

    # ONE global absolute deadline, computed here from the registry value
    # (1200s) -- passed UNCHANGED to ReviewPipeline (structural repair,
    # semantic review, rewrite, final re-review all measure against this
    # exact value). The Writer callable gets a SEPARATE, earlier
    # writer_deadline (global - the 300s semantic-review reserve) so the
    # Writer can never consume the time set aside for review -- see
    # SEDocumentationOrchestratorAgent._generate_llm_sections's docstring.
    global_deadline = time.monotonic() + pipeline.config.max_total_seconds
    writer_deadline = global_deadline - _SEMANTIC_REVIEW_RESERVE_SECONDS

    try:
        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=writer_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )
    except WriterBudgetExceededError as e:
        # The Writer ran out of its own reserved budget before every core
        # section could even be attempted -- distinct from a normal
        # provider failure. Never assemble a partial/fallback document,
        # never persist anything: .NET's acceptance gate rejects this
        # outright (see DocumentationGeneratorService) and the previously
        # accepted document is preserved untouched.
        return {
            "documentation": None,
            "agent": "SEDocumentationAgent",
            "llmUsed": False,
            "source": "writer-budget-exceeded",
            "provider": None,
            "modelUsed": None,
            "ollamaError": None,
            "ollamaRawPreview": None,
            "review": None,
            "coreSectionFallback": False,
            "writerBudgetExceeded": True,
            "missingSections": e.missing_sections,
            "completedSections": e.completed_sections,
            "generatedAt": datetime.utcnow().isoformat(),
            "message": (
                f"Documentation generation ran out of time before {e.missing_sections} "
                f"could be attempted (completed: {e.completed_sections}). "
                "No document was saved; your previous documentation is unchanged."
            ),
        }

    documentation = (
        result.output if result.usable else agent.build_safe_fallback(request).model_dump()
    )

    # The score/assessment must reflect what the semantic Reviewer actually
    # found, not only the deterministic checks run before review -- see
    # quality_outcome_policy.py's module docstring. Only applied when this
    # candidate genuinely went through this pipeline run (result.usable);
    # the build_safe_fallback branch above is a fresh, never-reviewed
    # candidate that this run's outcome does not describe.
    if result.usable and documentation.get("qualityAssessment"):
        base_assessment = QualityAssessmentDto.model_validate(documentation["qualityAssessment"])
        final_assessment = apply_review_outcome_to_quality(
            base_assessment,
            result,
            documentation.get("sectionProvenance", {}),
        )
        documentation["qualityAssessment"] = final_assessment.model_dump()
        documentation["documentationQualityScore"] = final_assessment.overallScore

    # Strict pre-presentation policy (stabilization task Step C): a document
    # with ANY core section (or an applicable-but-fallback AI report) is
    # never presented as real, complete AI output -- even when llmUsed=true
    # and the overall pipeline status is usable. This is computed here, at
    # the earliest backend boundary that has sectionProvenance, and
    # independently re-verified by .NET's own acceptance gate (defense in
    # depth -- .NET does not simply trust this field).
    core_section_fallback = result.usable and _has_core_section_fallback(documentation)

    return {
        "documentation": documentation,
        "agent": "SEDocumentationAgent",
        # Never claims a provider-only origin when a core section actually
        # used deterministic fallback content -- "partial-section-fallback"
        # deliberately contains the word "fallback" so it is also caught by
        # any existing substring-based fallback-source check.
        "llmUsed": agent.last_llm_used and not core_section_fallback,
        "source": (
            "partial-section-fallback" if core_section_fallback
            else (agent.last_provider if agent.last_llm_used else "dynamic-fallback")
        ),
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "coreSectionFallback": core_section_fallback,
        "writerBudgetExceeded": False,
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "SE documentation generated successfully",
    }


@router.post("/generate-se-documentation/jobs")
def start_se_documentation_job(
    request: SEDocumentationRequest,
    background_tasks: BackgroundTasks,
):
    job_id = str(uuid4())
    now = datetime.utcnow().isoformat()

    JOBS[job_id] = SEDocumentationJobDto(
        jobId=job_id,
        status="queued",
        progress=0,
        message="SE documentation generation job created",
        createdAt=now,
        updatedAt=now,
    )

    background_tasks.add_task(run_se_documentation_job, job_id, request)

    return {
        "jobId": job_id,
        "status": "queued",
        "progress": 0,
        "message": "SE documentation generation started",
        "pollUrl": f"/generate-se-documentation/jobs/{job_id}",
    }


@router.get("/generate-se-documentation/jobs/{job_id}")
def get_se_documentation_job(job_id: str):
    job = JOBS.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="SE documentation job not found")

    return job.model_dump()


def run_se_documentation_job(job_id: str, request: SEDocumentationRequest):
    job = JOBS[job_id]

    try:
        update_job(
            job_id,
            status="running",
            progress=10,
            message="Preparing SE documentation context",
        )

        agent = SEDocumentationAgent()

        update_job(
            job_id,
            status="running",
            progress=35,
            message="Generating SE documentation using AI agent",
        )

        # This path bypasses ReviewPipeline entirely, but still needs the
        # same coordinated deadline so a slow provider can't run this job
        # forever -- read the SAME registry value the synchronous endpoint
        # uses (via pipeline.config.max_total_seconds) rather than a
        # second hardcoded number that could drift out of sync.
        deadline = time.monotonic() + get_agent_config("SEDocumentationAgent").max_total_seconds
        documentation = agent.generate(request, deadline=deadline)

        update_job(
            job_id,
            status="running",
            progress=85,
            message="Validating and assembling documentation",
        )

        job.documentation = documentation.model_dump()
        job.llmUsed = agent.last_llm_used
        job.source = agent.last_provider if agent.last_llm_used else "dynamic-fallback"
        job.ollamaError = agent.last_error

        update_job(
            job_id,
            status="completed",
            progress=100,
            message="SE documentation generated successfully",
        )

    except WriterBudgetExceededError as e:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message=(
                f"Documentation generation ran out of time before {e.missing_sections} "
                f"could be attempted (completed: {e.completed_sections})."
            ),
        )
    except Exception as e:
        update_job(
            job_id,
            status="failed",
            progress=100,
            message=f"SE documentation generation failed: {str(e)}",
        )


def update_job(job_id: str, status: str, progress: int, message: str):
    job = JOBS[job_id]
    job.status = status
    job.progress = progress
    job.message = message
    job.updatedAt = datetime.utcnow().isoformat()