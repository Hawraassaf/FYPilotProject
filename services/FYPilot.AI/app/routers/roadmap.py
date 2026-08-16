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

import dataclasses
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.agents.project_roadmap_agent import ProjectRoadmapAgent, ProjectRoadmapRequest
from app.agents.roadmap import fallback_reason
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.registry import get_agent_config
from app.review.response import build_review_response
from app.services.llm_provider import LLMResult

router = APIRouter(tags=["Project Roadmap"])

logger = logging.getLogger("fypilot-roadmap-router")


class _SemanticReviewDisabledReviewerAgent:
    """
    TEMPORARY debug bypass (ROADMAP_SEMANTIC_REVIEW_DISABLED=1) -- makes the
    Reviewer stage report itself unavailable immediately, with no real
    provider call, instead of actually attempting (and potentially waiting
    on/timing out) a real semantic review. Unset/remove before relying on
    the output as fully reviewed: nothing about the semantic quality of the
    roadmap's wording (unsupported claims, contradictions) is checked while
    this is on -- only the deterministic gates already run inside
    ProjectRoadmapAgent.generate() (lifecycle coverage, deliverable
    coverage, schema/capacity validation) still apply, unaffected by this
    flag, since they run before ReviewPipeline is ever invoked.

    Deliberately returns ok=False (never raises, never fabricates an
    "approved" verdict) so guarded_call correctly marks this as
    provider_failed -- the SAME honest "review_unavailable"/"structural_
    only" labeling path already used when a real Reviewer call genuinely
    fails. Requires allow_unreviewed_output=True on the config passed to
    ReviewPipeline to actually ship the result (see the router below, which
    overrides ProjectRoadmapAgent's normal allow_unreviewed_output=False
    only for the duration of this flag).
    """

    def analyze(self, *args: Any, **kwargs: Any) -> LLMResult:
        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="Semantic review temporarily disabled via ROADMAP_SEMANTIC_REVIEW_DISABLED.",
        )

# Reserved for the semantic Reviewer / one structural repair / one semantic
# rewrite / final re-review -- the Writer must never consume this. Mirrors
# SE Documentation's identical global_deadline/writer_deadline split (see
# routers/se_documentation.py's _SEMANTIC_REVIEW_RESERVE_SECONDS = 300.0 out
# of a 1200s total budget), sized for Roadmap's 360s total (registry.py's
# ProjectRoadmapAgent.max_total_seconds): 60s leaves the Reviewer at least
# one meaningful provider attempt (ProviderChain's shared
# _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor is 4s) plus headroom for the
# deterministic parsing/firewall/schema-validation steps around it, while
# leaving the Writer a 300s budget (360s - 60s) for its own, comparatively
# more expensive structured phase-plan generation -- enough for DeepInfra's
# full 240s single-attempt cap (ROADMAP_DEEPINFRA_TIMEOUT_SECONDS, see
# llm_provider.py's _deepinfra_timing_for_tier) PLUS roughly 60s of Writer
# budget still available afterward for a Groq/Ollama fallback attempt, all
# still clamped against this same absolute Writer deadline (cap_timeout_to_
# deadline in _try_generate_phase_plan). Previously (90s total / 25s
# reserve, then 240s total / 120s DeepInfra cap) too little of the budget
# was left for DeepInfra's genuinely slow single-attempt latency, so a
# slow-but-successful candidate could either time out early or arrive only
# for ReviewPipeline.run's own _time_budget_exceeded check to discard it
# before the Reviewer ever ran (status="review_unavailable" with no
# candidate) -- this 360/300/60 split is sized specifically so DeepInfra's
# full attempt plus a fallback both fit inside the Writer budget.
_SEMANTIC_REVIEW_RESERVE_SECONDS = 60.0

# Maps a non-usable PipelineResult.status (review/models.py's PipelineStatus)
# to a typed Roadmap fallback reason -- covers every review-layer failure
# category that is NOT "the Writer itself never produced a candidate"
# (that finer-grained case is classified by the agent itself, see
# agent.last_fallback_reason_code / app/agents/roadmap/fallback_reason.py).
_REVIEW_STATUS_TO_FALLBACK_REASON: dict[str, str] = {
    "firewall_blocked": fallback_reason.BLOCKED_CONTENT,
    "schema_invalid": fallback_reason.SCHEMA_INVALID,
    "rejected": fallback_reason.SEMANTIC_REWRITE_FAILED,
    "review_unavailable": fallback_reason.REVIEWER_UNAVAILABLE,
}


def _classify_fallback(result: Any, agent: ProjectRoadmapAgent) -> tuple[str, str]:
    """
    Returns (fallbackReasonCode, fallbackReasonMessage) for a non-usable
    PipelineResult. When status=="provider_unavailable" the Writer itself
    never returned a usable candidate (agent.generate_candidate returned
    None), so the agent's own finer-grained classification -- set at the
    exact rejection point inside ProjectRoadmapAgent.generate() -- is used
    instead of the generic pipeline status. Every other non-usable status
    (firewall_blocked/schema_invalid/rejected/review_unavailable) means the
    Writer DID produce a candidate that a later stage rejected.
    """
    if result.status == "provider_unavailable":
        code = agent.last_fallback_reason_code or fallback_reason.UNKNOWN
    else:
        code = _REVIEW_STATUS_TO_FALLBACK_REASON.get(result.status, fallback_reason.UNKNOWN)

    return code, fallback_reason.safe_message_for(code)


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
    request_id = str(uuid.uuid4())

    agent = ProjectRoadmapAgent()
    agent.request_id = request_id
    context = _build_review_context(request)

    # TEMPORARY debug bypass -- see _SemanticReviewDisabledReviewerAgent's
    # docstring. allow_unreviewed_output is overridden to True ONLY for this
    # one pipeline instance (a config copy, via dataclasses.replace -- the
    # shared registry entry itself, and every other agent, is untouched) so
    # the Writer's candidate -- which has already passed the firewall,
    # schema validation, and every deterministic gate inside
    # ProjectRoadmapAgent.generate() -- ships as usable=True, honestly
    # labeled "review_unavailable"/structural_only, instead of being
    # discarded for the generic fallback the way a real Reviewer outage
    # would be under this agent's normal (and still default) False setting.
    semantic_review_disabled = os.getenv(
        "ROADMAP_SEMANTIC_REVIEW_DISABLED", "",
    ).strip().lower() in ("1", "true", "yes")

    # Reviewer/Rewrite tier must match the Writer's own tier (see
    # ReviewPipeline's constructor docstring) -- was "high" while the Writer
    # (ProjectRoadmapAgent) already used "roadmap"; fixed to "roadmap" so
    # Reviewer/Rewrite get both the cheap model and Roadmap's own tuned
    # DeepInfra/Ollama timeouts instead of the generic defaults.
    if semantic_review_disabled:
        logger.warning(
            "roadmap.semantic_review_DISABLED_bypassing_reviewer request_id=%s -- "
            "shipping the Writer's candidate unreviewed (deterministic gates still apply).",
            request_id,
        )
        pipeline = ReviewPipeline(
            "ProjectRoadmapAgent",
            tier="roadmap",
            reviewer_agent=_SemanticReviewDisabledReviewerAgent(),
            config=dataclasses.replace(
                get_agent_config("ProjectRoadmapAgent"),
                allow_unreviewed_output=True,
            ),
        )
    else:
        pipeline = ReviewPipeline("ProjectRoadmapAgent", tier="roadmap")

    # ONE absolute monotonic deadline, computed here from the registry's
    # max_total_seconds (90s) -- passed UNCHANGED to ReviewPipeline (schema/
    # structural-repair/semantic-review/rewrite/final-review all measure
    # against this exact value via ReviewPipeline.run's own deadline
    # threading -- see pipeline.py's _invoke_with_deadline, which already
    # forwards it to ReviewerAgent/RewriteAgent unchanged). The Writer
    # callable gets a SEPARATE, earlier writer_deadline (global - the
    # review reserve above) so it can never consume the time set aside for
    # review -- see _SEMANTIC_REVIEW_RESERVE_SECONDS's comment for why.
    global_deadline = time.monotonic() + pipeline.config.max_total_seconds
    writer_deadline = global_deadline - _SEMANTIC_REVIEW_RESERVE_SECONDS

    logger.info(
        "roadmap.request_received request_id=%s idea=%r team_size=%d hours_per_week=%d weeks=%d "
        "writer_budget_seconds=%.1f total_budget_seconds=%.1f",
        request_id, request.ideaTitle[:60], request.teamSize,
        request.availableHoursPerWeek, request.expectedDurationWeeks,
        writer_deadline - time.monotonic(), pipeline.config.max_total_seconds,
    )

    result = pipeline.run(
        lambda: agent.generate_candidate(request, deadline=writer_deadline),
        context,
        writer_trusted_parts=context.trusted_text_fields(),
        writer_untrusted_parts=context.untrusted_text_fields(),
        deadline=global_deadline,
    )

    if result.usable:
        final_roadmap = result.output
        fallback_used = False
        fallback_reason_code: str | None = None
        fallback_reason_message: str | None = None
        output_origin = (
            "ai_rewritten"
            if result.outputOrigin in ("structural_repair", "semantic_rewrite")
            else "ai_generated"
        )
    else:
        final_roadmap = agent.build_safe_fallback(request).model_dump()
        fallback_used = True
        fallback_reason_code, fallback_reason_message = _classify_fallback(result, agent)
        output_origin = "deterministic_fallback"

    logger.info(
        "roadmap.response_built request_id=%s status=%s usable=%s output_origin=%s "
        "fallback_used=%s fallback_reason_code=%s",
        request_id, result.status, result.usable, output_origin,
        fallback_used, fallback_reason_code,
    )

    return {
        "roadmap": final_roadmap,
        "agent": "ProjectRoadmapAgent",
        # llmUsed/source are UNCHANGED from before this batch: both already
        # correctly describe only the Writer stage's own attempt, and both
        # were already false/"dynamic-fallback" whenever a deterministic
        # fallback was used. The new fields below add PRECISION on top of
        # them (why), not a change to their existing meaning.
        "llmUsed": agent.last_llm_used,
        "source": agent.last_provider if agent.last_llm_used else "dynamic-fallback",
        "provider": agent.last_provider,
        "modelUsed": agent.last_model_used,
        "ollamaError": agent.last_error,
        "ollamaRawPreview": agent.last_raw_llm_response,
        "review": build_review_response(result),
        "generatedAt": datetime.utcnow().isoformat(),
        "message": "Project roadmap generated successfully",
        # ---- Additive fields (Roadmap fallback-provenance stabilization).
        # Every field above is unchanged; everything below is new and
        # backward-compatible -- a caller that doesn't read these keys is
        # completely unaffected.
        "requestId": request_id,
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
        "generationDiagnostics": {
            "phasePlanSource": agent.last_generation_source,
            "usablePhaseCount": agent.last_usable_phase_count,
            "lifecycleCoveragePassed": agent.last_lifecycle_coverage_passed,
            "missingLifecycleCategories": agent.last_missing_lifecycle_categories,
            "blockedTermTasksDropped": agent.last_blocked_term_tasks_dropped,
        },
    }
