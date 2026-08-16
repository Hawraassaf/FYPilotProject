"""
Regression tests for the firewall_blocked / url_not_in_source_metadata bug
reported live 2026-08-13: the entire Market Demand analysis was discarded
(status="firewall_blocked", groundedInLiveData=False, sources=[]) the
instant ANY URL-shaped substring appeared anywhere in the candidate that
wasn't an exact match to a verified source's own url -- registry.py's
url_mode="source_metadata_only" + allow_unreviewed_output=False for
MarketNeedsAgent means there is no unreviewed-fallback escape hatch here,
unlike Mentor Chat.

Two distinct, real carriers of a stray URL are covered:
1. LLM-authored narrative fields (problemEvidence, similarSolutions,
   lebaneseMarketFit, universityValue, risks, recommendation, nextSteps,
   targetSector) -- mitigated by prompt hardening AND guaranteed by
   deterministic stripping (_narrative_text/_narrative_string_list).
2. SourceItem title/publisher/relevance -- REAL, uncontrolled web content
   from Brave/Groq search results (never LLM-generated), which routinely
   embeds a SECOND unrelated URL that was never in allowed_source_metadata
   (only each source's own canonical url is there). This is the more
   likely, fully deterministic root cause of a CONSISTENTLY reproducible
   failure for a given project/search-result combination.

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
from app.llm_firewall.firewall import LlmFirewall  # noqa: E402
from app.models.market_needs_models import MarketNeedsRequest  # noqa: E402


def _request() -> MarketNeedsRequest:
    return MarketNeedsRequest(
        projectTitle="Arabic Medical Symptom Triage Assistant",
        problemStatement="Patients in Lebanon struggle to know whether their symptoms require urgent care.",
        targetUsers="Arabic-speaking patients in Lebanon",
        domain="Digital Health",
        technologies="Python FastAPI, PostgreSQL",
        countryContext="Lebanon",
        useSearch=True,
    )


class StripUrlsHelperTests(unittest.TestCase):
    def test_strips_a_bare_url(self):
        agent = MarketNeedsAgent()
        self.assertEqual(
            agent._strip_urls("See more at https://example.com/page for details."),
            "See more at for details.",
        )

    def test_strips_a_parenthetical_url_and_removes_empty_parens(self):
        agent = MarketNeedsAgent()
        self.assertEqual(
            agent._strip_urls("Infermedica (https://infermedica.com) is a competitor."),
            "Infermedica is a competitor.",
        )

    def test_leaves_url_free_text_unchanged(self):
        agent = MarketNeedsAgent()
        self.assertEqual(agent._strip_urls("Just plain text."), "Just plain text.")

    def test_empty_string_returns_empty_string(self):
        agent = MarketNeedsAgent()
        self.assertEqual(agent._strip_urls(""), "")


class NarrativeFieldUrlStrippingTests(unittest.TestCase):
    """
    Carrier 1: an LLM-authored narrative field containing a URL must never
    reach the final candidate -- proven via _create_response directly
    (no network call), simulating exactly what a non-compliant Writer
    response would produce despite the hardened prompt.
    """

    def test_narrative_fields_never_contain_a_url_even_if_the_model_wrote_one(self):
        agent = MarketNeedsAgent()
        raw_llm_data = {
            "scoreBreakdown": {
                "problemEvidence": 70, "marketFit": 70, "universityValue": 70,
                "competitionOpportunity": 70, "technologyMomentum": 70,
            },
            "targetSector": "Digital Health (see https://sector.example.com)",
            "problemEvidence": ["Patients struggle -- details at https://evidence.example.com"],
            "similarSolutions": [{
                "name": "Infermedica",
                "description": "A competitor. Visit https://infermedica.com for more.",
                "similarity": "high",
            }],
            "lebaneseMarketFit": "Aligns with local needs, see https://fit.example.com.",
            "universityValue": "Strong academic value (https://value.example.com).",
            "risks": ["Competition risk -- https://risk.example.com"],
            "recommendation": "Proceed. Reference: https://rec.example.com.",
            "nextSteps": ["Validate demand -- https://nextstep.example.com"],
        }

        response = agent._create_response(
            request=_request(), data=raw_llm_data, provider="deepinfra", model="m",
            error=None, search_used=True, search_provider="brave", sources=[],
        )

        candidate = response.model_dump()
        firewall = LlmFirewall()
        verdict = firewall.inspect_output(candidate, {}, url_mode="source_metadata_only", allowed_sources=[])

        self.assertFalse(
            verdict.has_blocking_finding(),
            f"A narrative-field URL still reached the candidate and would discard the whole analysis: {verdict.findings}",
        )

        # Explicit per-field checks, not just the aggregate firewall pass.
        self.assertNotIn("https://", response.target_sector)
        self.assertNotIn("https://", response.problem_evidence[0])
        self.assertNotIn("https://", response.similar_solutions[0].description)
        self.assertNotIn("https://", response.lebanese_market_fit)
        self.assertNotIn("https://", response.university_value)
        self.assertNotIn("https://", response.risks[0])
        self.assertNotIn("https://", response.recommendation)
        self.assertNotIn("https://", response.next_steps[0])


class SourceSnippetUrlStrippingTests(unittest.TestCase):
    """
    Carrier 2 -- the more likely, fully deterministic root cause: a source's
    OWN raw snippet (real, uncontrolled web content, never LLM-generated)
    embeds a second URL that was never in allowed_source_metadata.
    """

    def test_source_snippet_with_an_embedded_secondary_url_no_longer_blocks(self):
        agent = MarketNeedsAgent()
        raw_sources = [{
            "title": "AI Symptom Checker Market Size",
            "url": "https://www.grandviewresearch.com/industry-analysis/ai-symptom-checker-market",
            "snippet": (
                "The market is projected to grow steadily. Read the full "
                "methodology at https://www.grandviewresearch.com/methodology "
                "for details."
            ),
        }]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)

        self.assertEqual(len(sources), 1)
        # The source's OWN url must survive untouched.
        self.assertEqual(
            sources[0].url,
            "https://www.grandviewresearch.com/industry-analysis/ai-symptom-checker-market",
        )
        # The EMBEDDED secondary url (a different page on the same domain)
        # must be stripped from the stored snippet.
        self.assertNotIn("https://", sources[0].relevance)

        response = agent._create_response(
            request=_request(), data={"scoreBreakdown": {
                "problemEvidence": 70, "marketFit": 70, "universityValue": 70,
                "competitionOpportunity": 70, "technologyMomentum": 70,
            }},
            provider="deepinfra", model="m", error=None,
            search_used=True, search_provider="brave", sources=sources,
        )

        candidate = response.model_dump()
        firewall = LlmFirewall()
        allowed = [s.model_dump() for s in sources]
        verdict = firewall.inspect_output(candidate, {}, url_mode="source_metadata_only", allowed_sources=allowed)

        self.assertFalse(
            verdict.has_blocking_finding(),
            f"A source-snippet-embedded URL still blocked the analysis: {verdict.findings}",
        )

    def test_source_title_with_embedded_url_is_stripped_but_url_field_untouched(self):
        agent = MarketNeedsAgent()
        raw_sources = [{
            "title": "Report (mirror at https://mirror.example.com/report)",
            "url": "https://www.reuters.com/technology/report",
            "snippet": "",
        }]

        sources = agent._normalize_sources(raw_sources, _request(), maximum=14)

        self.assertEqual(sources[0].url, "https://www.reuters.com/technology/report")
        self.assertNotIn("https://", sources[0].title)


if __name__ == "__main__":
    unittest.main()
