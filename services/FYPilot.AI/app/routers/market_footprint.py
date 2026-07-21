import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.market_footprint_agent import MarketFootprintAgent
from app.models.market_footprint_models import (
    MarketFootprintRequest,
    MarketFootprintResponse,
)
from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline
from app.review.response import build_review_response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Insight — Regional Demand Footprint"])


def _build_review_context(request: MarketFootprintRequest) -> ReviewContext:
    trusted_structural_context = {"useSearch": request.use_search}

    untrusted_project_text = {
        "projectTitle": request.project_title,
        "problemStatement": request.problem_statement,
        "targetUsers": request.target_users,
        "domain": request.domain,
        "technologies": request.technologies,
    }

    return ReviewContext(
        agent_name="MarketFootprintAgent",
        trusted_system_instructions=(
            "MarketFootprintAgent: compares regional opportunity (Lebanon, "
            "MENA, Global) for a final year project idea, grounded in a "
            "live web-search step. All opportunity/confidence scores and "
            "matched source URLs are always computed deterministically, "
            "never written by the LLM."
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
    "/analyze-market-footprint",
    response_model=MarketFootprintResponse,
    response_model_by_alias=True,
)
async def analyze_market_footprint(
    request: MarketFootprintRequest,
) -> MarketFootprintResponse:
    try:
        agent = MarketFootprintAgent()

        # Run the real analysis (live search + LLM ratings + deterministic
        # scoring) exactly once, up front -- this is the same work
        # analyze()/generate_candidate() would otherwise trigger, just done
        # here so the REAL, already-verified sources it discovers can seed
        # allowed_source_metadata below, letting url_mode="source_metadata_only"
        # correctly allow those exact URLs without ever trusting an
        # LLM-authored one.
        raw_result = await asyncio.to_thread(agent._analyze_sync, request)

        context = _build_review_context(request)
        context.allowed_source_metadata = [
            source.model_dump() for source in raw_result.sources
        ]

        pipeline = ReviewPipeline("MarketFootprintAgent")

        # ReviewPipeline is a synchronous component (real, blocking Reviewer/
        # Rewrite provider calls) — offloaded to a worker thread so this
        # async route never blocks the event loop.
        result = await asyncio.to_thread(
            pipeline.run,
            lambda: agent.generate_candidate_from_result(raw_result),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
        )

        final = (
            result.output
            if result.usable
            else agent.build_safe_fallback(request).model_dump()
        )

        response = MarketFootprintResponse.model_validate(final)
        response.review = build_review_response(result)
        return response
    except Exception as exception:
        logger.exception("Market Footprint analysis failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Market Footprint analysis failed.",
        ) from exception
