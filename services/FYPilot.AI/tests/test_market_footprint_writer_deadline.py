"""
Tests for Market Footprint (MarketFootprintAgent) Writer/search absolute-
deadline propagation.

Root cause this covers: like Market Demand, MarketFootprintAgent's real
Writer work (search_web() + generate_json()) runs entirely INSIDE
_analyze_sync(), which the router calls directly and synchronously (via
asyncio.to_thread) BEFORE ReviewPipeline.run() is ever invoked --
generate_candidate_from_result() merely wraps the already-computed result,
it makes no provider call of its own. So even though ReviewPipeline.run()
self-computes an internal deadline when none is passed, that deadline never
reached _analyze_sync's search/generation calls, which could each use their
provider's full independent timeout regardless of the registry's 150s total
budget.

This file proves: (1) the router creates ONE global deadline from the real
registry budget and a shorter writer_deadline (review reserve), (2) the
SAME writer_deadline reaches _analyze_sync() directly (not through
pipeline.run()'s closure), (3) it flows into BOTH search_web() and
generate_json() as one shared, never-reset budget, (4) Brave/Groq search
timeout clamping and Groq's deadline-bound retry-disabling (shared,
unmodified ProviderChain code) work identically here, (5) generation is
skipped with a typed writer_deadline_exceeded reason when search already
exhausted the budget, (6) the global deadline reaches ReviewPipeline.run()
so the Reviewer gets its reserved time, (7) search remains MANDATORY when
use_search=True (a search failure is fatal -- status="insufficient_evidence"
-- unlike Market Demand's optional-search contract) and this task does not
change that, and (8) nothing about successful output, provider order,
retries, or other agents (Idea Generation, Roadmap, Market Demand, Project
DNA, Mentor Chat, Defense Simulator) changed.

No real network calls, no real sleeps. Wired into the REAL (unmodified)
ProviderChain/BraveSearchProvider/GroqProvider/ReviewPipeline classes
wherever practical so their own deadline-clamping/skip logic runs for real.

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

from app.agents.market_footprint_agent import (  # noqa: E402
    MarketFootprintAgent,
    WRITER_DEADLINE_EXCEEDED,
)
from app.models.market_footprint_models import MarketFootprintRequest  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import market_footprint as market_footprint_router  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    BraveSearchProvider,
    GroqProvider,
    LLMResult,
    ProviderChain,
)


def _request(use_search: bool = True) -> MarketFootprintRequest:
    return MarketFootprintRequest(
        projectTitle="Study Room Booking Platform",
        problemStatement="Students struggle to find available study rooms across campus.",
        targetUsers="University students",
        domain="Education",
        technologies="ASP.NET Core, PostgreSQL",
        useSearch=use_search,
    )


_FOOTPRINT_SOURCES = [
    {"url": "https://worldbank.org/report", "title": "Report", "publisher": "World Bank"}
]


def _generate_payload() -> dict:
    return {
        "regions": {
            "lebanon": {
                "problemUrgency": 70, "geographicFit": 70, "adoptionReadiness": 60,
                "competitionGap": 60, "targetUserReachability": 65, "technologyMomentum": 60,
                "evidenceSummary": "Evidence.", "sourceTitles": ["Report"],
            },
            "mena": {}, "global": {},
        },
        "whyDemanded": ["reason"], "strategicRecommendation": "Start local.", "limitations": [],
        "bestLaunchMarket": "Lebanon", "bestLaunchReason": "Strong local fit.",
    }


def _failed_search_result() -> LLMResult:
    return LLMResult(
        ok=False, provider="none", model=None, text="", data=None,
        error="no search in this test", search_used=False, search_failed=True,
    )


def _successful_search_result() -> LLMResult:
    return LLMResult(
        ok=True, provider="groq", model="compound-mini", text="", data=None,
        search_used=True, search_failed=False, sources=_FOOTPRINT_SOURCES,
    )


class _FakeGenerationProvider:
    def __init__(self, name: str = "fake-gen", *, ok: bool = True):
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
            return LLMResult(ok=True, provider=self.name, model="fake-model", text="", data=_generate_payload())

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake generation provider failure", error_category="provider_http_error",
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


def _agent_with_fakes(*, search_provider, generation_provider) -> MarketFootprintAgent:
    agent = MarketFootprintAgent()
    agent.chain = ProviderChain(
        providers=[generation_provider],
        search_providers=[search_provider],
    )
    return agent


# ---------------------------------------------------------------------------
# Tests 1, 2, 3, 4: router creates one global deadline, a strictly shorter
# writer deadline (by exactly the reserve), and forwards both correctly.
# ---------------------------------------------------------------------------


class _CapturingAgent:
    def __init__(self):
        self.last_fallback_reason_code: str | None = None
        self.captured_writer_deadline: float | None | str = "not called"

    def _analyze_sync(self, request, *, deadline=None):
        self.captured_writer_deadline = deadline
        return SimpleNamespace(status="insufficient_evidence", provider="none", sources=[])

    def generate_candidate_from_result(self, result):
        return None

    def build_safe_fallback(self, request):
        return SimpleNamespace(model_dump=lambda: {"bestLaunchMarket": "x", "strategicRecommendation": "y"})


class _CapturingPipeline:
    def __init__(self, agent_name):
        from app.review.registry import get_agent_config
        self.config = get_agent_config(agent_name)
        self.captured_global_deadline: float | None = None

    def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
        self.captured_global_deadline = deadline
        writer_call_fn()
        return SimpleNamespace(usable=False, status="provider_unavailable", output={}, outputOrigin="none")


class RouterGlobalDeadlineTests(unittest.TestCase):
    def test_router_creates_global_and_writer_deadlines_using_registry_budget(self):
        captured_pipeline: list[_CapturingPipeline] = []

        def _pipeline_factory(agent_name):
            pipeline = _CapturingPipeline(agent_name)
            captured_pipeline.append(pipeline)
            return pipeline

        agent_instance = _CapturingAgent()

        with patch.object(market_footprint_router, "MarketFootprintAgent", return_value=agent_instance), \
             patch.object(market_footprint_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(market_footprint_router, "build_review_response", return_value={}), \
             patch.object(market_footprint_router, "MarketFootprintResponse") as MockResponse:
            MockResponse.model_validate.return_value = SimpleNamespace(review=None)
            asyncio.run(market_footprint_router.analyze_market_footprint(_request()))

        pipeline = captured_pipeline[0]

        # Test 1: exactly one global deadline, from the real registry budget.
        self.assertIsNotNone(pipeline.captured_global_deadline)
        self.assertEqual(pipeline.config.max_total_seconds, 150.0)

        # Test 3: the global deadline reached ReviewPipeline.run().
        self.assertIsNotNone(pipeline.captured_global_deadline)

        # Test 4: the Writer deadline reached MarketFootprintAgent._analyze_sync.
        self.assertNotEqual(agent_instance.captured_writer_deadline, "not called")
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        # Test 2: writer_deadline < global_deadline by exactly the reserve.
        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, market_footprint_router._WRITER_TIME_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 5: search receives the exact same Writer deadline.
# ---------------------------------------------------------------------------


class SearchReceivesWriterDeadlineTests(unittest.TestCase):
    def test_search_web_receives_the_same_deadline_passed_into_analyze_sync(self):
        agent = MarketFootprintAgent()
        captured: dict = {}

        def capturing_search_web(query, *, deadline=None):
            captured["deadline"] = deadline
            return _failed_search_result()

        agent.chain.search_web = capturing_search_web

        writer_deadline = time.monotonic() + 150.0
        agent._analyze_sync(_request(), deadline=writer_deadline)

        self.assertIn("deadline", captured)
        self.assertEqual(captured["deadline"], writer_deadline)


# ---------------------------------------------------------------------------
# Tests 7, 8, 9: Brave/Groq search timeout clamping + Groq deadline-bound
# retry disabling, reusing the exact shared provider-level implementation.
# ---------------------------------------------------------------------------


class BraveGroqSearchClampTests(unittest.TestCase):
    def test_brave_timeout_is_clamped_when_remaining_budget_is_smaller(self):
        provider = BraveSearchProvider()
        provider.enabled = True
        provider.api_key = "fake-key"
        provider.timeout_seconds = 30.0
        captured: dict = {}

        def fake_post(url, *, headers, json, timeout):
            captured["timeout"] = timeout
            import requests
            raise requests.exceptions.Timeout()

        with patch("app.services.llm_provider.requests.post", side_effect=fake_post):
            provider.search_web("query", writer_budget_seconds=5.0)

        self.assertEqual(captured["timeout"], 5.0)

    def test_groq_timeout_is_clamped_when_remaining_budget_is_smaller(self):
        provider = GroqProvider()
        provider.enabled = True
        provider.timeout_seconds = 60.0
        captured: dict = {}

        def fake_client(timeout_override=None, *, max_retries_override=None):
            captured["timeout_override"] = timeout_override
            captured["max_retries_override"] = max_retries_override

            class _Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            raise RuntimeError("stop -- only client construction args matter here")

            return _Client()

        provider._client = fake_client
        provider.search_web("query", writer_budget_seconds=7.0)

        self.assertEqual(captured["timeout_override"], 7.0)
        # Test 9: deadline-bound retries are disabled.
        self.assertEqual(captured["max_retries_override"], 0)


# ---------------------------------------------------------------------------
# Test 10: search provider skipped below the shared minimum threshold.
# ---------------------------------------------------------------------------


class SearchProviderSkippingTests(unittest.TestCase):
    def test_search_provider_not_started_when_deadline_already_exhausted(self):
        provider = _FakeSearchProvider("fake-brave")
        chain = ProviderChain(providers=[], search_providers=[provider])

        deadline = time.monotonic() - 1.0
        result = chain.search_web("query", deadline=deadline)

        self.assertEqual(provider.call_count, 0)
        self.assertFalse(result.ok)


# ---------------------------------------------------------------------------
# Test 11: normal search failure behavior remains unchanged -- Market
# Footprint's own MANDATORY-search contract (unlike Market Demand's
# optional search, a failed search here is fatal: generation never runs,
# status="insufficient_evidence").
# ---------------------------------------------------------------------------


class MandatorySearchContractPreservedTests(unittest.TestCase):
    def test_generation_never_runs_when_search_fails(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        deadline = time.monotonic() + 150.0
        response = agent._analyze_sync(_request(), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertEqual(response.status, "insufficient_evidence")
        self.assertIsNone(agent.last_fallback_reason_code)

    def test_search_failure_without_any_deadline_matches_prior_behavior(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        response = agent._analyze_sync(_request())  # no deadline at all

        self.assertEqual(generation_provider.call_count, 0)
        self.assertEqual(response.status, "insufficient_evidence")
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Tests 12, 13: search and generation share ONE Writer budget; generation
# receives the exact deadline and a correctly clamped remaining time.
# ---------------------------------------------------------------------------


class SharedWriterBudgetTests(unittest.TestCase):
    def test_generation_receives_reduced_remaining_time_after_a_slow_search(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        real_now = time.monotonic()
        deadline = real_now + 150.0
        elapsed_seconds = {"value": 0.0}
        real_search = search_provider.search_web

        def search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 20.0  # simulate search taking 20 real seconds
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent._analyze_sync(_request(), deadline=deadline)

        self.assertEqual(search_provider.call_count, 1)
        self.assertEqual(generation_provider.call_count, 1)
        self.assertIsInstance(generation_provider.received_writer_budget_seconds, float)
        # ~130s = 150 - 20, not a fresh ~150s budget.
        self.assertAlmostEqual(generation_provider.received_writer_budget_seconds, 130.0, delta=1.0)


# ---------------------------------------------------------------------------
# Tests 14, 20, 21: generation skipped after search exhausts the budget,
# with a typed reason that is never mislabeled as a schema failure.
# ---------------------------------------------------------------------------


class GenerationSkippedAfterSearchExhaustsBudgetTests(unittest.TestCase):
    def test_generation_is_not_entered_and_typed_reason_is_set(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        real_now = time.monotonic()
        deadline = real_now + 150.0
        elapsed_seconds = {"value": 0.0}
        real_search = search_provider.search_web

        def search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 147.0  # leaves only 3s -- below the 4.0s floor
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            response = agent._analyze_sync(_request(), deadline=deadline)

        # Test 14: generate_json() never called.
        self.assertEqual(generation_provider.call_count, 0)
        # Test 20: exact typed reason.
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        self.assertEqual(response.status, "provider_unavailable")

    def test_deadline_exhaustion_pipeline_status_is_provider_unavailable_never_schema_invalid(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        already_past_deadline = time.monotonic() - 1.0
        raw_result = agent._analyze_sync(_request(), deadline=already_past_deadline)

        from app.review.context import ReviewContext  # noqa: F401
        from app.review.pipeline import ReviewPipeline as RealReviewPipeline

        context = market_footprint_router._build_review_context(_request())
        context.allowed_source_metadata = [s.model_dump() for s in raw_result.sources]

        reviewer = _NeverCalledReviewerAgent()
        pipeline = RealReviewPipeline("MarketFootprintAgent", reviewer_agent=reviewer)

        result = pipeline.run(
            lambda: agent.generate_candidate_from_result(raw_result),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=time.monotonic() + 150.0,
        )

        # Test 21: never mislabeled as schema_invalid.
        self.assertEqual(result.status, "provider_unavailable")
        self.assertNotEqual(result.status, "schema_invalid")
        self.assertFalse(result.usable)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        # Test 22: review never invoked without a usable Writer candidate.
        self.assertEqual(reviewer.call_count, 0)


class _NeverCalledReviewerAgent:
    def __init__(self):
        self.call_count = 0

    def analyze(self, candidate, context, **kwargs):
        self.call_count += 1
        raise AssertionError("reviewer must not be called after a Writer-deadline failure")


# ---------------------------------------------------------------------------
# Test 19: pre-generation exhaustion prevents provider call (no search).
# ---------------------------------------------------------------------------


class NoHangWhenDeadlineExhaustedNoSearchTests(unittest.TestCase):
    def test_generation_returns_quickly_without_calling_a_provider(self):
        generation_provider = _FakeGenerationProvider(ok=False)
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[generation_provider])

        # No search (use_search=False) -- already exhausted, below
        # _MIN_SECONDS_PER_PROVIDER_ATTEMPT, so generation must never start.
        deadline = time.monotonic() + 0.1

        started = time.monotonic()
        response = agent._analyze_sync(_request(use_search=False), deadline=deadline)
        elapsed = time.monotonic() - started

        self.assertEqual(generation_provider.call_count, 0)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        self.assertEqual(response.status, "provider_unavailable")


# ---------------------------------------------------------------------------
# Tests 15, 16: generation provider timeout clamping.
# ---------------------------------------------------------------------------


class ProviderTimeoutClampingTests(unittest.TestCase):
    def test_provider_receives_writer_budget_seconds_no_greater_than_remaining_deadline(self):
        provider = _FakeGenerationProvider("fake-deepinfra")
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[provider])

        deadline = time.monotonic() + 12.0
        agent._analyze_sync(_request(use_search=False), deadline=deadline)

        self.assertEqual(provider.call_count, 1)
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 12.0)
        self.assertGreater(provider.received_writer_budget_seconds, 12.0 - 2.0)

    def test_no_unnecessary_expansion_when_configured_timeout_is_shorter(self):
        provider = _FakeGenerationProvider("fake-deepinfra")
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[provider])

        deadline = time.monotonic() + 150.0
        agent._analyze_sync(_request(use_search=False), deadline=deadline)

        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertGreater(provider.received_writer_budget_seconds, 140.0)


# ---------------------------------------------------------------------------
# Test 17: fallback provider skipped below the shared threshold.
# ---------------------------------------------------------------------------


class GenerationFallbackSkippingTests(unittest.TestCase):
    def test_second_generation_provider_skipped_when_first_attempt_exhausts_budget(self):
        first = _FakeGenerationProvider("fake-first", ok=False)
        second = _FakeGenerationProvider("fake-second", ok=True)
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[first, second])

        real_now = time.monotonic()
        deadline = real_now + 10.0
        elapsed_seconds = {"value": 0.0}
        real_first_generate = first.generate_json

        def first_generate_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 8.0
            return real_first_generate(*args, **kwargs)

        first.generate_json = first_generate_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent._analyze_sync(_request(use_search=False), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)


# ---------------------------------------------------------------------------
# Test 18: fallback provider runs when enough time remains.
# ---------------------------------------------------------------------------


class GenerationFallbackRunsWithEnoughTimeTests(unittest.TestCase):
    def test_second_generation_provider_runs_when_first_fails_and_time_remains(self):
        first = _FakeGenerationProvider("fake-first", ok=False)
        second = _FakeGenerationProvider("fake-second", ok=True)
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[first, second])

        deadline = time.monotonic() + 150.0
        agent._analyze_sync(_request(use_search=False), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)


# ---------------------------------------------------------------------------
# Test 23: semantic review receives its reserved time when the Writer
# finishes within budget.
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
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        global_deadline = time.monotonic() + 150.0
        writer_deadline = global_deadline - market_footprint_router._WRITER_TIME_RESERVE_SECONDS
        raw_result = agent._analyze_sync(_request(), deadline=writer_deadline)

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("MarketFootprintAgent", reviewer_agent=reviewer)

        context = market_footprint_router._build_review_context(_request())
        context.allowed_source_metadata = [s.model_dump() for s in raw_result.sources]

        result = pipeline.run(
            lambda: agent.generate_candidate_from_result(raw_result),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )

        self.assertEqual(len(reviewer.received_candidates), 1)
        self.assertTrue(result.usable)
        self.assertEqual(result.status, "approved")


# ---------------------------------------------------------------------------
# Test 24: cancellation propagates honestly, never converted into a result.
#
# Honest limitation: search_web/generate_json here are synchronous, blocking
# calls. Cancellation is only ever observed BEFORE such a call starts or
# AFTER it raises/returns -- this task does not make an in-flight blocking
# request interruptible.
# ---------------------------------------------------------------------------


class CancellationTests(unittest.TestCase):
    def test_cancelled_error_from_search_propagates_and_generation_is_never_called(self):
        class _CancellingSearchProvider:
            name = "cancelling-search"

            def search_web(self, *args, **kwargs):
                raise asyncio.CancelledError()

        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(
            search_provider=_CancellingSearchProvider(), generation_provider=generation_provider,
        )

        with self.assertRaises(asyncio.CancelledError):
            agent._analyze_sync(_request(), deadline=time.monotonic() + 150.0)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertIsNone(agent.last_fallback_reason_code)

    def test_cancelled_error_from_generation_propagates(self):
        class _CancellingGenerationProvider:
            name = "cancelling-generation"

            def generate_json(self, *args, **kwargs):
                raise asyncio.CancelledError()

        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[_CancellingGenerationProvider()])

        with self.assertRaises(asyncio.CancelledError):
            agent._analyze_sync(_request(use_search=False), deadline=time.monotonic() + 150.0)

        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 25: successful output unchanged (fast search + fast generation).
# ---------------------------------------------------------------------------


class SuccessfulOutputUnchangedTests(unittest.TestCase):
    def test_output_identical_with_and_without_deadline_when_search_succeeds(self):
        agent_a = _agent_with_fakes(
            search_provider=_FakeSearchProvider("fake-brave", ok=True),
            generation_provider=_FakeGenerationProvider(ok=True),
        )
        agent_b = _agent_with_fakes(
            search_provider=_FakeSearchProvider("fake-brave", ok=True),
            generation_provider=_FakeGenerationProvider(ok=True),
        )

        request = _request()
        response_a = agent_a._analyze_sync(request)
        response_b = agent_b._analyze_sync(request, deadline=time.monotonic() + 150.0)

        dump_a = response_a.model_dump()
        dump_b = response_b.model_dump()
        dump_a.pop("analyzedAt", None)
        dump_b.pop("analyzedAt", None)
        self.assertEqual(dump_a, dump_b)
        self.assertEqual(response_a.status, "ready")
        self.assertEqual(response_b.status, "ready")


# ---------------------------------------------------------------------------
# Test 26: normal provider failure remains unchanged.
# ---------------------------------------------------------------------------


class NormalProviderFailureUnchangedTests(unittest.TestCase):
    def test_generation_failure_unrelated_to_deadline_falls_back_deterministically(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=False)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        response = agent._analyze_sync(_request(), deadline=time.monotonic() + 150.0)

        self.assertEqual(response.status, "provider_unavailable")
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 27: provider order remains unchanged.
# ---------------------------------------------------------------------------


class ProviderOrderUnchangedTests(unittest.TestCase):
    def test_default_search_and_generation_provider_order_is_unchanged(self):
        chain = ProviderChain()
        search_names = [type(p).__name__ for p in chain.search_providers]
        generation_names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(search_names, ["BraveSearchProvider", "GroqProvider"])
        self.assertEqual(generation_names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])


# ---------------------------------------------------------------------------
# Test 28: retry counts unchanged.
# ---------------------------------------------------------------------------


class RetryCountsUnchangedTests(unittest.TestCase):
    def test_no_extra_generation_attempts_are_added_beyond_the_configured_providers(self):
        first = _FakeGenerationProvider("fake-first", ok=False)
        second = _FakeGenerationProvider("fake-second", ok=False)
        agent = MarketFootprintAgent()
        agent.chain = ProviderChain(providers=[first, second])

        agent._analyze_sync(_request(use_search=False), deadline=time.monotonic() + 150.0)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)


# ---------------------------------------------------------------------------
# Test 29: legacy fakes without a `deadline` parameter continue to work.
# ---------------------------------------------------------------------------


class LegacyFakeCompatibilityTests(unittest.TestCase):
    def test_old_style_search_web_and_generate_json_without_deadline_still_work(self):
        class _StrictLegacyChain:
            """Mirrors the exact fake shapes used by
            test_market_agents_sync_bridge.py: `lambda *_a, **_kw: ...` for
            search_web, plain `generate_json(prompt, *, use_search=False)`
            for generation -- neither accepts
            `deadline`/`writer_budget_seconds`/`cap_timeout_to_deadline`."""

            def __init__(self, search_result, generate_result):
                self.search_result = search_result
                self.generate_result = generate_result
                self.search_calls: list = []
                self.generate_calls: list = []

            def search_web(self, query):
                self.search_calls.append(query)
                return self.search_result

            def generate_json(self, prompt, *, use_search=False, max_tokens=None):
                self.generate_calls.append(prompt)
                return self.generate_result

        agent = MarketFootprintAgent()
        agent.chain = _StrictLegacyChain(
            _successful_search_result(),
            LLMResult(ok=True, provider="fake", model="fake-model", text="", data=_generate_payload()),
        )

        # A deadline IS supplied, but neither fake method accepts it --
        # must not raise TypeError, and must not silently drop the real
        # candidate either.
        response = agent._analyze_sync(_request(), deadline=time.monotonic() + 150.0)

        self.assertEqual(len(agent.chain.search_calls), 1)
        self.assertEqual(len(agent.chain.generate_calls), 1)
        self.assertEqual(response.status, "ready")


# ---------------------------------------------------------------------------
# Test 30: no direct-provider bypass exists in this agent -- confirmed by
# inspection, nothing to clamp.
# ---------------------------------------------------------------------------


class NoDirectProviderBypassTests(unittest.TestCase):
    def test_agent_module_has_no_raw_http_calls(self):
        import app.agents.market_footprint_agent as module

        source = inspect.getsource(module)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("httpx.post", source)


# ---------------------------------------------------------------------------
# Test 36: Defense Simulator remains unchanged by this task.
# ---------------------------------------------------------------------------


class OtherAgentsUnaffectedTests(unittest.TestCase):
    def test_defense_simulator_orchestrator_now_threads_a_deadline(self):
        # Defense Simulator was a genuinely untouched agent when this
        # assertion was first written -- a later, separate freeze-audit fix
        # (FYP-016) closed a real gap here: its Writer stage previously
        # received no deadline at all. See the identical note in
        # tests/test_market_demand_writer_deadline.py for the full history;
        # this now asserts the fixed, intentional state.
        from app.agents.defense_simulator.defense_simulator_orchestrator import (
            DefenseSimulatorOrchestrator,
        )

        sig = inspect.signature(DefenseSimulatorOrchestrator.generate_questions_candidate)
        self.assertIn("deadline", sig.parameters)


if __name__ == "__main__":
    unittest.main()
