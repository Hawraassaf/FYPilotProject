"""
Roadmap router — exposes ProjectRoadmapAgent through FastAPI.

Endpoint:
POST /generate-project-roadmap
"""

from datetime import datetime

from fastapi import APIRouter

from app.agents.project_roadmap_agent import (
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
)

router = APIRouter(tags=["Project Roadmap"])


@router.post("/generate-project-roadmap")
def generate_project_roadmap(request: ProjectRoadmapRequest):
    agent = ProjectRoadmapAgent()
    result = agent.generate(request)

    return {
        "roadmap": result.model_dump(),
        "agent": "ProjectRoadmapAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project roadmap generated successfully",
    }