import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.market_footprint_agent import MarketFootprintAgent
from app.models.market_footprint_models import (
    MarketFootprintRequest,
    MarketFootprintResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Insight — Regional Demand Footprint"])


@router.post(
    "/analyze-market-footprint",
    response_model=MarketFootprintResponse,
    response_model_by_alias=True,
)
async def analyze_market_footprint(
    request: MarketFootprintRequest,
) -> MarketFootprintResponse:
    try:
        return await MarketFootprintAgent().analyze(request)
    except Exception as exception:
        logger.exception("Market Footprint analysis failed.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Market Footprint analysis failed.",
        ) from exception
