from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CamelModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        extra="ignore",
    )


class MarketFootprintRequest(CamelModel):
    project_title: str = Field(
        alias="projectTitle",
        min_length=2,
        max_length=300,
    )
    problem_statement: str = Field(
        alias="problemStatement",
        min_length=5,
        max_length=5000,
    )
    target_users: str = Field(
        default="",
        alias="targetUsers",
        max_length=2000,
    )
    domain: str = Field(
        default="General Software System",
        max_length=200,
    )
    technologies: str = Field(
        default="",
        max_length=2000,
    )
    use_search: bool = Field(
        default=True,
        alias="useSearch",
    )


class RegionScoreBreakdown(CamelModel):
    problem_urgency: int = Field(alias="problemUrgency", ge=0, le=100)
    geographic_fit: int = Field(alias="geographicFit", ge=0, le=100)
    adoption_readiness: int = Field(alias="adoptionReadiness", ge=0, le=100)
    competition_gap: int = Field(alias="competitionGap", ge=0, le=100)
    target_user_reachability: int = Field(
        alias="targetUserReachability", ge=0, le=100
    )
    technology_momentum: int = Field(alias="technologyMomentum", ge=0, le=100)
    evidence_strength: int = Field(alias="evidenceStrength", ge=0, le=100)


class RegionResult(CamelModel):
    region_key: str = Field(alias="regionKey")
    region_name: str = Field(alias="regionName")
    opportunity_score: int | None = Field(default=None, alias="opportunityScore")
    confidence_score: int = Field(default=0, alias="confidenceScore")
    demand_level: str = Field(default="", alias="demandLevel")
    competition_pressure: str = Field(default="medium", alias="competitionPressure")
    evidence_summary: str = Field(default="", alias="evidenceSummary")
    score_breakdown: RegionScoreBreakdown | None = Field(
        default=None, alias="scoreBreakdown"
    )
    source_titles: list[str] = Field(default_factory=list, alias="sourceTitles")
    source_urls: list[str] = Field(default_factory=list, alias="sourceUrls")


class FootprintSourceItem(CamelModel):
    title: str
    url: str
    publisher: str = ""
    relevance: str = ""
    relevance_score: int = Field(default=50, alias="relevanceScore", ge=0, le=100)
    is_verified: bool = Field(default=False, alias="isVerified")
    regions: list[str] = Field(default_factory=list)


class MarketFootprintResponse(CamelModel):
    status: str = "ready"
    provider: str = "none"
    model_used: str | None = Field(default=None, alias="modelUsed")
    grounded_in_live_data: bool = Field(default=False, alias="groundedInLiveData")

    overall_opportunity_score: int | None = Field(
        default=None, alias="overallOpportunityScore"
    )
    overall_confidence_score: int = Field(default=0, alias="overallConfidenceScore")
    overall_demand_level: str = Field(default="", alias="overallDemandLevel")

    best_launch_market: str = Field(default="", alias="bestLaunchMarket")
    best_launch_reason: str = Field(default="", alias="bestLaunchReason")
    expansion_path: list[str] = Field(default_factory=list, alias="expansionPath")

    why_demanded: list[str] = Field(default_factory=list, alias="whyDemanded")
    strategic_recommendation: str = Field(
        default="", alias="strategicRecommendation"
    )
    limitations: list[str] = Field(default_factory=list)

    regions: list[RegionResult] = Field(default_factory=list)
    sources: list[FootprintSourceItem] = Field(default_factory=list)

    analyzed_at: datetime = Field(alias="analyzedAt")

    # AI Quality Passport — the ReviewPipeline result for this analysis.
    # Optional/defaulted so a raw candidate (before review) can still
    # validate against this same schema; populated by the router.
    review: dict[str, Any] | None = None
