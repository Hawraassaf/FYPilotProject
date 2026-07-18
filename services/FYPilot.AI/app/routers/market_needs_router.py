import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.market_needs_agent import MarketNeedsAgent
from app.models.market_needs_models import (
    MarketNeedsRequest,
    MarketNeedsResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Demand Intelligence"])


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
        return await MarketNeedsAgent().analyze(request)
    except Exception as exception:
        logger.exception("Market Demand Intelligence failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Market Demand Intelligence failed.",
        ) from exception
