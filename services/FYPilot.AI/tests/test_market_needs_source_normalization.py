"""
Integration-level tests for MarketNeedsAgent._normalize_sources -- confirms
the wiring into app/agents/market_needs_evidence.py actually replaced the
old hardcoded relevanceScore=65 default and the old 4-bucket source_type/
19-domain allowlist classifier, using the REAL agent method (not just the
underlying evidence module in isolation -- see test_market_needs_evidence.py
for that).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.market_needs_agent import MarketNeedsAgent  # noqa: E402
from app.models.market_needs_models import MarketNeedsRequest  # noqa: E402


def _request() -> MarketNeedsRequest:
    return MarketNeedsRequest(
        projectTitle="Arabic Medical Symptom Triage Assistant",
        problemStatement=(
            "Patients in Lebanon struggle to know whether their symptoms "
            "require urgent care, a regular doctor visit, or self-care."
        ),
        targetUsers="Arabic-speaking patients and primary care clinics in Lebanon",
        domain="Digital Health / Clinical Decision Support",
        technologies="Python FastAPI, PostgreSQL, NLP symptom classifier",
        countryContext="Lebanon",
        useSearch=True,
    )


class NormalizeSourcesRelevanceScoreTests(unittest.TestCase):
    def test_real_sources_no_longer_get_the_hardcoded_65_constant(self):
        """
        The exact live-confirmed defect: raw Brave/Groq source dicts never
        carry a "relevanceScore" key, so every real source previously
        scored exactly 65 regardless of content.
        """
        agent = MarketNeedsAgent()
        raw_sources = [
            {
                "title": "AI Symptom Checker Market Size, Share & Trends",
                "url": "https://www.grandviewresearch.com/industry-analysis/ai-symptom-checker-market",
                "snippet": "digital health symptom checker adoption market size trends 2033",
            },
            {
                "title": "Best pizza recipes for 2026",
                "url": "https://randomblog.example.com/pizza",
                "snippet": "toppings and dough tips",
            },
        ]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)

        self.assertEqual(len(sources), 2)
        scores = {source.relevance_score for source in sources}
        self.assertGreater(len(scores), 1, "Both sources scored identically -- the hardcoded-65 defect regressed.")
        self.assertNotIn(65, [s.relevance_score for s in sources if True])

    def test_explicit_relevance_score_from_provider_metadata_is_still_honored(self):
        """
        If a provider ever DOES supply a real relevanceScore, it must still
        be used verbatim (clamped) rather than overridden by the computed
        fallback -- this is deliberately an opt-in override, not a removal
        of provider-supplied signal.
        """
        agent = MarketNeedsAgent()
        raw_sources = [{
            "title": "Some report", "url": "https://example.com/report",
            "snippet": "x", "relevanceScore": 91,
        }]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)
        self.assertEqual(sources[0].relevance_score, 91)


class NormalizeSourcesTypeClassificationTests(unittest.TestCase):
    def test_peer_reviewed_and_market_research_sources_are_marked_verified(self):
        agent = MarketNeedsAgent()
        raw_sources = [
            {"title": "A study", "url": "https://arxiv.org/abs/1234", "snippet": "symptom triage"},
            {"title": "Report", "url": "https://www.grandviewresearch.com/x", "snippet": "market size"},
        ]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)

        by_domain = {s.url: s for s in sources}
        self.assertTrue(by_domain["https://arxiv.org/abs/1234"].is_verified)
        self.assertTrue(by_domain["https://www.grandviewresearch.com/x"].is_verified)
        self.assertEqual(by_domain["https://arxiv.org/abs/1234"].source_type, "Peer-reviewed research")
        self.assertEqual(by_domain["https://www.grandviewresearch.com/x"].source_type, "Market research firm")

    def test_commercial_marketplace_source_is_never_verified(self):
        agent = MarketNeedsAgent()
        raw_sources = [{
            "title": "Free FYP source code", "url": "https://kashipara.com/x",
            "snippet": "download project with source code",
        }]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)
        self.assertFalse(sources[0].is_verified)
        self.assertEqual(sources[0].source_type, "Commercial marketplace")

    def test_dedupe_and_url_validation_still_work(self):
        agent = MarketNeedsAgent()
        raw_sources = [
            {"title": "A", "url": "https://example.com/a", "snippet": ""},
            {"title": "A again", "url": "https://example.com/a", "snippet": ""},
            {"title": "Bad", "url": "javascript:alert(1)", "snippet": ""},
            {"title": "No URL"},
        ]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
