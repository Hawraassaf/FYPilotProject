"""
FYP Mentor Chat router.

Endpoint:
POST /fyp-chat

This is the pilot feature for the shared AI review pipeline
(app/review/pipeline.py): the mentor's draft answer (the Writer stage) is
generated once, then passes through a content firewall, structural
validation, a semantic Reviewer, a deterministic rewrite decision, and up to
one targeted rewrite before being returned. The other 12 AI agents are not
yet wired into this pipeline -- see app/review/registry.py.

Trivial exchanges (empty message, a bare greeting, a code request without
usable code context) are intentionally never sent to an LLM or the review
pipeline at all -- see FypMentorAgent.try_short_circuit_answer.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.agents.fyp_mentor_agent import FypMentorAgent, FypMentorRequest
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response, empty_review_response

router = APIRouter(tags=["FYP Mentor Chat"])


def _build_review_context(request: FypMentorRequest) -> ReviewContext:
    trusted_structural: dict[str, Any] = {
        "studentProfile.experienceLevel": request.studentProfile.experienceLevel,
        "studentProfile.teamSize": request.studentProfile.teamSize,
        "studentProfile.availableHoursPerWeek": request.studentProfile.availableHoursPerWeek,
        "studentProfile.skillRatings": request.studentProfile.skillRatings,
    }

    untrusted_project_text: dict[str, str] = {
        "studentProfile.major": request.studentProfile.major,
        "studentProfile.skills": ", ".join(request.studentProfile.skills),
    }

    if request.selectedIdea is not None:
        idea = request.selectedIdea
        trusted_structural["selectedIdea.id"] = idea.id
        trusted_structural["selectedIdea.expectedDurationWeeks"] = idea.expectedDurationWeeks
        trusted_structural["selectedIdea.difficultyLevel"] = idea.difficultyLevel
        untrusted_project_text.update(
            {
                "selectedIdea.title": idea.title,
                "selectedIdea.problemStatement": idea.problemStatement,
                "selectedIdea.targetUsers": idea.targetUsers,
                "selectedIdea.whyUseful": idea.whyUseful,
                "selectedIdea.requiredTechnologies": idea.requiredTechnologies,
                "selectedIdea.requiredSkills": idea.requiredSkills,
                "selectedIdea.missingSkills": idea.missingSkills,
                "selectedIdea.domain": idea.domain,
                "selectedIdea.finalDeliverables": idea.finalDeliverables,
            }
        )

    if request.dnaSummary is not None:
        dna = request.dnaSummary
        trusted_structural["dnaSummary.overallScore"] = dna.overallScore
        trusted_structural["dnaSummary.riskLevel"] = dna.riskLevel
        untrusted_project_text["dnaSummary.strengths"] = ", ".join(dna.strengths)
        untrusted_project_text["dnaSummary.weaknesses"] = ", ".join(dna.weaknesses)

    for phase in request.roadmap:
        trusted_structural[f"roadmap.phase{phase.phaseNumber}.isCompleted"] = phase.isCompleted
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.name"] = phase.name
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.objective"] = phase.objective
        untrusted_project_text[f"roadmap.phase{phase.phaseNumber}.tasks"] = "; ".join(phase.tasks)

    conversation_history = [
        f"{message.role}: {message.content}" for message in request.recentMessages[-8:]
    ]

    previous_model_outputs = [
        message.content for message in request.recentMessages if message.role == "assistant"
    ]

    existing_code = None
    if request.codeContext is not None:
        existing_code = (
            "\n\n".join(
                text
                for text in (
                    request.codeContext.existingCode,
                    request.codeContext.requestedChange,
                )
                if text
            )
            or None
        )

    return ReviewContext(
        agent_name="FypMentorAgent",
        trusted_system_instructions=(
            "FypMentorAgent: a specialized mentor for final year project planning, "
            "implementation, documentation, testing, and defense preparation."
        ),
        trusted_structural_context=trusted_structural,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input=request.message,
        untrusted_conversation_history=conversation_history,
        untrusted_existing_code=existing_code,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=previous_model_outputs,
        allowed_source_metadata=[],
    )


@router.post("/fyp-chat")
def fyp_chat(request: FypMentorRequest):
    mentor_agent = FypMentorAgent()

    short_circuit = mentor_agent.try_short_circuit_answer(request)

    if short_circuit is not None:
        return {
            "answer": short_circuit.model_dump(),
            "agent": "FypMentorAgent",
            "llmUsed": False,
            "source": "dynamic-fallback",
            "provider": None,
            "modelUsed": None,
            "ollamaError": None,
            "review": empty_review_response(
                "Trivial exchange answered directly; not sent to an LLM or the review pipeline."
            ),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "message": "Mentor response generated successfully",
        }

    context = _build_review_context(request)
    pipeline = ReviewPipeline("FypMentorAgent")

    result = pipeline.run(
        lambda: mentor_agent.generate_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    final_answer = (
        result.output if result.usable else mentor_agent.build_safe_fallback(request).model_dump()
    )

    return {
        "answer": final_answer,
        "agent": "FypMentorAgent",
        "llmUsed": mentor_agent.last_llm_used,
        "source": mentor_agent.last_provider if mentor_agent.last_llm_used else "dynamic-fallback",
        "provider": mentor_agent.last_provider,
        "modelUsed": mentor_agent.last_model_used,
        "ollamaError": mentor_agent.last_error,
        "review": build_review_response(result),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "message": "Mentor response generated successfully",
    }
