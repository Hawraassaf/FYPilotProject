from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from app.agents.se_documentation.se_documentation_orchestrator import (
    SEDocumentationOrchestratorAgent as SEDocumentationAgent,
    SEDocumentationRequest,
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
    documentation = agent.generate(request)

    return {
        "documentation": documentation.model_dump(),
        "agent": "SEDocumentationAgent",
        "llmUsed": agent.last_llm_used,
        "source": "ollama" if agent.last_llm_used else "dynamic-fallback",
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
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
        job.source = "ollama" if agent.last_llm_used else "dynamic-fallback"
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