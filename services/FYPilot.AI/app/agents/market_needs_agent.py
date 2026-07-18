from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from app.models.market_needs_models import (
    MarketNeedsRequest,
    MarketNeedsResponse,
    ScoreBreakdown,
    SimilarSolution,
    SourceItem,
    TrendSignal,
    YearlyMarketPoint,
)
from app.services.llm_provider import ProviderChain
from app.services.market_needs_scoring import (
    calculate_confidence_score,
    calculate_demand_score,
    calculate_yearly_confidence,
    calculate_yearly_demand_index,
    clamp_score,
    confidence_label,
    demand_label,
)
from app.services.yearly_market_forecasting import build_annual_forecast


class MarketNeedsAgent:
    """
    Current market validation plus source-backed annual intelligence.

    Important:
    - The current score is a deterministic evidence score.
    - Annual points are accepted only when linked to real provider sources.
    - Forecasting uses annual indices, never repeated same-day app refreshes.
    """

    _recognized_domains = {
        "worldbank.org",
        "un.org",
        "unesco.org",
        "itu.int",
        "oecd.org",
        "who.int",
        "gov.lb",
        "aub.edu.lb",
        "lau.edu.lb",
        "liu.edu.lb",
        "usek.edu.lb",
        "usj.edu.lb",
        "ul.edu.lb",
        "weforum.org",
        "gartner.com",
        "mckinsey.com",
        "statista.com",
        "linkedin.com",
        "ilo.org",
        "imf.org",
    }

    def __init__(self) -> None:
        self.chain = ProviderChain()

    async def analyze(
        self,
        request: MarketNeedsRequest,
    ) -> MarketNeedsResponse:
        prompt = self._build_prompt(request)

        result = await asyncio.to_thread(
            self.chain.generate_json,
            prompt,
            use_search=request.use_search,
        )

        if not getattr(result, "ok", False) or not getattr(
            result,
            "data",
            None,
        ):
            return self._fallback(
                request,
                getattr(result, "error", None),
            )

        return self._create_response(
            request=request,
            data=result.data,
            provider=str(
                getattr(result, "provider", "unknown") or "unknown"
            ),
            model=(str(getattr(result, "model", "")) or None),
            error=(str(getattr(result, "error", "")) or None),
            provider_result=result,
        )

    def _create_response(
        self,
        *,
        request: MarketNeedsRequest,
        data: dict[str, Any],
        provider: str,
        model: str | None,
        error: str | None,
        provider_result: Any,
    ) -> MarketNeedsResponse:
        search_used = bool(
            request.use_search
            and getattr(provider_result, "search_used", False)
        )

        old_score = clamp_score(data.get("demandScore"), default=60)
        raw_breakdown = self._dict(data.get("scoreBreakdown"))
        breakdown = ScoreBreakdown(
            problemEvidence=clamp_score(
                raw_breakdown.get("problemEvidence", old_score)
            ),
            marketFit=clamp_score(
                raw_breakdown.get("marketFit", old_score)
            ),
            universityValue=clamp_score(
                raw_breakdown.get("universityValue", old_score)
            ),
            competitionOpportunity=clamp_score(
                raw_breakdown.get("competitionOpportunity", old_score)
            ),
            technologyMomentum=clamp_score(
                raw_breakdown.get("technologyMomentum", old_score)
            ),
        )

        sources = self._normalize_sources(
            self._extract_provider_sources(provider_result),
            maximum=14,
        )
        grounded = search_used and bool(sources)

        yearly_points = self._normalize_yearly_points(
            raw_points=self._list(data.get("yearlyEvidence")),
            sources=sources,
            request=request,
        )
        annual_forecast = build_annual_forecast(
            yearly_points,
            request.forecast_years,
        )

        problem_evidence = self._string_list(
            data.get("problemEvidence"),
            maximum=6,
        )
        unique_domains = {
            self._domain(source.url)
            for source in sources
            if self._domain(source.url)
        }
        verified_count = sum(
            1 for source in sources if source.is_verified
        )
        confidence_score = calculate_confidence_score(
            grounded_in_live_data=grounded,
            valid_source_count=len(sources),
            verified_source_count=verified_count,
            problem_evidence_count=len(problem_evidence),
            unique_domain_count=len(unique_domains),
            source_backed_year_count=len(yearly_points),
        )
        demand_score = calculate_demand_score(breakdown)

        similar_solutions = [
            SimilarSolution(
                name=self._text(item.get("name"), maximum=250),
                description=self._text(
                    item.get("description"),
                    maximum=1500,
                ),
                similarity=self._similarity(item.get("similarity")),
            )
            for item in self._list(data.get("similarSolutions"))
            if isinstance(item, dict) and self._text(item.get("name"))
        ][:6]

        trend_signals = [
            TrendSignal(
                topic=self._text(item.get("topic"), maximum=250),
                direction=self._direction(item.get("direction")),
                evidence=self._text(item.get("evidence"), maximum=1500),
                sourceUrl=self._matched_source_url(
                    item.get("sourceTitle"),
                    item.get("sourceUrl"),
                    sources,
                ),
            )
            for item in self._list(data.get("trendSignals"))
            if isinstance(item, dict) and self._text(item.get("topic"))
        ][:6]

        note = (
            "Annual values are normalized evidence indices from 0 to 100. "
            "They are not revenue, market size, or Google Trends values. "
            "A year is included only when it can be linked to a real source "
            "returned by the live research provider."
        )

        return MarketNeedsResponse(
            source=(f"{provider}-live-research" if grounded else provider),
            provider=provider,
            modelUsed=model,
            searchUsed=search_used,
            searchProvider=(
                f"{provider.title()} grounded search" if search_used else None
            ),
            groundedInLiveData=grounded,
            confidenceLevel=confidence_label(confidence_score),
            confidenceScore=confidence_score,
            cloudError=error,
            marketDemand=demand_label(demand_score),
            demandScore=demand_score,
            scoreBreakdown=breakdown,
            targetSector=self._text(
                data.get("targetSector"),
                default=request.domain,
                maximum=300,
            ),
            problemEvidence=problem_evidence,
            similarSolutions=similar_solutions,
            sources=sources,
            trendSignals=trend_signals,
            yearlyPoints=yearly_points,
            annualForecast=annual_forecast,
            historicalDataNote=note,
            lebaneseMarketFit=self._text(
                data.get("lebaneseMarketFit"),
                maximum=5000,
            ),
            universityValue=self._text(
                data.get("universityValue"),
                maximum=5000,
            ),
            risks=self._string_list(data.get("risks"), maximum=6),
            recommendation=self._text(
                data.get("recommendation"),
                maximum=5000,
            ),
            nextSteps=self._string_list(data.get("nextSteps"), maximum=6),
            analyzedAt=datetime.now(timezone.utc),
        )

    def _normalize_yearly_points(
        self,
        *,
        raw_points: list[Any],
        sources: list[SourceItem],
        request: MarketNeedsRequest,
    ) -> list[YearlyMarketPoint]:
        current_year = datetime.now(timezone.utc).year
        minimum_year = current_year - request.history_years + 1
        points: list[YearlyMarketPoint] = []

        for raw in raw_points:
            if not isinstance(raw, dict):
                continue

            try:
                year = int(raw.get("year"))
            except (TypeError, ValueError):
                continue

            if year < minimum_year or year > current_year:
                continue

            requested_titles = self._string_list(
                raw.get("sourceTitles"),
                maximum=6,
            )
            matched_sources = self._match_year_sources(
                year=year,
                requested_titles=requested_titles,
                sources=sources,
            )

            # Correctness rule: no real source means no historical point.
            if not matched_sources:
                continue

            problem = clamp_score(raw.get("problemSignal"), 50)
            adoption = clamp_score(raw.get("adoptionSignal"), 50)
            jobs = clamp_score(raw.get("jobDemandSignal"), 50)
            technology = clamp_score(
                raw.get("technologyMomentumSignal"),
                50,
            )
            summary = self._text(
                raw.get("evidenceSummary"),
                maximum=1600,
            )
            explicit_year = any(
                self._source_mentions_year(source, year)
                for source in matched_sources
            )
            verified_count = sum(
                1 for source in matched_sources if source.is_verified
            )
            confidence = calculate_yearly_confidence(
                source_count=len(matched_sources),
                verified_source_count=verified_count,
                has_explicit_year_evidence=explicit_year,
                evidence_summary_present=bool(summary),
            )

            points.append(
                YearlyMarketPoint(
                    year=year,
                    problemSignal=problem,
                    adoptionSignal=adoption,
                    jobDemandSignal=jobs,
                    technologyMomentumSignal=technology,
                    demandIndex=calculate_yearly_demand_index(
                        problem_signal=problem,
                        adoption_signal=adoption,
                        job_demand_signal=jobs,
                        technology_momentum_signal=technology,
                    ),
                    confidenceScore=confidence,
                    evidenceSummary=summary,
                    sourceUrls=[source.url for source in matched_sources[:4]],
                )
            )

        # Keep the highest-confidence result for duplicate years.
        by_year: dict[int, YearlyMarketPoint] = {}
        for point in points:
            existing = by_year.get(point.year)
            if existing is None or point.confidence_score > existing.confidence_score:
                by_year[point.year] = point

        return [by_year[year] for year in sorted(by_year)]

    def _match_year_sources(
        self,
        *,
        year: int,
        requested_titles: list[str],
        sources: list[SourceItem],
    ) -> list[SourceItem]:
        ranked: list[tuple[float, SourceItem]] = []

        for source in sources:
            score = 0.0
            if self._source_mentions_year(source, year):
                score += 0.75

            source_title = self._normalize_text(source.title)
            for requested in requested_titles:
                requested_title = self._normalize_text(requested)
                if not requested_title or not source_title:
                    continue
                similarity = SequenceMatcher(
                    None,
                    requested_title,
                    source_title,
                ).ratio()
                if requested_title in source_title or source_title in requested_title:
                    similarity = max(similarity, 0.92)
                score = max(score, similarity)

            if score >= 0.60:
                ranked.append((score, source))

        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].is_verified,
                item[1].relevance_score,
            ),
            reverse=True,
        )
        return [source for _, source in ranked[:4]]

    @staticmethod
    def _source_mentions_year(source: SourceItem, year: int) -> bool:
        text = f"{source.title} {source.relevance} {source.url}"
        return str(year) in text

    def _matched_source_url(
        self,
        source_title: object,
        proposed_url: object,
        sources: list[SourceItem],
    ) -> str | None:
        proposed = self._text(proposed_url, maximum=2000)
        for source in sources:
            if proposed and source.url.rstrip("/") == proposed.rstrip("/"):
                return source.url

        title = self._normalize_text(source_title)
        if not title:
            return None

        best: tuple[float, str] | None = None
        for source in sources:
            ratio = SequenceMatcher(
                None,
                title,
                self._normalize_text(source.title),
            ).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, source.url)

        return best[1] if best and best[0] >= 0.65 else None

    def _build_prompt(self, request: MarketNeedsRequest) -> str:
        current_year = datetime.now(timezone.utc).year
        start_year = current_year - request.history_years + 1

        return f"""
You are the Market Demand Intelligence Agent for FYPilot.

Analyze whether this final-year software project solves a real problem and how
its demand has changed across calendar years.

PROJECT
Title: {request.project_title}
Problem: {request.problem_statement}
Target users: {request.target_users}
Domain: {request.domain}
Technologies: {request.technologies}
Market scope: {request.country_context}
Historical window: {start_year} to {current_year}

RESEARCH RULES
- Use live web research before answering.
- Prefer official institutions, government, universities, recognized research,
  job-market reports, industry reports, and credible organizations.
- Use Lebanon evidence first; when unavailable, use MENA or global evidence
  and state that limitation.
- Never invent a URL, statistic, annual value, publication, or market size.
- Current and annual scores are evidence indices from 0 to 100, not money,
  revenue, total-addressable market, or Google Trends values.
- Historical years must be backed by sources returned during this search.
- For each yearly point, copy sourceTitles exactly from the search results you
  used. The application will reject years that cannot be matched to real tool
  sources.
- Do not output a sources array. Real source URLs are read from provider tool
  metadata, not trusted from generated JSON.

CURRENT SCORE CATEGORIES
- problemEvidence: strength that the problem exists now.
- marketFit: fit with the requested geographic scope and target users.
- universityValue: academic, research, operational, or partnership value.
- competitionOpportunity: remaining opportunity after competitors.
- technologyMomentum: present adoption and technical relevance.

ANNUAL SIGNAL CATEGORIES
For each year with enough real evidence, rate:
- problemSignal: evidence that the problem was important that year.
- adoptionSignal: adoption or organizational interest that year.
- jobDemandSignal: employment, procurement, or implementation demand.
- technologyMomentumSignal: research, funding, standards, or technology activity.

Do not force all years. Omit years without defensible source evidence.
Aim for 4 to {request.history_years} distinct years when the evidence supports it.

Return ONLY valid JSON in this exact shape:
{{
  "scoreBreakdown": {{
    "problemEvidence": 0,
    "marketFit": 0,
    "universityValue": 0,
    "competitionOpportunity": 0,
    "technologyMomentum": 0
  }},
  "targetSector": "",
  "problemEvidence": [""],
  "yearlyEvidence": [
    {{
      "year": {start_year},
      "problemSignal": 0,
      "adoptionSignal": 0,
      "jobDemandSignal": 0,
      "technologyMomentumSignal": 0,
      "evidenceSummary": "",
      "sourceTitles": [""]
    }}
  ],
  "similarSolutions": [
    {{
      "name": "",
      "description": "",
      "similarity": "low|medium|high"
    }}
  ],
  "trendSignals": [
    {{
      "topic": "",
      "direction": "rising|stable|falling",
      "evidence": "",
      "sourceTitle": ""
    }}
  ],
  "lebaneseMarketFit": "",
  "universityValue": "",
  "risks": [""],
  "recommendation": "",
  "nextSteps": [""]
}}
"""

    def _fallback(
        self,
        request: MarketNeedsRequest,
        error: object,
    ) -> MarketNeedsResponse:
        breakdown = ScoreBreakdown(
            problemEvidence=45,
            marketFit=45,
            universityValue=50,
            competitionOpportunity=45,
            technologyMomentum=45,
        )
        annual_forecast = build_annual_forecast([], request.forecast_years)
        demand_score = calculate_demand_score(breakdown)

        return MarketNeedsResponse(
            source="fallback",
            provider="none",
            modelUsed=None,
            searchUsed=False,
            searchProvider=None,
            groundedInLiveData=False,
            confidenceLevel="low",
            confidenceScore=10,
            cloudError=self._text(error, maximum=1000),
            marketDemand=demand_label(demand_score),
            demandScore=demand_score,
            scoreBreakdown=breakdown,
            targetSector=request.domain,
            problemEvidence=[],
            similarSolutions=[],
            sources=[],
            trendSignals=[],
            yearlyPoints=[],
            annualForecast=annual_forecast,
            historicalDataNote=(
                "No source-backed annual data was produced because live "
                "research was unavailable."
            ),
            lebaneseMarketFit="",
            universityValue="",
            risks=[
                "Live research was unavailable, so this result must not be "
                "used as a final market decision."
            ],
            recommendation=(
                "Retry when Groq or Gemini grounded search is available."
            ),
            nextSteps=[
                "Verify cloud provider API keys.",
                "Confirm live-search configuration.",
                "Run the analysis again.",
            ],
            analyzedAt=datetime.now(timezone.utc),
        )

    def _extract_provider_sources(self, provider_result: Any) -> list[Any]:
        sources: list[Any] = []
        for attribute in (
            "sources",
            "citations",
            "search_results",
            "searchResults",
        ):
            sources.extend(self._list(getattr(provider_result, attribute, None)))
        return sources

    def _normalize_sources(
        self,
        raw_sources: list[Any],
        *,
        maximum: int,
    ) -> list[SourceItem]:
        results: list[SourceItem] = []
        seen: set[str] = set()

        for item in raw_sources:
            if isinstance(item, str):
                item = {"url": item}
            if not isinstance(item, dict):
                continue

            url = self._text(item.get("url") or item.get("link"), maximum=2000)
            if not self._valid_url(url):
                continue

            normalized_url = url.rstrip("/").lower()
            if normalized_url in seen:
                continue
            seen.add(normalized_url)

            domain = self._domain(url)
            publisher = self._text(
                item.get("publisher"),
                default=domain,
                maximum=250,
            )
            snippet = self._text(
                item.get("snippet") or item.get("relevance"),
                maximum=1800,
            )

            results.append(
                SourceItem(
                    title=self._text(
                        item.get("title"),
                        default=publisher or domain,
                        maximum=500,
                    ),
                    url=url,
                    publisher=publisher,
                    relevance=snippet,
                    relevanceScore=clamp_score(
                        item.get("relevanceScore", 65)
                    ),
                    sourceType=self._source_type(domain),
                    isVerified=self._is_recognized_domain(domain),
                )
            )

        return sorted(
            results,
            key=lambda source: (
                source.is_verified,
                source.relevance_score,
            ),
            reverse=True,
        )[:maximum]

    def _is_recognized_domain(self, domain: str) -> bool:
        return any(
            domain == known or domain.endswith(f".{known}")
            for known in self._recognized_domains
        )

    @staticmethod
    def _source_type(domain: str) -> str:
        if domain.endswith(".gov") or ".gov." in domain or domain == "gov.lb":
            return "Official"
        if ".edu" in domain or domain.endswith(".ac.uk"):
            return "University"
        if any(token in domain for token in ("oecd", "worldbank", "un.org", "itu")):
            return "Institution"
        return "External"

    @staticmethod
    def _valid_url(value: object) -> bool:
        try:
            parsed = urlparse(str(value or "").strip())
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except Exception:
            return False

    @staticmethod
    def _domain(url: str) -> str:
        try:
            return urlparse(url).netloc.lower().removeprefix("www.")
        except Exception:
            return ""

    @staticmethod
    def _dict(value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _list(value: object) -> list[Any]:
        return value if isinstance(value, list) else []

    def _string_list(self, value: object, *, maximum: int) -> list[str]:
        return [
            self._text(item, maximum=1600)
            for item in self._list(value)
            if self._text(item)
        ][:maximum]

    @staticmethod
    def _text(
        value: object,
        default: str = "",
        maximum: int = 5000,
    ) -> str:
        text = str(value or default).strip()
        return text[:maximum]

    @staticmethod
    def _normalize_text(value: object) -> str:
        text = str(value or "").lower()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()

    @staticmethod
    def _similarity(value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"low", "medium", "high"} else "medium"

    @staticmethod
    def _direction(value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"rising", "stable", "falling"} else "stable"
