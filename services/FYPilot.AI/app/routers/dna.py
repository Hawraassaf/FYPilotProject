"""
DNA router — exposes ProjectDNAAgent through FastAPI.

Endpoint:
POST /analyze-project-dna
"""

from datetime import datetime

from fastapi import APIRouter

from app.agents.project_dna_agent import ProjectDNAAgent, ProjectDNARequest
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["Project DNA Analysis"])


def _build_review_context(request: ProjectDNARequest) -> ReviewContext:
    trusted_structural_context = {
        "teamSize": request.teamSize,
        "availableHoursPerWeek": request.availableHoursPerWeek,
        "experienceLevel": request.experienceLevel,
        "skillRatings": request.skillRatings,
        "difficultyLevel": request.difficultyLevel,
    }

    untrusted_project_text = {
        "ideaTitle": request.ideaTitle,
        "problemStatement": request.problemStatement,
        "targetUsers": request.targetUsers,
        "whyUseful": request.whyUseful,
        "lebaneseMarketRelevance": request.lebaneseMarketRelevance,
        "requiredTechnologies": request.requiredTechnologies,
        "requiredSkills": request.requiredSkills,
        "missingSkills": request.missingSkills,
        "datasetNeeded": request.datasetNeeded,
        "finalDeliverables": request.finalDeliverables,
        "domain": request.domain,
        "lebaneseSector": request.lebaneseSector,
        "studentMajor": request.studentMajor,
        "studentSkills": ", ".join(request.studentSkills),
    }

    return ReviewContext(
        agent_name="ProjectDNAAgent",
        trusted_system_instructions=(
            "ProjectDNAAgent: analyzes a selected final year project idea "
            "against the student's profile and skills, producing 8 scored "
            "dimensions, a risk profile, and a required-skills breakdown. "
            "Must never contradict the student's own trusted skill ratings."
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


@router.post("/analyze-project-dna")
def analyze_project_dna(request: ProjectDNARequest):
    agent = ProjectDNAAgent()
    context = _build_review_context(request)
    pipeline = ReviewPipeline("ProjectDNAAgent")
    result = pipeline.run(
        lambda: agent.generate_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    analysis = (
        result.output if result.usable else agent.build_safe_fallback(request).model_dump()
    )

    return {
        "analysis": analysis,
        "agent": "ProjectDNAAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project DNA analysis generated successfully",
    }