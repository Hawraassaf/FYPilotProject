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

from app.agents.answer_review_agent import AnswerReviewAgent

router = APIRouter(tags=["FYP Mentor Chat"])


@router.post("/fyp-chat")
def fyp_chat(request: FypMentorRequest):
    mentor_agent = FypMentorAgent()
    raw_answer = mentor_agent.chat(request)

    review_agent = AnswerReviewAgent()

    review = review_agent.review_mentor_answer(
        answer=raw_answer.model_dump(),
        user_message=request.message,
        selected_idea=(
            request.selectedIdea.model_dump()
            if request.selectedIdea
            else None
        ),
        roadmap=[
            phase.model_dump()
            for phase in (request.roadmap or [])
        ],
    )

    return {
        "answer": review.revised_answer,
        "agent": "FypMentorAgent",
        "reviewAgent": "AnswerReviewAgent",
        "llmUsed": mentor_agent.last_llm_used,
        "reviewUsed": review.reviewer_used,
        "reviewApproved": review.approved,
        "reviewScore": review.review_score,
        "reviewIssues": [
            issue.model_dump()
            for issue in review.issues
        ],
        # repairUsed=True means the first response was invalid JSON and a
        # second repair call was made (doubles latency). Watch this during
        # testing: if it fires often, something is wrong with the prompt.
        "repairUsed": mentor_agent.last_repair_used,
        "source": "ollama" if mentor_agent.last_llm_used else "dynamic-fallback",
        "ollamaError": mentor_agent.last_error,
        "ollamaRawPreview": mentor_agent.last_raw_llm_response,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "FYP mentor response generated and reviewed successfully",
    }