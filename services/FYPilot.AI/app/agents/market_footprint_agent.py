from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from app.models.market_footprint_models import (
    FootprintSourceItem,
    MarketFootprintRequest,
    MarketFootprintResponse,
    RegionResult,
    RegionScoreBreakdown,
)
from app.services.llm_provider import LLMResult, ProviderChain
from app.services.market_footprint_scoring import (
    calculate_evidence_strength,
    calculate_overall_confidence_score,
    calculate_overall_opportunity_score,
    calculate_region_confidence_score,
    calculate_region_opportunity_score,
    clamp_score,
    competition_pressure_label,
    demand_label,
)

# Fixed display order required by the .NET UI (Lebanon, MENA, Global).
_REGION_ORDER: list[tuple[str, str]] = [
    ("lebanon", "Lebanon"),
    ("mena", "MENA"),
    ("global", "Global"),
]

logger = logging.getLogger(__name__)


class MarketFootprintAgent:
    """
    Regional Demand Footprint — a compact, evidence-based opportunity
    comparison across Lebanon, MENA, and Global for the Idea Generator.

    Design (mirrors MarketNeedsAgent's safety rules):
    - Step 1 uses Groq Compound Mini for a small dedicated web search.
    - Step 2 uses Groq llama-3.3-70b-versatile first to produce strict JSON.
    - Real source URLs are read only from ProviderChain tool metadata,
      never trusted from model-generated JSON.
    - The seventh dimension (evidenceStrength) and every region's
      confidence score are computed here from actually-matched sources,
      not asserted by the LLM.
    - The final weighted opportunity/confidence scores are always
      calculated by market_footprint_scoring, never by the LLM directly.
    - If every provider fails, no region ever receives an invented score.
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
        request: MarketFootprintRequest,
    ) -> MarketFootprintResponse:
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
        request: MarketFootprintRequest,
    ) -> MarketFootprintResponse:
        """
        Two-step Groq-first flow.

        1. ProviderChain.search_web() uses Groq Compound Mini to collect
           real source metadata.
        2. ProviderChain.generate_json(..., use_search=False) uses normal
           Groq generation first, with Gemini and Ollama kept only as
           fallbacks.

        Splitting search from generation avoids asking Compound Mini to
        perform web research and produce a large strict JSON response in
        the same request.
        """
        search_result: Any | None = None
        raw_sources: list[Any] = []

        if request.use_search:
            search_query = self._build_search_query(request)

            try:
                search_result = self.chain.search_web(search_query)
            except Exception as exception:
                logger.exception(
                    "Market footprint Groq web search crashed: %s",
                    exception,
                )

            if (
                search_result is None
                or not getattr(search_result, "ok", False)
                or not getattr(search_result, "search_used", False)
                or not getattr(search_result, "sources", None)
            ):
                logger.warning(
                    "Market footprint search returned no verifiable evidence. "
                    "Provider=%s Model=%s Error=%s",
                    getattr(search_result, "provider", "none"),
                    getattr(search_result, "model", None),
                    getattr(search_result, "error", "No search result"),
                )

                return self._insufficient_evidence(
                    provider=str(
                        getattr(search_result, "provider", "none") or "none"
                    ),
                    model=(
                        str(getattr(search_result, "model", "") or "")
                        or None
                    ),
                    status="insufficient_evidence",
                )

            raw_sources = list(getattr(search_result, "sources", []) or [])

        normalized_sources = self._normalize_sources(
            raw_sources,
            maximum=20,
        )

        if request.use_search and not normalized_sources:
            logger.warning(
                "Market footprint search sources could not be normalized."
            )
            return self._insufficient_evidence(
                provider=str(
                    getattr(search_result, "provider", "none") or "none"
                ),
                model=(
                    str(getattr(search_result, "model", "") or "")
                    or None
                ),
                status="insufficient_evidence",
            )

        evidence_context = self._format_sources_for_prompt(
            normalized_sources
        )
        prompt = self._build_prompt(
            request,
            evidence_context=evidence_context,
        )

        result = self.chain.generate_json(
            prompt,
            use_search=False,
        )

        if not getattr(result, "ok", False) or not getattr(result, "data", None):
            logger.warning(
                "Market footprint structured generation failed. "
                "Provider=%s Model=%s Error=%s",
                getattr(result, "provider", "none"),
                getattr(result, "model", None),
                getattr(result, "error", None),
            )

            return self._insufficient_evidence(
                provider=str(getattr(result, "provider", "none") or "none"),
                model=(str(getattr(result, "model", "") or "") or None),
                status="provider_unavailable",
            )

        logger.info(
            "Market footprint completed. SearchProvider=%s "
            "SearchModel=%s AnalysisProvider=%s AnalysisModel=%s "
            "Sources=%s",
            getattr(search_result, "provider", "none"),
            getattr(search_result, "model", None),
            getattr(result, "provider", "unknown"),
            getattr(result, "model", None),
            len(normalized_sources),
        )

        return self._create_response(
            request=request,
            data=result.data,
            provider=str(getattr(result, "provider", "unknown") or "unknown"),
            model=(str(getattr(result, "model", "") or "") or None),
            source_result=search_result,
            normalized_sources=normalized_sources,
        )

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(
        self,
        request: MarketFootprintRequest,
    ) -> MarketFootprintResponse:
        """
        Public entry point for the deterministic fallback response -- the
        same "insufficient evidence" template _analyze_sync already returns
        internally when no verifiable evidence is available. Exposed
        publicly so routers never reach into a private method (matches
        ProjectRoadmapAgent.build_safe_fallback).
        """
        return self._insufficient_evidence(
            provider="none", model=None, status="insufficient_evidence",
        )

    def generate_candidate_from_result(
        self,
        result: MarketFootprintResponse,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Takes an ALREADY-
        COMPUTED result from _analyze_sync() (the router runs it once,
        up front, so it can also read the real matched sources before
        building the ReviewContext's allowed_source_metadata for the
        source_metadata_only URL policy -- see market_footprint.py) and
        wraps it as an LLMResult so it can flow through guarded_call like
        any other LLM stage, without re-running the live search/generation.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- whenever
        status != "ready" (insufficient_evidence or provider_unavailable),
        since in that case there is no real candidate to review; the router
        should use build_safe_fallback() directly instead.
        """
        if result.status != "ready":
            return None

        return LLMResult(
            ok=True,
            provider=result.provider,
            model=result.model_used,
            text="",
            data=result.model_dump(),
        )

    # ── Response assembly ────────────────────────────────────────────────────

    def _create_response(
        self,
        *,
        request: MarketFootprintRequest,
        data: dict[str, Any],
        provider: str,
        model: str | None,
        source_result: Any | None,
        normalized_sources: list[FootprintSourceItem],
    ) -> MarketFootprintResponse:
        search_used = bool(
            request.use_search
            and source_result is not None
            and getattr(source_result, "search_used", False)
        )

        all_sources = normalized_sources
        grounded = search_used and bool(all_sources)

        raw_regions = self._dict(data.get("regions"))

        if not any(self._dict(raw_regions.get(key)) for key, _ in _REGION_ORDER):
            # The provider responded, but gave no usable per-region ratings at
            # all — a distinct, honest "insufficient evidence" state, not a
            # hard provider failure and not a fabricated score.
            return self._insufficient_evidence(
                provider=provider,
                model=model,
                status="insufficient_evidence",
            )

        region_results: list[RegionResult] = []
        region_opportunity: dict[str, int | None] = {}
        region_confidence: dict[str, int] = {}
        region_source_regions: dict[str, set[str]] = {
            source.url: set() for source in all_sources
        }

        for region_key, region_name in _REGION_ORDER:
            raw_region = self._dict(raw_regions.get(region_key))

            requested_titles = self._string_list(
                raw_region.get("sourceTitles"), maximum=6
            )
            matched_sources = self._match_region_sources(
                region_name=region_name,
                requested_titles=requested_titles,
                sources=all_sources,
            )

            for source in matched_sources:
                region_source_regions.setdefault(source.url, set()).add(region_name)

            verified_count = sum(1 for s in matched_sources if s.is_verified)
            unique_domains = {
                self._domain(s.url) for s in matched_sources if self._domain(s.url)
            }
            has_region_evidence = any(
                region_name.lower() in f"{s.title} {s.relevance}".lower()
                for s in matched_sources
            )

            evidence_strength = calculate_evidence_strength(
                matched_source_count=len(matched_sources),
                verified_source_count=verified_count,
            )

            breakdown = RegionScoreBreakdown(
                problemUrgency=clamp_score(raw_region.get("problemUrgency"), 50),
                geographicFit=clamp_score(raw_region.get("geographicFit"), 50),
                adoptionReadiness=clamp_score(
                    raw_region.get("adoptionReadiness"), 50
                ),
                competitionGap=clamp_score(raw_region.get("competitionGap"), 50),
                targetUserReachability=clamp_score(
                    raw_region.get("targetUserReachability"), 50
                ),
                technologyMomentum=clamp_score(
                    raw_region.get("technologyMomentum"), 50
                ),
                evidenceStrength=evidence_strength,
            )

            # Always computed from the structured breakdown (six LLM-rated
            # dimensions + the source-derived evidenceStrength dimension) —
            # never gated on live search succeeding, matching the same
            # philosophy MarketNeedsAgent uses for its current-demand score.
            # groundedInLiveData is tracked separately as an honest signal.
            opportunity_score = calculate_region_opportunity_score(breakdown)
            confidence_score = calculate_region_confidence_score(
                grounded_in_live_data=grounded,
                matched_source_count=len(matched_sources),
                verified_source_count=verified_count,
                unique_domain_count=len(unique_domains),
                has_region_specific_evidence=has_region_evidence,
            )

            region_opportunity[region_key] = opportunity_score
            region_confidence[region_key] = confidence_score

            region_results.append(
                RegionResult(
                    regionKey=region_key,
                    regionName=region_name,
                    opportunityScore=opportunity_score,
                    confidenceScore=confidence_score,
                    demandLevel=(
                        demand_label(opportunity_score)
                        if opportunity_score is not None
                        else "Unavailable"
                    ),
                    competitionPressure=competition_pressure_label(
                        breakdown.competition_gap
                    ),
                    evidenceSummary=self._text(
                        raw_region.get("evidenceSummary"), maximum=600
                    ),
                    scoreBreakdown=breakdown,
                    sourceTitles=[s.title for s in matched_sources[:4]],
                    sourceUrls=[s.url for s in matched_sources[:4]],
                )
            )

        for source in all_sources:
            source.regions = sorted(region_source_regions.get(source.url, set()))

        overall_opportunity = calculate_overall_opportunity_score(region_opportunity)
        overall_confidence = calculate_overall_confidence_score(region_confidence)

        scored_regions = [
            (key, name, region_opportunity[key])
            for key, name in _REGION_ORDER
            if region_opportunity.get(key) is not None
        ]

        best_region = (
            max(scored_regions, key=lambda item: item[2]) if scored_regions else None
        )

        expansion_path = [
            name
            for _, name, _ in sorted(
                scored_regions,
                key=lambda item: item[2],
                reverse=True,
            )
        ]

        best_region_result = next(
            (r for r in region_results if best_region and r.region_key == best_region[0]),
            None,
        )

        return MarketFootprintResponse(
            status="ready",
            provider=provider,
            modelUsed=model,
            groundedInLiveData=grounded,
            overallOpportunityScore=overall_opportunity,
            overallConfidenceScore=overall_confidence,
            overallDemandLevel=(
                demand_label(overall_opportunity)
                if overall_opportunity is not None
                else "Unavailable"
            ),
            bestLaunchMarket=best_region[1] if best_region else "",
            bestLaunchReason=(
                best_region_result.evidence_summary
                if best_region_result and best_region_result.evidence_summary
                else self._text(data.get("bestLaunchReason"), maximum=600)
            ),
            expansionPath=expansion_path,
            whyDemanded=self._string_list(data.get("whyDemanded"), maximum=3),
            strategicRecommendation=self._text(
                data.get("strategicRecommendation"), maximum=1200
            ),
            limitations=self._string_list(data.get("limitations"), maximum=4),
            regions=region_results,
            sources=all_sources,
            analyzedAt=datetime.now(timezone.utc),
        )

    def _insufficient_evidence(
        self,
        *,
        provider: str,
        model: str | None,
        status: str,
    ) -> MarketFootprintResponse:
        empty_regions = [
            RegionResult(
                regionKey=key,
                regionName=name,
                opportunityScore=None,
                confidenceScore=0,
                demandLevel="Unavailable",
                competitionPressure="medium",
                evidenceSummary="",
                scoreBreakdown=None,
                sourceTitles=[],
                sourceUrls=[],
            )
            for key, name in _REGION_ORDER
        ]

        return MarketFootprintResponse(
            status=status,
            provider=provider,
            modelUsed=model,
            groundedInLiveData=False,
            overallOpportunityScore=None,
            overallConfidenceScore=0,
            overallDemandLevel="Unavailable",
            bestLaunchMarket="",
            bestLaunchReason="",
            expansionPath=[],
            whyDemanded=[],
            strategicRecommendation="",
            limitations=[
                "Regional evidence could not be verified right now. "
                "No provider returned usable grounded research.",
            ],
            regions=empty_regions,
            sources=[],
            analyzedAt=datetime.now(timezone.utc),
        )

    # ── Prompt ────────────────────────────────────────────────────────────────

    def _build_search_query(
        self,
        request: MarketFootprintRequest,
    ) -> str:
        return f"""
Research current, verifiable market evidence for this software project.

PROJECT
Title: {request.project_title}
Problem: {request.problem_statement}
Target users: {request.target_users}
Domain: {request.domain}
Technologies: {request.technologies}

Find concise evidence for exactly these geographic scopes:
1. Lebanon
2. MENA (Middle East and North Africa)
3. Global

Focus only on:
- real problem urgency
- regional relevance and geographic fit
- adoption readiness
- existing alternatives and competition
- target-user reachability
- technology or policy momentum

Prefer official institutions, government sources, universities, academic
research, international organizations, and recognized industry reports.

For Lebanon, prioritize Lebanon-specific evidence.
For MENA, prioritize regional evidence that explicitly covers MENA.
For Global, prioritize international or worldwide evidence.

Return a concise research summary with the real sources used.
Do not invent URLs, statistics, market size, revenue, CAGR, or user counts.
"""

    def _build_prompt(
        self,
        request: MarketFootprintRequest,
        *,
        evidence_context: str,
    ) -> str:
        return f"""
You are the Regional Market Footprint Agent for FYPilot.

Compare the opportunity for this final-year software project across exactly
three regions: Lebanon, MENA, and Global.

PROJECT
Title: {request.project_title}
Problem: {request.problem_statement}
Target users: {request.target_users}
Domain: {request.domain}
Technologies: {request.technologies}

VERIFIED SEARCH EVIDENCE
{evidence_context}

ANALYSIS RULES
- Use only the evidence supplied above.
- Do not browse again.
- Do not invent a source, URL, percentage, market size, revenue, CAGR,
  employment count, adoption rate, or user count.
- The six regional ratings below are normalized opportunity inputs.
- They are not market share, expected revenue, population percentage, or
  probability of commercial success.
- Python calculates the final weighted opportunity percentages.
- Copy sourceTitles exactly from the VERIFIED SEARCH EVIDENCE.
- When evidence for a region is weak, lower the relevant scores and state
  the limitation.
- Keep summaries concise and useful for a student and supervisor.

FOR EACH REGION, rate from 0 to 100:
- problemUrgency
- geographicFit
- adoptionReadiness
- competitionGap
- targetUserReachability
- technologyMomentum

Return ONLY valid JSON in exactly this structure:
{{
  "regions": {{
    "lebanon": {{
      "problemUrgency": 0,
      "geographicFit": 0,
      "adoptionReadiness": 0,
      "competitionGap": 0,
      "targetUserReachability": 0,
      "technologyMomentum": 0,
      "evidenceSummary": "one concise evidence-based sentence",
      "sourceTitles": ["exact source title"]
    }},
    "mena": {{
      "problemUrgency": 0,
      "geographicFit": 0,
      "adoptionReadiness": 0,
      "competitionGap": 0,
      "targetUserReachability": 0,
      "technologyMomentum": 0,
      "evidenceSummary": "one concise evidence-based sentence",
      "sourceTitles": ["exact source title"]
    }},
    "global": {{
      "problemUrgency": 0,
      "geographicFit": 0,
      "adoptionReadiness": 0,
      "competitionGap": 0,
      "targetUserReachability": 0,
      "technologyMomentum": 0,
      "evidenceSummary": "one concise evidence-based sentence",
      "sourceTitles": ["exact source title"]
    }}
  }},
  "whyDemanded": [
    "evidence-based reason one",
    "evidence-based reason two",
    "evidence-based reason three"
  ],
  "strategicRecommendation": "one practical paragraph covering where to start, who to validate with, which advantage to emphasize, and what must be proven before expansion",
  "limitations": ["short evidence limitation when applicable"]
}}
"""

    @staticmethod
    def _format_sources_for_prompt(
        sources: list[FootprintSourceItem],
    ) -> str:
        if not sources:
            return "No verified web sources were returned."

        blocks: list[str] = []

        for index, source in enumerate(sources[:12], start=1):
            blocks.append(
                "\n".join(
                    [
                        f"Source {index}",
                        f"Title: {source.title}",
                        f"URL: {source.url}",
                        f"Publisher: {source.publisher}",
                        f"Evidence: {source.relevance}",
                    ]
                )
            )

        return "\n\n".join(blocks)

    # ── Region source matching (mirrors MarketNeedsAgent) ────────────────────

    def _match_region_sources(
        self,
        *,
        region_name: str,
        requested_titles: list[str],
        sources: list[FootprintSourceItem],
    ) -> list[FootprintSourceItem]:
        ranked: list[tuple[float, FootprintSourceItem]] = []

        for source in sources:
            score = 0.0
            source_title = self._normalize_text(source.title)

            if region_name.lower() in f"{source.title} {source.relevance}".lower():
                score += 0.35

            for requested in requested_titles:
                requested_title = self._normalize_text(requested)
                if not requested_title or not source_title:
                    continue

                similarity = SequenceMatcher(
                    None, requested_title, source_title
                ).ratio()

                if requested_title in source_title or source_title in requested_title:
                    similarity = max(similarity, 0.92)

                score = max(score, similarity)

            if score >= 0.55:
                ranked.append((score, source))

        ranked.sort(
            key=lambda item: (item[0], item[1].is_verified, item[1].relevance_score),
            reverse=True,
        )

        return [source for _, source in ranked[:4]]

    # ── Source extraction / normalization (never trust LLM-generated URLs) ──

    def _normalize_sources(
        self,
        raw_sources: list[Any],
        *,
        maximum: int,
    ) -> list[FootprintSourceItem]:
        results: list[FootprintSourceItem] = []
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
            publisher = self._text(item.get("publisher"), default=domain, maximum=250)
            snippet = self._text(
                item.get("snippet") or item.get("relevance"), maximum=800
            )

            results.append(
                FootprintSourceItem(
                    title=self._text(
                        item.get("title"), default=publisher or domain, maximum=300
                    ),
                    url=url,
                    publisher=publisher,
                    relevance=snippet,
                    relevanceScore=clamp_score(item.get("relevanceScore", 65)),
                    isVerified=self._is_recognized_domain(domain),
                )
            )

        return sorted(
            results,
            key=lambda source: (source.is_verified, source.relevance_score),
            reverse=True,
        )[:maximum]

    def _is_recognized_domain(self, domain: str) -> bool:
        return any(
            domain == known or domain.endswith(f".{known}")
            for known in self._recognized_domains
        )

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
            self._text(item, maximum=400) for item in self._list(value) if self._text(item)
        ][:maximum]

    @staticmethod
    def _text(value: object, default: str = "", maximum: int = 2000) -> str:
        text = str(value or default).strip()
        return text[:maximum]

    @staticmethod
    def _normalize_text(value: object) -> str:
        text = str(value or "").lower()
        return re.sub(r"[^a-z0-9]+", " ", text).strip()