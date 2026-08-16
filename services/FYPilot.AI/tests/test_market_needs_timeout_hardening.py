"""
Regression tests for the "standard"-tier hidden-retry fix.

Root cause (same class of bug as test_mentor_timeout_hardening.py's "mentor"
tier fix, confirmed by code inspection against the live-verified mentor bug,
not by reproducing a live hang): "standard" -- the DEFAULT ProviderChain
tier, and the tier MarketNeedsAgent's own ProviderChain() and
ReviewPipeline("MarketNeedsAgent")'s Reviewer/Rewrite ProviderChain all use,
since neither passes an explicit tier -- had no entry in
_DEEPINFRA_TIER_TIMING / _GROQ_TIER_TIMING, so DeepInfraProvider/
GroqProvider(max_retries=None, ...) let the underlying SDK's own default
retry count (2) apply for every one of Market Demand's three LLM stages
(Writer/Reviewer/Rewrite), on top of ProviderChain's own explicit
provider-to-provider cascade -- the exact mechanism that caused a single
Writer call to silently take ~69s against a 65s budget for Mentor Chat
before that fix.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.services import llm_provider as llm_provider_module  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    DeepInfraProvider,
    GroqProvider,
    ProviderChain,
)


class StandardTierRetryConfigTests(unittest.TestCase):
    def test_standard_tier_has_deepinfra_timing_entry(self):
        self.assertIn("standard", llm_provider_module._DEEPINFRA_TIER_TIMING)

    def test_standard_tier_disables_deepinfra_hidden_retries(self):
        timing = llm_provider_module._deepinfra_timing_for_tier("standard")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_standard_tier_has_groq_timing_entry(self):
        self.assertIn("standard", llm_provider_module._GROQ_TIER_TIMING)

    def test_standard_tier_disables_groq_hidden_retries(self):
        timing = llm_provider_module._groq_timing_for_tier("standard")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_standard_tier_does_not_change_configured_timeout(self):
        """Only the hidden retry multiplier is removed -- the per-attempt
        timeout itself must remain each provider's own configured default
        (None here means "use DeepInfraProvider's/GroqProvider's own
        DEEPINFRA_TIMEOUT_SECONDS/GROQ_TIMEOUT_SECONDS default")."""
        self.assertIsNone(
            llm_provider_module._deepinfra_timing_for_tier("standard").get("timeout_seconds"),
        )
        self.assertIsNone(
            llm_provider_module._groq_timing_for_tier("standard").get("timeout_seconds"),
        )

    def test_default_provider_chain_tier_is_standard_and_gets_the_fix(self):
        """ProviderChain(tier: str = "standard") -- MarketNeedsAgent's own
        `self.chain = ProviderChain()` and ReviewPipeline's default
        `tier: str = "standard"` both rely on this default applying the
        same fix without needing to pass tier="standard" explicitly."""
        chain = ProviderChain()
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))
        groq = next(p for p in chain.providers if isinstance(p, GroqProvider))
        self.assertEqual(deepinfra.max_retries, 0)
        self.assertEqual(groq.max_retries, 0)

    def test_explicit_standard_tier_constructs_deepinfra_with_max_retries_zero(self):
        chain = ProviderChain(tier="standard")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))
        self.assertEqual(deepinfra.max_retries, 0)

    def test_explicit_standard_tier_constructs_groq_with_max_retries_zero(self):
        chain = ProviderChain(tier="standard")
        groq = next(p for p in chain.providers if isinstance(p, GroqProvider))
        self.assertEqual(groq.max_retries, 0)

    def test_standard_tier_client_omits_sdk_default_retry(self):
        """The actual behavior the fix guarantees: with max_retries=0
        explicitly set (not None), the constructed SDK client must receive
        max_retries=0, never silently fall through to the SDK's own
        default of 2."""
        chain = ProviderChain(tier="standard")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))

        captured_kwargs: dict = {}

        def fake_openai(**kwargs):
            captured_kwargs.update(kwargs)
            return object()

        with patch("openai.OpenAI", side_effect=fake_openai):
            deepinfra._client()

        self.assertEqual(captured_kwargs.get("max_retries"), 0)

    def test_market_needs_agent_provider_chain_uses_the_fixed_default_tier(self):
        """MarketNeedsAgent itself, not just ProviderChain() in isolation --
        confirms the fix actually reaches the real agent's own chain."""
        from app.agents.market_needs_agent import MarketNeedsAgent

        agent = MarketNeedsAgent()
        deepinfra = next(p for p in agent.chain.providers if isinstance(p, DeepInfraProvider))
        groq = next(p for p in agent.chain.providers if isinstance(p, GroqProvider))
        self.assertEqual(deepinfra.max_retries, 0)
        self.assertEqual(groq.max_retries, 0)

    def test_review_pipeline_reviewer_rewrite_use_the_fixed_default_tier(self):
        """ReviewPipeline("MarketNeedsAgent") passes no explicit tier, so
        its Reviewer/Rewrite ProviderChain also default to "standard" and
        must get the same fix -- confirmed against the real ReviewPipeline
        construction, not a synthetic tier string."""
        from app.review.pipeline import ReviewPipeline

        pipeline = ReviewPipeline("MarketNeedsAgent")

        reviewer_chain = pipeline.reviewer_agent.provider_chain
        rewrite_chain = pipeline.rewrite_agent.provider_chain

        for chain in (reviewer_chain, rewrite_chain):
            deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))
            groq = next(p for p in chain.providers if isinstance(p, GroqProvider))
            self.assertEqual(deepinfra.max_retries, 0)
            self.assertEqual(groq.max_retries, 0)


if __name__ == "__main__":
    unittest.main()
