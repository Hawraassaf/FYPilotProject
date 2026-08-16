"""
Tests for FYP Mentor Chat (FypMentorAgent) Writer/search/ReviewPipeline
absolute-deadline propagation.

Root cause this covers: FypMentorAgent's Writer stage (an OPTIONAL,
heuristically-gated search_web() call, plus a mandatory generate_json() call
-- both made inside chat(), which the router invokes via the closure passed
to ReviewPipeline.run()) had NO deadline at all. Each provider call could
use its own full independent configured timeout, so a slow-but-successful
candidate could alone exceed ReviewPipeline's entire 90s total budget (see
app/review/registry.py's FypMentorAgent.max_total_seconds), and would only
be discarded by ReviewPipeline.run's own self-computed _time_budget_exceeded
check before the Reviewer ever ran (status="review_unavailable", no
candidate) -- even though real, paid AI work had already completed. This
mirrors Idea Generation's identical two-call (search + generate) Writer-
deadline fix (see test_idea_generation_writer_deadline.py) -- same
architecture, same shared ProviderChain deadline-clamping/skip logic,
adapted to Mentor Chat's own registry budget and heuristically-optional
search step.

No real network calls, no real sleeps -- fake providers/reviewer agents
only, wired into the REAL (unmodified) ProviderChain/ReviewPipeline so their
own deadline-clamping/skip logic runs for real.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.fyp_mentor_agent import (  # noqa: E402
    FypMentorAgent,
    FypMentorRequest,
    WRITER_DEADLINE_EXCEEDED,
)
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import fyp_chat as fyp_chat_router  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


# A non-trivial, non-greeting, non-code-request message -- must NOT trigger
# try_short_circuit_answer, so chat() actually reaches the Writer stage.
_ORDINARY_MESSAGE = "How should I design the database schema for my booking system?"

# A message whose text matches FypMentorAgent._should_search_web's heuristic
# (contains "compare"), so chat() performs the optional search step.
_SEARCH_TRIGGERING_MESSAGE = "Can you compare Postgres and MySQL for my project?"


def _request(message: str = _ORDINARY_MESSAGE) -> FypMentorRequest:
    return FypMentorRequest(message=message)


def _valid_answer_payload() -> dict:
    return {
        "reply": "Use a normalized schema with a bookings table referencing users and rooms.",
        "intent": "database_help",
        "usedContext": [],
        "suggestedNextActions": ["Design the bookings table first."],
        "warning": "",
        "confidence": 85,
        "assumptions": [],
        "codeBlocks": [],
    }


def _failed_search_result() -> LLMResult:
    return LLMResult(
        ok=False, provider="none", model=None, text="", data=None,
        error="no search in this test", search_used=False, search_failed=True,
    )


def _successful_search_result() -> LLMResult:
    return LLMResult(
        ok=True, provider="groq", model="groq-compound", text="", data=None,
        search_used=True, search_failed=False,
        sources=[{"title": "Postgres vs MySQL", "url": "https://example.org/x", "snippet": "s"}],
    )


def _agent_with_fake_providers(*providers) -> FypMentorAgent:
    """
    Real FypMentorAgent wired to a REAL ProviderChain built from the given
    fake providers (generation chain only), so ProviderChain's own
    (unmodified, shared) _run_cascade deadline/skip logic runs for real.
    search_web is stubbed to fail fast -- no real Brave/Groq network calls.
    """
    agent = FypMentorAgent()
    agent.provider_chain = ProviderChain(providers=list(providers))
    agent.provider_chain.search_web = lambda *_a, **_kw: _failed_search_result()
    return agent


class _FakeProvider:
    """
    Stands in for a real BaseProvider (DeepInfraProvider/GroqProvider/
    OllamaProvider) inside a REAL ProviderChain -- so ProviderChain's own
    (unmodified, shared) _run_cascade deadline/skip logic runs for real,
    while this fake never makes a network call or sleeps.
    """

    def __init__(self, name: str, *, ok: bool = True):
        self.name = name
        self._ok = ok
        self.call_count = 0
        self.received_writer_budget_seconds: float | None | str = "not called"

    def generate_json(
        self, prompt, *, use_search=False, max_tokens=None, reporter=None,
        schema_description=None, writer_budget_seconds=None,
    ) -> LLMResult:
        self.call_count += 1
        self.received_writer_budget_seconds = writer_budget_seconds

        if self._ok:
            return LLMResult(
                ok=True, provider=self.name, model="fake-model", text="",
                data=_valid_answer_payload(),
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake provider failure", error_category="provider_http_error",
        )


class _FakeSearchProvider:
    def __init__(self, name: str, *, ok: bool = True):
        self.name = name
        self._ok = ok
        self.call_count = 0
        self.received_writer_budget_seconds: float | None | str = "not called"

    def search_web(self, query, *, writer_budget_seconds=None) -> LLMResult:
        self.call_count += 1
        self.received_writer_budget_seconds = writer_budget_seconds

        if self._ok:
            return _successful_search_result()

        return LLMResult(
            ok=False, provider=self.name, model=None, text="", data=None,
            error="fake search provider failure", search_used=False, search_failed=True,
        )


# ---------------------------------------------------------------------------
# Tests 1, 2, 3, 4: router creates one global deadline, a strictly shorter
# writer deadline (by exactly the reserve), and forwards both correctly.
# ---------------------------------------------------------------------------


class _CapturingAgent:
    """Records the deadline the router forwards to generate_candidate,
    without making any real call."""

    def __init__(self):
        self.last_llm_used = False
        self.last_provider = None
        self.last_model_used = None
        self.last_error = None
        self.last_sources: list = []
        self.last_search_used = False
        self.last_search_failed = False
        self.last_search_firewall_blocked = False
        self.last_search_firewall_flags: list = []
        self.last_fallback_reason_code: str | None = None
        self.last_search_intent: str | None = None
        self.last_search_query: str | None = None
        self.last_search_quality: str | None = None
        self.last_search_classification_source: str | None = None
        self.captured_writer_deadline: float | None | str = "not called"

    def try_short_circuit_answer(self, request):
        return None

    def generate_candidate(self, request, *, deadline=None):
        self.captured_writer_deadline = deadline
        return None

    def build_safe_fallback(self, request):
        return SimpleNamespace(model_dump=lambda: {"reply": "fallback"})


class _CapturingPipeline:
    """Records the deadline the router passes to run(), while resolving
    max_total_seconds from the REAL registry config (so this test stays
    correct if that value is ever retuned)."""

    def __init__(self, agent_name, tier):
        from app.review.registry import get_agent_config
        self.config = get_agent_config(agent_name)
        self.captured_global_deadline: float | None = None

    def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
        self.captured_global_deadline = deadline
        writer_call_fn()
        return SimpleNamespace(
            usable=False, status="provider_unavailable", output={}, outputOrigin="none",
        )


class RouterGlobalDeadlineTests(unittest.TestCase):
    def test_router_creates_one_global_deadline_and_a_strictly_shorter_writer_deadline(self):
        captured_pipeline: list[_CapturingPipeline] = []

        def _pipeline_factory(agent_name, tier):
            pipeline = _CapturingPipeline(agent_name, tier)
            captured_pipeline.append(pipeline)
            return pipeline

        agent_instance = _CapturingAgent()

        with patch.object(fyp_chat_router, "FypMentorAgent", return_value=agent_instance), \
             patch.object(fyp_chat_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(fyp_chat_router, "build_review_response", return_value={}):
            fyp_chat_router.fyp_chat(_request())

        pipeline = captured_pipeline[0]

        # Test 1: exactly one global deadline, from the real registry budget.
        self.assertIsNotNone(pipeline.captured_global_deadline)
        self.assertEqual(pipeline.config.max_total_seconds, 90.0)

        # Test 3: the global deadline reached ReviewPipeline.run().
        self.assertIsNotNone(pipeline.captured_global_deadline)

        # Test 4: the Writer deadline reached FypMentorAgent.
        self.assertNotEqual(agent_instance.captured_writer_deadline, "not called")
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        # Test 2: writer_deadline < global_deadline by exactly the reserve.
        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, fyp_chat_router._WRITER_TIME_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 5: the Writer deadline reaches generate_json().
# ---------------------------------------------------------------------------


class ProviderDeadlinePropagationTests(unittest.TestCase):
    # search_planner.classify_search_intent_via_ai patched out below: this
    # class tests the WRITER's own generation-provider-cascade deadline math
    # in isolation, a distinct concern from search-intent classification
    # (see test_mentor_ai_search_fallback_integration.py for that). Without
    # this, _ORDINARY_MESSAGE (deliberately chosen to not match the
    # deterministic classifier either) would also trigger the AI fallback's
    # OWN extra generate_json call against these same fake providers,
    # shifting call_count by one and coupling an unrelated feature into
    # these assertions.
    @patch("app.agents.fyp_mentor_agent.classify_search_intent_via_ai", return_value=None)
    def test_provider_receives_writer_budget_seconds_no_greater_than_remaining_deadline(self, _mock_ai_fallback):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        deadline = time.monotonic() + 12.0
        agent.chat(_request(), deadline=deadline)

        self.assertEqual(provider.call_count, 1)
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 12.0)
        self.assertGreater(provider.received_writer_budget_seconds, 12.0 - 2.0)  # test-overhead margin

    @patch("app.agents.fyp_mentor_agent.classify_search_intent_via_ai", return_value=None)
    def test_no_deadline_means_no_writer_budget_forwarded(self, _mock_ai_fallback):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        agent.chat(_request())

        self.assertEqual(provider.call_count, 1)
        self.assertIsNone(provider.received_writer_budget_seconds)


# ---------------------------------------------------------------------------
# Tests 6, 7: provider timeout clamping.
# ---------------------------------------------------------------------------


class ProviderTimeoutClampingTests(unittest.TestCase):
    def test_provider_receives_reduced_budget_when_remaining_time_is_smaller(self):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        deadline = time.monotonic() + 5.0
        agent.chat(_request(), deadline=deadline)

        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 5.0)

    def test_no_unnecessary_expansion_when_remaining_time_is_larger(self):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        deadline = time.monotonic() + 90.0
        agent.chat(_request(), deadline=deadline)

        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertGreater(provider.received_writer_budget_seconds, 80.0)


# ---------------------------------------------------------------------------
# Test 8: fallback provider skipped below the shared threshold.
# ---------------------------------------------------------------------------


class FallbackProviderSkippingTests(unittest.TestCase):
    def test_second_provider_not_started_when_first_attempt_exhausts_the_budget(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = _agent_with_fake_providers(first, second)

        real_now = time.monotonic()
        deadline = real_now + 10.0  # enough for the first attempt to start

        # `time` is the stdlib singleton module -- patching
        # "app.services.llm_provider.time.monotonic" patches the function on
        # that SAME shared module object, so it also affects
        # FypMentorAgent's own time.monotonic() calls (its logging lines,
        # its _writer_deadline_exhausted pre-check) for the duration of this
        # `with` block, not just llm_provider's. A small fixed-length
        # iterator would therefore be fragile to exactly how many incidental
        # monotonic() calls happen before the cascade runs. Instead, the
        # clock only advances when `first`'s call is ACTUALLY made --
        # simulating "the first (failed) provider call took 8 real seconds"
        # regardless of how many other monotonic() reads happen around it,
        # leaving ~2s (below ProviderChain's shared
        # _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor of 4.0s) by the time the
        # SECOND provider's budget is next checked.
        elapsed_seconds = {"value": 0.0}
        real_first_generate_json = first.generate_json

        def first_generate_json_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 8.0
            return real_first_generate_json(*args, **kwargs)

        first.generate_json = first_generate_json_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.chat(_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)
        self.assertFalse(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 9: fallback provider runs when enough time remains.
# ---------------------------------------------------------------------------


class FallbackProviderRunsWithEnoughTimeTests(unittest.TestCase):
    @patch("app.agents.fyp_mentor_agent.classify_search_intent_via_ai", return_value=None)
    def test_second_provider_runs_when_first_fails_and_time_remains(self, _mock_ai_fallback):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = _agent_with_fake_providers(first, second)

        deadline = time.monotonic() + 90.0
        agent.chat(_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)
        self.assertTrue(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 10: pre-generation exhaustion prevents any provider call.
# ---------------------------------------------------------------------------


class NoHangWhenDeadlineExhaustedTests(unittest.TestCase):
    def test_generation_returns_quickly_without_calling_a_provider(self):
        provider = _FakeProvider("fake-deepinfra", ok=False)
        agent = _agent_with_fake_providers(provider)

        deadline = time.monotonic() + 0.1

        started = time.monotonic()
        agent.chat(_request(), deadline=deadline)
        elapsed = time.monotonic() - started

        self.assertEqual(provider.call_count, 0)
        self.assertLess(elapsed, 0.5)
        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)


# ---------------------------------------------------------------------------
# Tests 11, 12, 13: typed deadline reason; never schema_invalid; review never
# invoked without a usable candidate.
# ---------------------------------------------------------------------------


class DeadlineExhaustionProvenanceTests(unittest.TestCase):
    def test_agent_reports_writer_deadline_exceeded_not_a_generic_failure(self):
        agent = _agent_with_fake_providers(_FakeProvider("fake", ok=False))

        already_past_deadline = time.monotonic() - 1.0
        answer = agent.chat(_request(), deadline=already_past_deadline)

        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        self.assertTrue(answer.reply)

    def test_deadline_exhaustion_pipeline_status_is_provider_unavailable_never_schema_invalid(self):
        agent = _agent_with_fake_providers(_FakeProvider("fake", ok=False))

        reviewer = _NeverCalledReviewerAgent()
        pipeline = ReviewPipeline("FypMentorAgent", tier="mentor", reviewer_agent=reviewer)

        request = _request()
        context = fyp_chat_router._build_review_context(request)
        already_past_deadline = time.monotonic() - 1.0

        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=already_past_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=time.monotonic() + 90.0,
        )

        self.assertEqual(result.status, "provider_unavailable")
        self.assertNotEqual(result.status, "schema_invalid")
        self.assertFalse(result.usable)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        self.assertEqual(reviewer.call_count, 0)


class _NeverCalledReviewerAgent:
    def __init__(self):
        self.call_count = 0

    def analyze(self, candidate, context, **kwargs):
        self.call_count += 1
        raise AssertionError("reviewer must not be called after a Writer-deadline failure")


# ---------------------------------------------------------------------------
# Test 14: Reviewer receives reserved time when the Writer finishes fast.
# ---------------------------------------------------------------------------


def _ok(data):
    return LLMResult(ok=True, provider="fake-reviewer", model="fake-model", text="", data=data)


def _reviewer_ok_payload():
    return {"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "solid"}


class _FakeReviewerAgent:
    def __init__(self, results):
        self._results = list(results)
        self.received_candidates: list = []

    def analyze(self, candidate, context, **kwargs):
        self.received_candidates.append(candidate)
        return self._results.pop(0)


class ReviewReceivesReservedTimeTests(unittest.TestCase):
    def test_reviewer_is_invoked_and_candidate_is_accepted(self):
        agent = _agent_with_fake_providers(_FakeProvider("fake-deepinfra", ok=True))

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("FypMentorAgent", tier="mentor", reviewer_agent=reviewer)

        request = _request()
        context = fyp_chat_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - fyp_chat_router._WRITER_TIME_RESERVE_SECONDS

        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=writer_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )

        self.assertEqual(len(reviewer.received_candidates), 1)
        self.assertTrue(result.usable)
        self.assertEqual(result.status, "approved")
        self.assertTrue(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 15: cancellation propagates honestly, never converted into a result.
#
# Honest limitation: search_web/generate_json here are synchronous, blocking
# calls. Cancellation is only ever observed BEFORE such a call starts or
# AFTER it raises/returns -- this task does not make an in-flight blocking
# request interruptible.
# ---------------------------------------------------------------------------


class CancellationTests(unittest.TestCase):
    def test_cancelled_error_from_generation_propagates(self):
        class _CancellingProvider:
            name = "cancelling-generation"

            def generate_json(self, *args, **kwargs):
                raise asyncio.CancelledError()

        agent = _agent_with_fake_providers(_CancellingProvider())

        with self.assertRaises(asyncio.CancelledError):
            agent.chat(_request(), deadline=time.monotonic() + 90.0)

        self.assertIsNone(agent.last_fallback_reason_code)

    def test_cancelled_error_from_search_propagates_and_generation_is_never_called(self):
        class _CancellingSearchProvider:
            name = "cancelling-search"

            def search_web(self, *args, **kwargs):
                raise asyncio.CancelledError()

        generation_provider = _FakeProvider("fake-generation")
        agent = FypMentorAgent()
        agent.provider_chain = ProviderChain(providers=[generation_provider])
        agent.provider_chain.search_web = _CancellingSearchProvider().search_web

        with self.assertRaises(asyncio.CancelledError):
            agent.chat(_request(_SEARCH_TRIGGERING_MESSAGE), deadline=time.monotonic() + 90.0)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 16: successful output unchanged (with and without a deadline).
# ---------------------------------------------------------------------------


class BackwardCompatibilityTests(unittest.TestCase):
    def test_successful_generation_output_is_identical_regardless_of_deadline(self):
        agent_a = _agent_with_fake_providers(_FakeProvider("fake-a", ok=True))
        agent_b = _agent_with_fake_providers(_FakeProvider("fake-b", ok=True))

        request = _request()
        answer_a = agent_a.chat(request)
        answer_b = agent_b.chat(request, deadline=time.monotonic() + 90.0)

        self.assertEqual(answer_a.reply, answer_b.reply)
        self.assertTrue(agent_a.last_llm_used)
        self.assertTrue(agent_b.last_llm_used)

    # Test 17: normal provider failure remains unchanged.
    def test_provider_failure_unrelated_to_deadline_still_falls_back_deterministically(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)

        answer = agent.chat(_request(), deadline=time.monotonic() + 90.0)

        self.assertFalse(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertTrue(answer.reply)

    # Test 18: provider order remains unchanged.
    def test_default_provider_chain_order_is_unchanged(self):
        chain = ProviderChain()
        names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])

    # Test 19: retry counts remain unchanged.
    @patch("app.agents.fyp_mentor_agent.classify_search_intent_via_ai", return_value=None)
    def test_no_extra_generation_attempts_are_added_beyond_the_configured_providers(self, _mock_ai_fallback):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=False)
        agent = _agent_with_fake_providers(first, second)

        agent.chat(_request(), deadline=time.monotonic() + 90.0)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)


# ---------------------------------------------------------------------------
# Test 20: legacy fake compatibility.
# ---------------------------------------------------------------------------


class LegacyProviderChainCompatibilityTests(unittest.TestCase):
    @patch("app.agents.fyp_mentor_agent.classify_search_intent_via_ai", return_value=None)
    def test_old_style_provider_chain_without_deadline_support_still_works(self, _mock_ai_fallback):
        class _StrictLegacyProviderChain:
            """Matches the PRE-deadline generate_json/search_web signatures
            exactly -- neither accepts `deadline`. Proves that supplying a
            deadline never raises TypeError against an older fake."""

            def __init__(self, result: LLMResult):
                self.result = result
                self.generate_calls: list = []
                self.search_calls: list = []

            def search_web(self, query):
                self.search_calls.append(query)
                return _failed_search_result()

            def generate_json(self, prompt, *, use_search=False, max_tokens=None):
                self.generate_calls.append(prompt)
                return self.result

        fake_chain = _StrictLegacyProviderChain(_ok(_valid_answer_payload()))
        agent = FypMentorAgent()
        agent.provider_chain = fake_chain

        answer = agent.chat(_request(), deadline=time.monotonic() + 90.0)

        self.assertTrue(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(fake_chain.generate_calls), 1)
        self.assertTrue(answer.reply)


# ---------------------------------------------------------------------------
# Test 21: search shares the Writer deadline.
# ---------------------------------------------------------------------------


class SearchSharesWriterDeadlineTests(unittest.TestCase):
    def test_search_web_receives_the_same_deadline_passed_into_chat(self):
        agent = FypMentorAgent()
        captured: dict = {}

        def capturing_search_web(query, *, deadline=None):
            captured["deadline"] = deadline
            return _failed_search_result()

        agent.provider_chain.search_web = capturing_search_web
        agent.provider_chain.generate_json = lambda *a, **kw: LLMResult(
            ok=True, provider="fake", model="m", text="", data=_valid_answer_payload(),
        )

        writer_deadline = time.monotonic() + 90.0
        agent.chat(_request(_SEARCH_TRIGGERING_MESSAGE), deadline=writer_deadline)

        self.assertIn("deadline", captured)
        self.assertEqual(captured["deadline"], writer_deadline)


# ---------------------------------------------------------------------------
# Test 22: search failure behavior remains unchanged -- optional search, a
# failure never blocks generation.
# ---------------------------------------------------------------------------


class OptionalSearchContractPreservedTests(unittest.TestCase):
    def test_generation_runs_when_search_fails(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeProvider("fake-generation", ok=True)
        agent = FypMentorAgent()
        agent.provider_chain = ProviderChain(
            providers=[generation_provider], search_providers=[search_provider],
        )

        deadline = time.monotonic() + 90.0
        answer = agent.chat(_request(_SEARCH_TRIGGERING_MESSAGE), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 1)
        self.assertTrue(answer.reply)
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 23: search cannot reset the Writer budget -- generation sees reduced
# remaining time after a slow search.
# ---------------------------------------------------------------------------


class SharedWriterBudgetTests(unittest.TestCase):
    def test_generation_receives_reduced_remaining_time_after_a_slow_search(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeProvider("fake-generation", ok=True)
        agent = FypMentorAgent()
        agent.provider_chain = ProviderChain(
            providers=[generation_provider], search_providers=[search_provider],
        )

        real_now = time.monotonic()
        deadline = real_now + 90.0
        elapsed_seconds = {"value": 0.0}
        real_search = search_provider.search_web

        def search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 20.0  # simulate search taking 20 real seconds
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.chat(_request(_SEARCH_TRIGGERING_MESSAGE), deadline=deadline)

        self.assertEqual(search_provider.call_count, 1)
        self.assertEqual(generation_provider.call_count, 1)
        self.assertIsInstance(generation_provider.received_writer_budget_seconds, float)
        # ~70s = 90 - 20, not a fresh ~90s budget.
        self.assertAlmostEqual(generation_provider.received_writer_budget_seconds, 70.0, delta=1.0)

    def test_generation_is_skipped_when_search_exhausts_the_budget(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeProvider("fake-generation", ok=True)
        agent = FypMentorAgent()
        agent.provider_chain = ProviderChain(
            providers=[generation_provider], search_providers=[search_provider],
        )

        real_now = time.monotonic()
        deadline = real_now + 90.0
        elapsed_seconds = {"value": 0.0}
        real_search = search_provider.search_web

        def search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 87.0  # leaves only 3s -- below the 4.0s floor
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            answer = agent.chat(_request(_SEARCH_TRIGGERING_MESSAGE), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        self.assertTrue(answer.reply)


# ---------------------------------------------------------------------------
# Test 30: Market Footprint remains unchanged by this task.
# ---------------------------------------------------------------------------


class OtherAgentsUnaffectedTests(unittest.TestCase):
    def test_market_footprint_agent_signature_is_unaffected(self):
        from app.agents.market_footprint_agent import MarketFootprintAgent

        sig = inspect.signature(MarketFootprintAgent.generate_candidate_from_result)
        self.assertNotIn("deadline", sig.parameters)


if __name__ == "__main__":
    unittest.main()
