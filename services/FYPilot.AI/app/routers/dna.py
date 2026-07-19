"""
DNA router — exposes ProjectDNAAgent through FastAPI.

Endpoint:
POST /analyze-project-dna
"""

from datetime import datetime

from fastapi import APIRouter

from app.agents.project_dna_agent import ProjectDNAAgent, ProjectDNARequest

router = APIRouter(tags=["Project DNA Analysis"])


@router.post("/analyze-project-dna")
def analyze_project_dna(request: ProjectDNARequest):
    agent = ProjectDNAAgent()
    result = agent.analyze(request)

    return {
        "analysis": result.model_dump(),
        "agent": "ProjectDNAAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project DNA analysis generated successfully",
    }