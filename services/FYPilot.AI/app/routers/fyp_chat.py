"""
FYP Mentor Chat router.

Endpoint:
POST /fyp-chat
"""

from datetime import datetime, timezone

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
        # repairUsed=True means the first response was invalid JSON and a
        # second repair call was made (doubles latency). Watch this during
        # testing: if it fires often, something is wrong with the prompt.
        "repairUsed": agent.last_repair_used,
        "source": "ollama" if agent.last_llm_used else "dynamic-fallback",
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "FYP mentor response generated successfully",
    }