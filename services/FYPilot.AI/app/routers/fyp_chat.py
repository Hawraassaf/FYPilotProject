"""
FYP Mentor Chat router.

Endpoint:
POST /fyp-chat
"""

from datetime import datetime

from fastapi import APIRouter

from app.agents.fyp_mentor_agent import (
    FypMentorAgent,
    FypMentorRequest,
)

router = APIRouter(tags=["FYP Mentor Chat"])


@router.post("/fyp-chat")
def fyp_chat(request: FypMentorRequest):
    agent = FypMentorAgent()
    result = agent.chat(request)

    return {
        "answer": result.model_dump(),
        "agent": "FypMentorAgent",
        "llmUsed": agent.last_llm_used,
        "source": "ollama" if agent.last_llm_used else "dynamic-fallback",
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "FYP mentor response generated successfully",
    }