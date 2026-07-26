from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.models.market_needs_models import (
    MarketNeedsRequest,
    MarketNeedsResponse,
    ScoreBreakdown,
    SimilarSolution,
    SourceItem,
)
from app.services.llm_provider import LLMResult, ProviderChain
from app.services.market_needs_scoring import (
    calculate_confidence_score,
    calculate_demand_score,
    clamp_score,
    confidence_label,
    demand_label,
)


class MarketNeedsAgent:
    """
    Current market validation, grounded in live research.

    Important:
    - The current score is a deterministic evidence score.
    - Claims are backed by real provider sources, never invented.
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
        """
        Async public entry point (unchanged contract) — delegates to the
        synchronous core via a worker thread, so the existing async FastAPI
        router caller is unaffected. ReviewPipeline is a plain synchronous
        component and cannot await this directly, so _analyze_sync is also
        exposed as a plain synchronous method for that caller.
        """
        return await asyncio.to_thread(self._analyze_sync, request)

    def _analyze_sync(
        self,
        request: MarketNeedsRequest,
    ) -> MarketNeedsResponse:
        prompt = self._build_prompt(request)

        result = self.chain.generate_json(
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

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(
        self,
        request: MarketNeedsRequest,
    ) -> MarketNeedsResponse:
        """
        Public entry point for the deterministic fallback response -- the
        same low-confidence template _analyze_sync already returns
        internally when every provider fails, exposed publicly so routers
        never reach into a private method (matches
        ProjectRoadmapAgent.build_safe_fallback).
        """
        return self._fallback(
            request,
            "Handled by the review pipeline's safe-fallback path.",
        )

    def generate_candidate_from_result(
        self,
        result: MarketNeedsResponse,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Takes an ALREADY-
        COMPUTED result from _analyze_sync() (the router runs it once, up
        front, so it can also read the real matched sources before building
        the ReviewContext's allowed_source_metadata for the
        source_metadata_only URL policy -- see market_needs_router.py) and
        wraps it as an LLMResult so it can flow through guarded_call like
        any other LLM stage, without re-running the live research.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- whenever
        _analyze_sync() had to fall back internally (source == "fallback"),
        since in that case there is no real candidate to review; the router
        should use build_safe_fallback() directly instead.
        """
        if result.source == "fallback":
            return None

        return LLMResult(
            ok=True,
            provider=result.provider,
            model=result.model_used,
            text="",
            data=result.model_dump(),
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

    def _build_prompt(self, request: MarketNeedsRequest) -> str:
        return f"""
You are the Market Demand Intelligence Agent for FYPilot.

Analyze whether this final-year software project solves a real, current
problem worth building for.

PROJECT
Title: {request.project_title}
Problem: {request.problem_statement}
Target users: {request.target_users}
Domain: {request.domain}
Technologies: {request.technologies}
Market scope: {request.country_context}

RESEARCH RULES
- Use live web research before answering.
- Prefer official institutions, government, universities, recognized research,
  job-market reports, industry reports, and credible organizations.
- Use Lebanon evidence first; when unavailable, use MENA or global evidence
  and state that limitation.
- Never invent a URL, statistic, publication, or market size.
- Scores are evidence indices from 0 to 100, not money, revenue,
  total-addressable market, or Google Trends values.
- Do not output a sources array. Real source URLs are read from provider tool
  metadata, not trusted from generated JSON.

CURRENT SCORE CATEGORIES
- problemEvidence: strength that the problem exists now.
- marketFit: fit with the requested geographic scope and target users.
- universityValue: academic, research, operational, or partnership value.
- competitionOpportunity: remaining opportunity after competitors.
- technologyMomentum: present adoption and technical relevance.

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
  "similarSolutions": [
    {{
      "name": "",
      "description": "",
      "similarity": "low|medium|high"
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
    def _similarity(value: object) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"low", "medium", "high"} else "medium"
