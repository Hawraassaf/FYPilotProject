"""
Regression tests for the Mentor Chat timeout/hidden-retry fix.

Root cause this guards against (confirmed LIVE, not theoretical, on an
unmocked /fyp-chat call): "mentor" was absent from llm_provider.py's
_DEEPINFRA_TIER_TIMING / _GROQ_TIER_TIMING dicts, so DeepInfraProvider/
GroqProvider(max_retries=None, ...) let the underlying SDK's own default
retry count (2) apply. A single Writer generate_json() call was observed
taking ~69s (a full ~60s timeout, an SDK-logged retry, then a second
attempt) against a 65s Writer budget -- and neither ReviewerAgent.analyze
nor RewriteAgent's calls capped their per-attempt provider timeout to the
remaining pipeline deadline at all (no cap_timeout_to_deadline), unlike the
Writer stage.

These tests are deterministic -- no real sleeping, no real network calls --
using fake provider objects/clocks, matching this repo's existing
test_mentor_chat_writer_deadline.py convention.

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

from app.review.context import ReviewContext  # noqa: E402
from app.review.reviewer_agent import ReviewerAgent  # noqa: E402
from app.review.rewrite_agent import RewriteAgent  # noqa: E402
from app.services import llm_provider as llm_provider_module  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    DeepInfraProvider,
    GroqProvider,
    LLMResult,
    ProviderChain,
)


class MentorTierRetryConfigTests(unittest.TestCase):
    """Static configuration assertions -- the actual fix."""

    def test_mentor_tier_has_deepinfra_timing_entry(self):
        self.assertIn("mentor", llm_provider_module._DEEPINFRA_TIER_TIMING)

    def test_mentor_tier_disables_deepinfra_hidden_retries(self):
        timing = llm_provider_module._deepinfra_timing_for_tier("mentor")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_mentor_tier_has_groq_timing_entry(self):
        self.assertIn("mentor", llm_provider_module._GROQ_TIER_TIMING)

    def test_mentor_tier_disables_groq_hidden_retries(self):
        timing = llm_provider_module._groq_timing_for_tier("mentor")
        self.assertEqual(timing.get("max_retries"), 0)

    def test_mentor_provider_chain_constructs_deepinfra_with_max_retries_zero(self):
        chain = ProviderChain(tier="mentor")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))
        self.assertEqual(deepinfra.max_retries, 0)

    def test_mentor_provider_chain_constructs_groq_with_max_retries_zero(self):
        chain = ProviderChain(tier="mentor")
        groq = next(p for p in chain.providers if isinstance(p, GroqProvider))
        self.assertEqual(groq.max_retries, 0)

    def test_mentor_provider_chain_client_omits_sdk_default_retry(self):
        """The actual behavior the live bug traced back to: with
        max_retries=0 explicitly set (not None), the constructed SDK client
        must receive max_retries=0, never silently fall through to the SDK's
        own default of 2."""
        chain = ProviderChain(tier="mentor")
        deepinfra = next(p for p in chain.providers if isinstance(p, DeepInfraProvider))

        captured_kwargs: dict = {}

        def fake_openai(**kwargs):
            captured_kwargs.update(kwargs)
            return object()

        with patch("openai.OpenAI", side_effect=fake_openai):
            deepinfra._client()

        self.assertEqual(captured_kwargs.get("max_retries"), 0)


class _FakeGenerateJsonProvider:
    """Stands in for a real provider inside a REAL ProviderChain -- records
    whether cap_timeout_to_deadline caused writer_budget_seconds to be
    forwarded, without any network call or sleep."""

    name = "fake"

    def __init__(self):
        self.received_writer_budget_seconds: float | None | str = "not called"

    def generate_json(self, prompt, *, use_search=False, max_tokens=None,
                       reporter=None, schema_description=None, writer_budget_seconds=None):
        self.received_writer_budget_seconds = writer_budget_seconds
        return LLMResult(
            ok=True, provider=self.name, model="fake-model", text="",
            data={"strengths": [], "issues": [], "qualityScore": 90, "overallAssessment": "ok"},
        )


class ReviewerRewriteDeadlineCappingTests(unittest.TestCase):
    """
    Confirms the SECOND half of the live bug is fixed: ReviewerAgent /
    RewriteAgent must forward writer_budget_seconds (i.e. actually cap the
    provider's own per-attempt timeout to the remaining deadline), not only
    decide whether to start the next fallback provider.
    """

    def _context(self) -> ReviewContext:
        return ReviewContext(
            agent_name="FypMentorAgent",
            trusted_system_instructions="",
            trusted_structural_context={},
            untrusted_project_text={},
            untrusted_user_input="How should I design my database?",
            untrusted_conversation_history=[],
            untrusted_existing_code=None,
            untrusted_retrieved_web_content=[],
            previous_model_outputs=[],
            allowed_source_metadata=[],
        )

    def test_reviewer_forwards_writer_budget_seconds_when_deadline_given(self):
        import time

        fake_provider = _FakeGenerateJsonProvider()
        chain = ProviderChain(providers=[fake_provider])
        reviewer = ReviewerAgent(chain)

        deadline = time.monotonic() + 12.0
        reviewer.analyze(
            {"reply": "x"}, self._context(),
            known_risky_claims=[], mandatory_fields=["reply"], deadline=deadline,
        )

        self.assertNotEqual(fake_provider.received_writer_budget_seconds, "not called")
        self.assertIsNotNone(fake_provider.received_writer_budget_seconds)
        self.assertLessEqual(fake_provider.received_writer_budget_seconds, 12.0)

    def test_reviewer_does_not_cap_when_no_deadline_given(self):
        """None (unbounded) preserves exact prior behavior for every caller
        that doesn't pass a deadline -- must not regress non-deadline callers."""
        fake_provider = _FakeGenerateJsonProvider()
        chain = ProviderChain(providers=[fake_provider])
        reviewer = ReviewerAgent(chain)

        reviewer.analyze(
            {"reply": "x"}, self._context(),
            known_risky_claims=[], mandatory_fields=["reply"], deadline=None,
        )

        self.assertIsNone(fake_provider.received_writer_budget_seconds)

    def test_rewrite_forwards_writer_budget_seconds_when_deadline_given(self):
        import time
        from app.review.models import ReviewerIssue

        fake_provider = _FakeGenerateJsonProvider()

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None,
                                reporter=None, schema_description=None,
                                estimated_prompt_tokens=None, provider_token_limits=None,
                                writer_budget_seconds=None, deadline=None,
                                cap_timeout_to_deadline=False):
            # Mirrors ProviderChain.generate_json's own cap_timeout_to_deadline
            # contract for a single fake provider, without needing the full
            # cascade machinery.
            budget = (deadline and __import__("time").monotonic()) and (deadline - __import__("time").monotonic())
            fake_provider.received_writer_budget_seconds = budget if cap_timeout_to_deadline else None
            return LLMResult(ok=True, provider="fake", model="m", text="", data={"reply": "fixed"})

        chain = ProviderChain(providers=[fake_provider])
        chain.generate_json = fake_generate_json
        rewrite_agent = RewriteAgent(chain)

        deadline = time.monotonic() + 9.0
        rewrite_agent.rewrite(
            {"reply": "bad"}, [ReviewerIssue(
                severity="high", requiresCorrection=True, category="unsupported_claim",
                affectedField="reply", description="x", revisionInstruction="fix it",
            )],
            self._context(), agent_name="FypMentorAgent", deadline=deadline,
        )

        self.assertIsNotNone(fake_provider.received_writer_budget_seconds)
        self.assertLessEqual(fake_provider.received_writer_budget_seconds, 9.0)


class SlowProviderDoesNotBlowMentorBudgetTests(unittest.TestCase):
    """
    End-to-end-ish simulation: a provider that would (without this fix) take
    far longer than its configured timeout via hidden SDK retries must not
    be able to silently multiply its actual attempt time within a single
    ProviderChain.generate_json call once max_retries=0 is honored. This
    doesn't sleep -- it asserts on the CONFIGURATION that prevents the
    multiplication (max_retries=0 for the mentor tier), which is what the
    live trace showed was missing.
    """

    def test_mentor_tier_deepinfra_and_groq_both_configured_for_single_attempt(self):
        chain = ProviderChain(tier="mentor")
        for provider in chain.providers:
            if isinstance(provider, (DeepInfraProvider, GroqProvider)):
                self.assertEqual(
                    provider.max_retries, 0,
                    f"{provider.name} must be configured for a single attempt "
                    "(max_retries=0) on the mentor tier -- otherwise the "
                    "underlying SDK's own default retry count silently "
                    "multiplies a single ProviderChain attempt's real duration.",
                )


if __name__ == "__main__":
    unittest.main()
