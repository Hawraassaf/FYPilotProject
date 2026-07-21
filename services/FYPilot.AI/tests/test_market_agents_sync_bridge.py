"""
Regression tests for the Market Footprint / Market Needs sync-to-async
bridge.

Both agents' real logic used to live directly inside an `async def
analyze()` method, calling `asyncio.to_thread(self.chain.search_web, ...)`
/ `asyncio.to_thread(self.chain.generate_json, ...)`. ReviewPipeline is a
plain synchronous component (see app/review/pipeline.py) and cannot await
anything, so a later batch needs a synchronous entry point into these
agents without duplicating their logic.

This batch extracts each agent's actual logic into a new synchronous
`_analyze_sync()` method, calling ProviderChain directly (no await, no
to_thread). The existing `async def analyze()` becomes a thin wrapper --
`await asyncio.to_thread(self._analyze_sync, request)` -- so its contract
and the existing FastAPI router callers are completely unchanged.

These tests prove:
- `_analyze_sync()` runs correctly with no event loop at all.
- `analyze()` (awaited via asyncio.run) produces the same result as calling
  `_analyze_sync()` directly, for the same stubbed provider responses --
  i.e. the refactor changed nothing about behavior.

No real network/provider calls are made; ProviderChain.search_web /
generate_json are stubbed directly on the agent instance.
"""

import asyncio
import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.market_footprint_agent import MarketFootprintAgent  # noqa: E402
from app.agents.market_needs_agent import MarketNeedsAgent  # noqa: E402
from app.models.market_footprint_models import MarketFootprintRequest  # noqa: E402
from app.models.market_needs_models import MarketNeedsRequest  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


def _search_result(sources=None):
    return LLMResult(
        ok=True, provider="groq", model="compound-mini", text="", data=None,
        search_used=True, sources=sources or [],
    )


def _json_result(data, provider="groq", model="llama-3.3-70b-versatile"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


_FOOTPRINT_SOURCES = [
    {"url": "https://worldbank.org/report", "title": "Report", "publisher": "World Bank"}
]

_FOOTPRINT_DATA = {
    "regions": {
        "lebanon": {
            "problemUrgency": 70, "geographicFit": 70, "adoptionReadiness": 60,
            "competitionGap": 60, "targetUserReachability": 65, "technologyMomentum": 60,
            "evidenceSummary": "Evidence.", "sourceTitles": ["Report"],
        },
        "mena": {}, "global": {},
    },
    "whyDemanded": ["reason"], "strategicRecommendation": "Start local.", "limitations": [],
}

_NEEDS_DATA = {
    "scoreBreakdown": {
        "problemEvidence": 70, "marketFit": 65, "universityValue": 70,
        "competitionOpportunity": 60, "technologyMomentum": 65,
    },
    "targetSector": "Education", "problemEvidence": ["evidence"],
    "yearlyEvidence": [], "similarSolutions": [], "trendSignals": [],
    "lebaneseMarketFit": "fit", "universityValue": "value",
    "risks": [], "recommendation": "go", "nextSteps": [],
}


class MarketFootprintSyncBridgeTests(unittest.TestCase):
    def _request(self):
        return MarketFootprintRequest(
            projectTitle="Test Idea",
            problemStatement="A real problem worth solving.",
            targetUsers="Students",
            domain="Education",
            technologies="ASP.NET Core",
            useSearch=True,
        )

    def _stub_chain(self, agent):
        agent.chain.search_web = lambda *_a, **_kw: _search_result(_FOOTPRINT_SOURCES)
        agent.chain.generate_json = lambda *_a, **_kw: _json_result(_FOOTPRINT_DATA)

    def test_sync_core_runs_without_an_event_loop(self):
        agent = MarketFootprintAgent()
        self._stub_chain(agent)

        result = agent._analyze_sync(self._request())

        self.assertEqual(result.status, "ready")
        self.assertTrue(any(r.region_key == "lebanon" for r in result.regions))
        self.assertIsNotNone(result.overall_opportunity_score)

    def test_async_wrapper_matches_sync_core(self):
        agent = MarketFootprintAgent()
        self._stub_chain(agent)

        sync_result = agent._analyze_sync(self._request())
        async_result = asyncio.run(agent.analyze(self._request()))

        self.assertEqual(sync_result.status, async_result.status)
        self.assertEqual(
            sync_result.overall_opportunity_score,
            async_result.overall_opportunity_score,
        )
        self.assertEqual(len(sync_result.regions), len(async_result.regions))
        self.assertEqual(sync_result.best_launch_market, async_result.best_launch_market)


class MarketNeedsSyncBridgeTests(unittest.TestCase):
    def _request(self):
        return MarketNeedsRequest(
            projectTitle="Test Idea",
            problemStatement="A real problem worth solving.",
            targetUsers="Students",
            domain="Education",
            technologies="ASP.NET Core",
            countryContext="Lebanon",
            historyYears=6,
            forecastYears=3,
            useSearch=True,
        )

    def _stub_chain(self, agent):
        agent.chain.generate_json = lambda *_a, **_kw: _json_result(_NEEDS_DATA)

    def test_sync_core_runs_without_an_event_loop(self):
        agent = MarketNeedsAgent()
        self._stub_chain(agent)

        result = agent._analyze_sync(self._request())

        self.assertEqual(result.target_sector, "Education")
        self.assertIsNotNone(result.demand_score)

    def test_async_wrapper_matches_sync_core(self):
        agent = MarketNeedsAgent()
        self._stub_chain(agent)

        sync_result = agent._analyze_sync(self._request())
        async_result = asyncio.run(agent.analyze(self._request()))

        self.assertEqual(sync_result.demand_score, async_result.demand_score)
        self.assertEqual(sync_result.target_sector, async_result.target_sector)
        self.assertEqual(sync_result.confidence_score, async_result.confidence_score)


if __name__ == "__main__":
    unittest.main()
