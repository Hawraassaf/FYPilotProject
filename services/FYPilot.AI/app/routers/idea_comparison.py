"""
Idea Comparison router — exposes IdeaComparisonAgent through FastAPI.

Endpoint:
POST /compare-generated-ideas
"""

from datetime import datetime

from fastapi import APIRouter

from app.agents.project_idea_comparison import (
    IdeaComparisonAgent,
    IdeaComparisonRequest,
)

router = APIRouter(tags=["Idea Comparison"])


@router.post("/compare-generated-ideas")
def compare_generated_ideas(request: IdeaComparisonRequest):
    agent = IdeaComparisonAgent()
    result = agent.compare(request)

    return {
        "comparison": result.model_dump(),
        "agent": "IdeaComparisonAgent",
        "llmUsed": agent.last_llm_used,
        "source": "ollama" if agent.last_llm_used else "dynamic-fallback",
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Generated ideas compared successfully",
    }