"""
Regression tests for Idea Generator's timeout/hidden-retry fix (Gap 2 of the
pre-freeze production-hardening pass).

Root cause this guards against: "high" -- the DeepInfra tier ProjectIdeaAgent's
Writer AND its own ReviewPipeline("ProjectIdeaAgent", tier="high") Reviewer/
Rewrite stages all share (see project_idea_agent.py / routers/ideas.py) --
was absent from llm_provider.py's _DEEPINFRA_TIER_TIMING / _GROQ_TIER_TIMING
dicts, so DeepInfraProvider/GroqProvider(max_retries=None, ...) let the
underlying SDK's own default retry count (2) apply on top of ProviderChain's
own explicit provider-to-provider cascade -- the same root cause already
fixed for "mentor" (test_mentor_timeout_hardening.py) and "standard"
(test_market_needs_timeout_hardening.py).

This tier is especially exposed: Idea Generator's Writer budget (~90s of a
120s total, see routers/ideas.py's _WRITER_TIME_RESERVE_SECONDS) is shared
across TWO sequential calls (search then generation) before this provider is
even reached, and the Reviewer/Rewrite stages afterward share the SAME
"high" tier with only the ~30s review reserve left.

These tests are deterministic -- no real sleeping, no real network calls.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.review.context import ReviewContext  # noqa: E402
from app.review.reviewer_agent import ReviewerAgent  # noqa: E402
from app.review.rewrite_agent import RewriteAgent  # noqa: E402
from app.review.models import ReviewerIssue  # noqa: E402
from app.services import llm_provider as llm_provider_module  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    DeepInfraProvider,
    GroqProvider,
    LLMResult,
    ProviderChain,
)


class HighTierRetryConfigTests(unittest.TestCase):
    """Static configuration assertions -- the actual fix."""

    def test_high_tier_has_deepinfra_timing_entry(self):
        self.assertIn("high", llm_provider_module._DEEPINFRA_TIER_TIMING)

    def test_high_tier_disables_deepinfra_hidden_retries(self):
        timing = llm_provider_module._deepinfra_timing_for_tier("high")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_high_tier_has_groq_timing_entry(self):
        self.assertIn("high", llm_provider_module._GROQ_TIER_TIMING)

    def test_high_tier_disables_groq_hidden_retries(self):
        timing = llm_provider_module._groq_timing_for_tier("high")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_high_tier_model_unaffected_by_timing_fix(self):
        """Regression guard: adding a timing entry must not change which
        model this tier resolves to (the actual generation quality)."""
        self.assertEqual(
            llm_provider_module._deepinfra_model_for_tier("high"),
            "anthropic/claude-opus-4-8",
        )

    def test_high_provider_chain_constructs_deepinfra_with_max_retries_zero(self):
        chain = ProviderChain(tier="high")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))
        self.assertEqual(deepinfra.max_retries, 0)

    def test_high_provider_chain_constructs_groq_with_max_retries_zero(self):
        chain = ProviderChain(tier="high")
        groq = next(p for p in chain.providers if isinstance(p, GroqProvider))
        self.assertEqual(groq.max_retries, 0)

    def test_high_provider_chain_client_omits_sdk_default_retry(self):
        """The actual behavior the live "mentor" bug traced back to: with
        max_retries=0 explicitly set (not None), the constructed SDK client
        must receive max_retries=0, never silently fall through to the
        SDK's own default of 2 -- now closed for "high" too."""
        chain = ProviderChain(tier="high")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))

        captured_kwargs: dict = {}

        def fake_openai(**kwargs):
            captured_kwargs.update(kwargs)
            return object()

        with patch("openai.OpenAI", side_effect=fake_openai):
            deepinfra._client()

        self.assertEqual(captured_kwargs.get("max_retries"), 0)

    def test_high_tier_deepinfra_and_groq_both_configured_for_single_attempt(self):
        chain = ProviderChain(tier="high")
        for provider in chain.providers:
            if isinstance(provider, (DeepInfraProvider, GroqProvider)):
                self.assertEqual(
                    provider.max_retries, 0,
                    f"{provider.name} must be configured for a single attempt "
                    "(max_retries=0) on the high tier -- otherwise the "
                    "underlying SDK's own default retry count silently "
                    "multiplies a single ProviderChain attempt's real duration.",
                )


class _FakeGenerateJsonProvider:
    """Stands in for a real provider inside a REAL ProviderChain -- records
    whether cap_timeout_to_deadline caused writer_budget_seconds to be
    forwarded, without any network call or sleep."""

    name = "fake"

    def __init__(self, data: dict):
        self._data = data
        self.received_writer_budget_seconds: float | None | str = "not called"

    def generate_json(self, prompt, *, use_search=False, max_tokens=None,
                       reporter=None, schema_description=None, writer_budget_seconds=None):
        self.received_writer_budget_seconds = writer_budget_seconds
        return LLMResult(ok=True, provider=self.name, model="fake-model", text="", data=self._data)


def _idea_review_context() -> ReviewContext:
    return ReviewContext(
        agent_name="ProjectIdeaAgent",
        trusted_system_instructions="",
        trusted_structural_context={},
        untrusted_project_text={},
        untrusted_user_input="",
        untrusted_conversation_history=[],
        untrusted_existing_code=None,
        untrusted_retrieved_web_content=[],
        previous_model_outputs=[],
        allowed_source_metadata=[],
    )


class DeadlinePropagationAuditTests(unittest.TestCase):
    """
    Audits that Writer, Reviewer, and Rewrite deadline propagation/capping
    already behaves correctly on the "high" tier -- the Gap 2 defect was
    only the missing max_retries=0, never a missing deadline/cap wiring.
    These tests prove the timing fix didn't accidentally change that.
    """

    def test_reviewer_forwards_writer_budget_seconds_on_high_tier(self):
        fake_provider = _FakeGenerateJsonProvider(
            {"strengths": [], "issues": [], "qualityScore": 90, "overallAssessment": "ok"},
        )
        chain = ProviderChain(providers=[fake_provider])
        reviewer = ReviewerAgent(chain)

        deadline = time.monotonic() + 12.0
        reviewer.analyze(
            {"ideas": []}, _idea_review_context(),
            known_risky_claims=[], mandatory_fields=["ideas"], deadline=deadline,
        )

        self.assertNotEqual(fake_provider.received_writer_budget_seconds, "not called")
        self.assertIsNotNone(fake_provider.received_writer_budget_seconds)
        self.assertLessEqual(fake_provider.received_writer_budget_seconds, 12.0)

    def test_rewrite_forwards_writer_budget_seconds_on_high_tier(self):
        fake_provider = _FakeGenerateJsonProvider({"ideas": []})

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None,
                                reporter=None, schema_description=None,
                                estimated_prompt_tokens=None, provider_token_limits=None,
                                writer_budget_seconds=None, deadline=None,
                                cap_timeout_to_deadline=False):
            budget = (deadline - time.monotonic()) if (deadline is not None and cap_timeout_to_deadline) else None
            fake_provider.received_writer_budget_seconds = budget
            return LLMResult(ok=True, provider="fake", model="m", text="", data={"ideas": []})

        chain = ProviderChain(providers=[fake_provider])
        chain.generate_json = fake_generate_json
        rewrite_agent = RewriteAgent(chain)

        deadline = time.monotonic() + 9.0
        rewrite_agent.rewrite(
            {"ideas": []},
            [ReviewerIssue(
                severity="high", requiresCorrection=True, category="quality",
                affectedField="ideas", description="x", revisionInstruction="fix it",
            )],
            _idea_review_context(), agent_name="ProjectIdeaAgent", deadline=deadline,
        )

        self.assertIsNotNone(fake_provider.received_writer_budget_seconds)
        self.assertLessEqual(fake_provider.received_writer_budget_seconds, 9.0)

    def test_provider_chain_search_web_skips_once_deadline_nearly_exhausted(self):
        """Shared, tier-independent guard (ProviderChain._MIN_SECONDS_PER_
        PROVIDER_ATTEMPT) -- confirms the Writer's search step still refuses
        to start a call it has no time left for, unaffected by the timing
        dict fix."""
        chain = ProviderChain(tier="high")
        deadline = time.monotonic() + 1.0  # below _MIN_SECONDS_PER_PROVIDER_ATTEMPT (4.0)

        result = chain.search_web("test query", deadline=deadline)

        self.assertFalse(result.search_used)
        self.assertIn("skipped", result.error)


if __name__ == "__main__":
    unittest.main()
