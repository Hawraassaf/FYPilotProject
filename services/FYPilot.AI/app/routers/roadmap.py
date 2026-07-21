"""
Roadmap router — exposes ProjectRoadmapAgent through FastAPI.

Endpoint:
POST /generate-project-roadmap

Batch 1 of the AI Output Review Pipeline rollout (see app/review/registry.py
for the full agent classification). The roadmap's draft (the Writer stage --
LLM phase design + deterministic week expansion, unchanged from before) now
passes through the same content firewall, structural validation, semantic
Reviewer, deterministic rewrite decision, and up to one targeted rewrite as
FYP Mentor Chat. The response shape is unchanged and additive: every
existing key (roadmap, agent, llmUsed, source, provider, modelUsed,
ollamaError, ollamaRawPreview, generatedAt, message) is preserved exactly;
only the new "review" key is added.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.agents.project_roadmap_agent import ProjectRoadmapAgent, ProjectRoadmapRequest
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["Project Roadmap"])


def _build_review_context(request: ProjectRoadmapRequest) -> ReviewContext:
    trusted_structural: dict[str, Any] = {
        "expectedDurationWeeks": request.expectedDurationWeeks,
        "teamSize": request.teamSize,
        "availableHoursPerWeek": request.availableHoursPerWeek,
        "difficultyLevel": request.difficultyLevel,
        "skillRatings": request.skillRatings,
    }

    untrusted_project_text: dict[str, str] = {
        "ideaTitle": request.ideaTitle,
        "problemStatement": request.problemStatement,
        "requiredTechnologies": request.requiredTechnologies,
        "requiredSkills": request.requiredSkills,
        "missingSkills": request.missingSkills,
        "domain": request.domain,
        "finalDeliverables": request.finalDeliverables,
        "studentSkills": ", ".join(request.studentSkills),
    }

    return ReviewContext(
        agent_name="ProjectRoadmapAgent",
        trusted_system_instructions=(
            "ProjectRoadmapAgent: designs a phased, week-by-week implementation "
            "roadmap for a student's selected final year project idea. The LLM "
            "proposes phase content; durations, week counts, and team "
            "responsibilities are always computed deterministically."
        ),
        trusted_structural_context=trusted_structural,
        untrusted_project_text=untrusted_project_text,
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


@router.post("/generate-project-roadmap")
def generate_project_roadmap(request: ProjectRoadmapRequest):
    agent = ProjectRoadmapAgent()
    context = _build_review_context(request)
    pipeline = ReviewPipeline("ProjectRoadmapAgent")

    result = pipeline.run(
        lambda: agent.generate_candidate(request),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
    )

    final_roadmap = (
        result.output if result.usable else agent.build_safe_fallback(request).model_dump()
    )

    return {
        "roadmap": final_roadmap,
        "agent": "ProjectRoadmapAgent",
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project roadmap generated successfully",
    }
