"""
DNA router — exposes ProjectDNAAgent through FastAPI.

Endpoint:
POST /analyze-project-dna
"""

import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.agents.project_dna_agent import (
    WRITER_DEADLINE_EXCEEDED,
    ProjectDNAAgent,
    ProjectDNARequest,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

router = APIRouter(tags=["Project DNA Analysis"])

logger = logging.getLogger("fypilot-dna-router")

# Reserved for the semantic Reviewer / one structural repair / one semantic
# rewrite / final re-review -- the Writer must never consume this. Mirrors
# Roadmap's identical global_deadline/writer_deadline split (see
# routers/roadmap.py's _SEMANTIC_REVIEW_RESERVE_SECONDS = 25.0 out of a 90s
# total budget): Project DNA's registry budget (registry.py's
# ProjectDNAAgent.max_total_seconds) is the SAME 90.0s, and its Writer stage
# makes the SAME shape of exactly one generation call (no search) before the
# Reviewer runs once -- the identical risk profile Roadmap's 25s reserve was
# already sized for, so it is reused unchanged rather than re-derived. 25s
# leaves the Reviewer at least one meaningful provider attempt (ProviderChain's
# shared _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor is 4s) plus headroom for the
# deterministic parsing/firewall/schema-validation steps around it, while
# still leaving the Writer the large majority of the 90s budget for its own
# single generation call. Previously there was no writer_deadline at all --
# analyze()'s generate_json() call could alone use its provider's full
# independent timeout (DeepInfra/Groq/Ollama, each configured up to ~60-90s),
# so a slow-but-successful candidate could arrive only for
# ReviewPipeline.run's own _time_budget_exceeded check (working from its own
# internally self-computed deadline, since none was ever passed in) to
# discard it before the Reviewer ever ran.
_WRITER_TIME_RESERVE_SECONDS = 25.0

# Maps a non-usable PipelineResult.status (review/models.py's PipelineStatus)
# to a typed DNA fallback reason -- covers every review-layer failure
# category that is NOT "the Writer itself never produced a candidate" (that
# finer-grained case -- including Writer-deadline exhaustion -- is
# classified by the agent itself via agent.last_fallback_reason_code, see
# ProjectDNAAgent.WRITER_DEADLINE_EXCEEDED). Mirrors
# routers/roadmap.py's _REVIEW_STATUS_TO_FALLBACK_REASON, scoped down to
# plain strings since ProjectDNAAgent has no dedicated fallback_reason
# taxonomy module (out of scope to introduce one for this task).
_REVIEW_STATUS_TO_FALLBACK_REASON: dict[str, str] = {
    "firewall_blocked": "blocked_content",
    "schema_invalid": "schema_invalid",
    "rejected": "semantic_rewrite_failed",
    "review_unavailable": "reviewer_unavailable",
}

_SAFE_FALLBACK_MESSAGES: dict[str, str] = {
    "blocked_content": "The AI response was blocked by the content safety firewall.",
    "schema_invalid": "The AI response did not match the required Project DNA structure.",
    "semantic_rewrite_failed": "A quality issue in the AI analysis could not be corrected.",
    "reviewer_unavailable": "The AI quality reviewer could not be reached in time.",
    "provider_unavailable": "The AI providers could not be reached.",
    WRITER_DEADLINE_EXCEEDED: (
        "AI Project DNA generation ran out of time before a valid response was produced."
    ),
    "unknown": "AI Project DNA generation did not succeed for an unspecified reason.",
}


def _classify_fallback(result: Any, agent: ProjectDNAAgent) -> tuple[str, str]:
    """
    Returns (fallbackReasonCode, fallbackReasonMessage) for a non-usable
    PipelineResult. When status=="provider_unavailable" the Writer itself
    never returned a usable candidate (agent.generate_candidate returned
    None) -- agent.last_fallback_reason_code, set at the exact rejection
    point inside ProjectDNAAgent.analyze(), distinguishes Writer-deadline
    exhaustion from an ordinary all-providers-failed case. Every other
    non-usable status (firewall_blocked/schema_invalid/rejected/
    review_unavailable) means the Writer DID produce a candidate that a
    later review-pipeline stage rejected.
    """
    if result.status == "provider_unavailable":
        code = agent.last_fallback_reason_code or "provider_unavailable"
    else:
        code = _REVIEW_STATUS_TO_FALLBACK_REASON.get(result.status, "unknown")

    return code, _SAFE_FALLBACK_MESSAGES.get(code, _SAFE_FALLBACK_MESSAGES["unknown"])


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

    # ONE absolute monotonic deadline, computed here from the registry's
    # max_total_seconds (90s) -- passed UNCHANGED to ReviewPipeline (schema/
    # structural-repair/semantic-review/rewrite/final-review all measure
    # against this exact value via ReviewPipeline.run's own deadline
    # threading). The Writer callable gets a SEPARATE, earlier
    # writer_deadline (global - the review reserve above) so it can never
    # consume the time set aside for review -- see
    # _WRITER_TIME_RESERVE_SECONDS's comment for why. Matches Roadmap's
    # identical pattern in routers/roadmap.py.
    global_deadline = time.monotonic() + pipeline.config.max_total_seconds
    writer_deadline = global_deadline - _WRITER_TIME_RESERVE_SECONDS

    logger.info(
        "project_dna.request_received writer_budget_seconds=%.1f total_budget_seconds=%.1f",
        writer_deadline - time.monotonic(), pipeline.config.max_total_seconds,
    )

    result = pipeline.run(
        lambda: agent.generate_candidate(request, deadline=writer_deadline),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
        deadline=global_deadline,
    )

    # Provenance must describe the DISPLAYED output, not merely the
    # attempted Writer call. A Writer call can succeed (agent.last_llm_used
    # =True, agent.last_provider="deepinfra") and still end up discarded by
    # a later review-pipeline stage (result.usable=False) -- in that case
    # the response must NOT claim AI/DeepInfra provenance for the
    # deterministic template that is actually shown. The Writer's own raw
    # attempt is preserved separately below (providerAttempts), never
    # deleted -- just no longer conflated with final-output provenance.
    if result.usable:
        analysis = result.output
        fallback_used = False
        fallback_reason_code: str | None = None
        fallback_reason_message: str | None = None
        output_origin = (
            "ai_rewritten"
            if result.outputOrigin in ("structural_repair", "semantic_rewrite")
            else "ai_generated"
        )
        displayed_llm_used = True
        displayed_provider = agent.last_provider
        displayed_model = agent.last_model_used
        displayed_source = agent.last_provider or "unknown"
    else:
        analysis = agent.build_safe_fallback(request).model_dump()
        fallback_used = True
        fallback_reason_code, fallback_reason_message = _classify_fallback(result, agent)
        output_origin = "deterministic_fallback"
        displayed_llm_used = False
        displayed_provider = None
        displayed_model = None
        displayed_source = "dynamic-fallback"

    logger.info(
        "project_dna.response_built status=%s usable=%s output_origin=%s "
        "fallback_used=%s fallback_reason_code=%s",
        result.status, result.usable, output_origin,
        fallback_used, fallback_reason_code,
    )

    return {
        "analysis": analysis,
        "agent": "ProjectDNAAgent",
        # llmUsed/source/provider/modelUsed describe the DISPLAYED
        # `analysis` above, not merely the Writer's own attempt -- see the
        # comment above this block. ollamaError/ollamaRawPreview remain the
        # Writer's raw diagnostic output regardless of what was displayed
        # (they were already diagnostic-only, never a provenance claim).
        "llmUsed": displayed_llm_used,
        "source": displayed_source,
        "provider": displayed_provider,
        "modelUsed": displayed_model,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project DNA analysis generated successfully",
        # ---- Additive fields (DNA fallback-provenance correction). Every
        # field above keeps its prior key/shape; everything below is new
        # and backward-compatible -- a caller that doesn't read these keys
        # is unaffected.
        "outputOrigin": output_origin,
        "fallbackUsed": fallback_used,
        "fallbackReasonCode": fallback_reason_code,
        "fallbackReasonMessage": fallback_reason_message,
        "providerAttempts": [
            {
                "stage": "writer",
                "provider": agent.last_provider,
                "model": agent.last_model_used,
                "success": agent.last_llm_used,
            },
        ],
    }