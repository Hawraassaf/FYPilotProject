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


class MarketNeedsRequest(CamelModel):
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
    country_context: str = Field(
        default="Lebanon",
        alias="countryContext",
        max_length=120,
    )
    history_years: int = Field(
        default=6,
        alias="historyYears",
        ge=4,
        le=10,
    )
    forecast_years: int = Field(
        default=3,
        alias="forecastYears",
        ge=1,
        le=5,
    )
    use_search: bool = Field(
        default=True,
        alias="useSearch",
    )


class SourceItem(CamelModel):
    title: str
    url: str
    publisher: str = ""
    relevance: str = ""
    relevance_score: int = Field(
        default=50,
        alias="relevanceScore",
        ge=0,
        le=100,
    )
    source_type: str = Field(
        default="External",
        alias="sourceType",
    )
    is_verified: bool = Field(
        default=False,
        alias="isVerified",
    )


class SimilarSolution(CamelModel):
    name: str
    description: str
    similarity: str = "medium"


class TrendSignal(CamelModel):
    topic: str
    direction: str
    evidence: str
    source_url: str | None = Field(
        default=None,
        alias="sourceUrl",
    )


class ScoreBreakdown(CamelModel):
    problem_evidence: int = Field(
        alias="problemEvidence",
        ge=0,
        le=100,
    )
    market_fit: int = Field(
        alias="marketFit",
        ge=0,
        le=100,
    )
    university_value: int = Field(
        alias="universityValue",
        ge=0,
        le=100,
    )
    competition_opportunity: int = Field(
        alias="competitionOpportunity",
        ge=0,
        le=100,
    )
    technology_momentum: int = Field(
        alias="technologyMomentum",
        ge=0,
        le=100,
    )


class YearlyMarketPoint(CamelModel):
    year: int
    problem_signal: int = Field(
        alias="problemSignal",
        ge=0,
        le=100,
    )
    adoption_signal: int = Field(
        alias="adoptionSignal",
        ge=0,
        le=100,
    )
    job_demand_signal: int = Field(
        alias="jobDemandSignal",
        ge=0,
        le=100,
    )
    technology_momentum_signal: int = Field(
        alias="technologyMomentumSignal",
        ge=0,
        le=100,
    )
    demand_index: float = Field(
        alias="demandIndex",
        ge=0,
        le=100,
    )
    confidence_score: int = Field(
        alias="confidenceScore",
        ge=0,
        le=100,
    )
    evidence_summary: str = Field(
        alias="evidenceSummary",
    )
    source_urls: list[str] = Field(
        default_factory=list,
        alias="sourceUrls",
    )


class AnnualForecastPoint(CamelModel):
    year: int
    predicted_score: float = Field(
        alias="predictedScore",
        ge=0,
        le=100,
    )
    lower_bound: float = Field(
        alias="lowerBound",
        ge=0,
        le=100,
    )
    upper_bound: float = Field(
        alias="upperBound",
        ge=0,
        le=100,
    )


class AnnualTrendAnalysis(CamelModel):
    direction: str
    strength: str
    slope_per_year: float = Field(
        alias="slopePerYear",
    )
    total_change: float = Field(
        alias="totalChange",
    )
    volatility: float
    r_squared: float = Field(
        alias="rSquared",
        ge=0,
        le=1,
    )
    summary: str


class AnnualForecast(CamelModel):
    status: str
    forecast_ready: bool = Field(
        alias="forecastReady",
    )
    forecast_reliable: bool = Field(
        alias="forecastReliable",
    )
    model_used: str | None = Field(
        default=None,
        alias="modelUsed",
    )
    model_mae: float | None = Field(
        default=None,
        alias="modelMae",
        ge=0,
    )
    naive_mae: float | None = Field(
        default=None,
        alias="naiveMae",
        ge=0,
    )
    average_yearly_confidence: float = Field(
        alias="averageYearlyConfidence",
        ge=0,
        le=100,
    )
    historical_start_year: int | None = Field(
        default=None,
        alias="historicalStartYear",
    )
    historical_end_year: int | None = Field(
        default=None,
        alias="historicalEndYear",
    )
    forecast_horizon_years: int = Field(
        alias="forecastHorizonYears",
        ge=1,
        le=5,
    )
    trend: AnnualTrendAnalysis
    forecast_points: list[AnnualForecastPoint] = Field(
        default_factory=list,
        alias="forecastPoints",
    )
    warning: str | None = None


class MarketNeedsResponse(CamelModel):
    source: str
    provider: str
    model_used: str | None = Field(
        default=None,
        alias="modelUsed",
    )

    search_used: bool = Field(alias="searchUsed")
    search_provider: str | None = Field(
        default=None,
        alias="searchProvider",
    )
    grounded_in_live_data: bool = Field(
        alias="groundedInLiveData",
    )

    confidence_level: str = Field(alias="confidenceLevel")
    confidence_score: int = Field(
        alias="confidenceScore",
        ge=0,
        le=100,
    )
    cloud_error: str | None = Field(
        default=None,
        alias="cloudError",
    )

    market_demand: str = Field(alias="marketDemand")
    demand_score: int = Field(
        alias="demandScore",
        ge=0,
        le=100,
    )
    score_breakdown: ScoreBreakdown = Field(alias="scoreBreakdown")

    target_sector: str = Field(alias="targetSector")
    problem_evidence: list[str] = Field(
        default_factory=list,
        alias="problemEvidence",
    )
    similar_solutions: list[SimilarSolution] = Field(
        default_factory=list,
        alias="similarSolutions",
    )
    sources: list[SourceItem] = Field(default_factory=list)
    trend_signals: list[TrendSignal] = Field(
        default_factory=list,
        alias="trendSignals",
    )

    yearly_points: list[YearlyMarketPoint] = Field(
        default_factory=list,
        alias="yearlyPoints",
    )
    annual_forecast: AnnualForecast = Field(alias="annualForecast")
    historical_data_note: str = Field(alias="historicalDataNote")

    lebanese_market_fit: str = Field(
        default="",
        alias="lebaneseMarketFit",
    )
    university_value: str = Field(
        default="",
        alias="universityValue",
    )
    risks: list[str] = Field(default_factory=list)
    recommendation: str = ""
    next_steps: list[str] = Field(
        default_factory=list,
        alias="nextSteps",
    )

    analyzed_at: datetime = Field(alias="analyzedAt")

    # AI Quality Passport — the ReviewPipeline result for this analysis.
    # Optional/defaulted so a raw candidate (before review) can still
    # validate against this same schema; populated by the router.
    review: dict[str, Any] | None = None
