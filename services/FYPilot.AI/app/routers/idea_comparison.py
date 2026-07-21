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
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["Idea Comparison"])


def _build_review_context(request: IdeaComparisonRequest) -> ReviewContext:
    trusted_structural_context = {
        "teamSize": request.teamSize,
        "availableHoursPerWeek": request.availableHoursPerWeek,
        "experienceLevel": request.experienceLevel,
        "skillRatings": request.skillRatings,
    }

    ideas_summary = "\n".join(
        f"- id={idea.id} | {idea.title} | {idea.problemStatement}"
        for idea in request.ideas
    )

    untrusted_project_text = {
        "studentMajor": request.studentMajor,
        "studentSkills": ", ".join(request.studentSkills),
        "ideasSummary": ideas_summary,
    }

    return ReviewContext(
        agent_name="IdeaComparisonAgent",
        trusted_system_instructions=(
            "IdeaComparisonAgent: compares and ranks a batch of the student's "
            "own already-generated project ideas against their skills, team "
            "size, and available hours. Must never contradict the student's "
            "own trusted skill ratings, and must only compare the ideas "
            "actually provided."
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


@router.post("/compare-generated-ideas")
def compare_generated_ideas(request: IdeaComparisonRequest):
    agent = IdeaComparisonAgent()
    context = _build_review_context(request)
    pipeline = ReviewPipeline("IdeaComparisonAgent")
    result = pipeline.run(
        lambda: agent.generate_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    comparison = (
        result.output if result.usable else agent.build_safe_fallback(request).model_dump()
    )

    return {
        "comparison": comparison,
        "agent": "IdeaComparisonAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Generated ideas compared successfully",
    }