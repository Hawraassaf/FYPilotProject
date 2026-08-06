import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, status

from app.agents.market_needs_agent import MarketNeedsAgent
from app.models.market_needs_models import (
    MarketNeedsRequest,
    MarketNeedsResponse,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Demand Intelligence"])

# Reserved for the semantic Reviewer / one structural repair / one semantic
# rewrite / final re-review -- the Writer must never consume this. Mirrors
# Idea Generation's identical global_deadline/writer_deadline split (see
# routers/ideas.py's _WRITER_TIME_RESERVE_SECONDS = 30.0 out of a 120s total
# budget: registry.py's ProjectIdeaAgent.max_total_seconds) -- reused here
# unchanged rather than re-derived, because MarketNeedsAgent's own registry
# budget (registry.py's MarketNeedsAgent.max_total_seconds) is the SAME
# 120.0s, its Writer stage makes the SAME shape of two sequential provider
# calls (a live search, then structured-JSON generation) before the
# Reviewer runs once, and its Reviewer/Rewrite stages run on the SAME
# "standard" tier as its own Writer (no tier mismatch to compensate for,
# unlike Roadmap's earlier fix). 30s leaves the Reviewer at least one
# meaningful provider attempt (ProviderChain's shared
# _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor is 4s) plus headroom for the
# deterministic parsing/firewall/schema-validation steps around it, while
# still leaving the Writer the large majority of the 120s budget for its
# own two-call sequence. Previously there was no writer_deadline at all --
# _analyze_sync()'s search_web()/generate_json() calls (each able to use
# their provider's full independent timeout, DeepInfra/Groq ~60s, Ollama
# ~90s) ran entirely BEFORE pipeline.run() was ever invoked, so a
# slow-but-successful candidate could arrive only for ReviewPipeline.run's
# own _time_budget_exceeded check (working from its own internally
# self-computed deadline, which never reached _analyze_sync in the first
# place) to discard it before the Reviewer ever ran.
_WRITER_TIME_RESERVE_SECONDS = 30.0


def _build_review_context(request: MarketNeedsRequest) -> ReviewContext:
    trusted_structural_context = {
        "countryContext": request.country_context,
        "useSearch": request.use_search,
    }

    untrusted_project_text = {
        "projectTitle": request.project_title,
        "problemStatement": request.problem_statement,
        "targetUsers": request.target_users,
        "domain": request.domain,
        "technologies": request.technologies,
    }

    return ReviewContext(
        agent_name="MarketNeedsAgent",
        trusted_system_instructions=(
            "MarketNeedsAgent: validates current market demand for a final "
            "year project idea, grounded in live web research. The demand "
            "score and score breakdown are always computed "
            "deterministically, never written by the LLM."
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


@router.post(
    "/analyze-market-demand",
    response_model=MarketNeedsResponse,
    response_model_by_alias=True,
)
@router.post(
    "/analyze-market-needs",
    response_model=MarketNeedsResponse,
    response_model_by_alias=True,
    include_in_schema=False,
)
async def analyze_market_needs(
    request: MarketNeedsRequest,
) -> MarketNeedsResponse:
    try:
        agent = MarketNeedsAgent()
        pipeline = ReviewPipeline("MarketNeedsAgent")

        # ONE absolute monotonic deadline, computed here from the registry's
        # max_total_seconds (120s) -- passed UNCHANGED to ReviewPipeline
        # (schema/structural-repair/semantic-review/rewrite/final-review all
        # measure against this exact value via ReviewPipeline.run's own
        # deadline threading). writer_deadline (global - the review reserve
        # above) reaches _analyze_sync() DIRECTLY -- NOT via pipeline.run's
        # writer_call_fn closure -- because MarketNeedsAgent's real Writer
        # work (search + generation) runs BEFORE pipeline.run() is ever
        # called; see generate_candidate_from_result's docstring. This
        # matches Roadmap/Idea Generation's identical global_deadline/
        # writer_deadline split, adapted to this agent's pre-computed-result
        # architecture.
        global_deadline = time.monotonic() + pipeline.config.max_total_seconds
        writer_deadline = global_deadline - _WRITER_TIME_RESERVE_SECONDS

        logger.info(
            "market_needs.request_received writer_budget_seconds=%.1f total_budget_seconds=%.1f",
            writer_deadline - time.monotonic(), pipeline.config.max_total_seconds,
        )

        # Run the real analysis (live research + deterministic scoring/
        # forecasting) exactly once, up front -- the same work
        # analyze()/generate_candidate() would otherwise trigger, just done
        # here so the REAL, already-verified sources it discovers can seed
        # allowed_source_metadata below, letting url_mode="source_metadata_only"
        # correctly allow those exact URLs without ever trusting an
        # LLM-authored one.
        raw_result = await asyncio.to_thread(
            agent._analyze_sync, request, deadline=writer_deadline,
        )

        context = _build_review_context(request)
        context.allowed_source_metadata = [
            source.model_dump() for source in raw_result.sources
        ]

        # ReviewPipeline is a synchronous component (real, blocking Reviewer/
        # Rewrite provider calls) — offloaded to a worker thread so this
        # async route never blocks the event loop.
        result = await asyncio.to_thread(
            pipeline.run,
            lambda: agent.generate_candidate_from_result(raw_result),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )

        logger.info(
            "market_needs.response_built status=%s usable=%s writer_deadline_reason=%s",
            result.status, result.usable, agent.last_fallback_reason_code,
        )

        final = (
            result.output
            if result.usable
            else agent.build_safe_fallback(request).model_dump()
        )

        response = MarketNeedsResponse.model_validate(final)
        response.review = build_review_response(result)
        return response
    except Exception as exception:
        logger.exception("Market Demand Intelligence failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Market Demand Intelligence failed.",
        ) from exception
