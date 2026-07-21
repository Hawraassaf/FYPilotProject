from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.agents.se_documentation.se_documentation_orchestrator import (
    SEDocumentationOrchestratorAgent as SEDocumentationAgent,
    SEDocumentationRequest,
    SEDocStudentProfile,
    SEDocSelectedIdea,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["SE Documentation"])


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
    pipeline = ReviewPipeline("SEDocumentationAgent")
    result = pipeline.run(
        lambda: agent.generate_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    documentation = (
        result.output if result.usable else agent.build_safe_fallback(request).model_dump()
    )

    return {
        "documentation": documentation,
        "agent": "SEDocumentationAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
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

        documentation = agent.generate(request)

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