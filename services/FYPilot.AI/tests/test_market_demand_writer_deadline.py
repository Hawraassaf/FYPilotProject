"""
Tests for Market Demand (MarketNeedsAgent) Writer/search absolute-deadline
propagation.

Root cause this covers: unlike Roadmap/Idea Generation, MarketNeedsAgent's
real Writer work (search_web() + generate_json()) runs entirely INSIDE
_analyze_sync(), which the router calls directly and synchronously (via
asyncio.to_thread) BEFORE ReviewPipeline.run() is ever invoked --
generate_candidate_from_result() merely wraps the already-computed result,
it makes no provider call of its own. So even though ReviewPipeline.run()
self-computes an internal deadline when none is passed, that deadline never
reached _analyze_sync's search/generation calls, which could each use their
provider's full independent timeout (DeepInfra/Groq ~60s, Ollama ~90s,
Brave ~30s, Groq search ~60s with SDK retries) regardless of the registry's
120s total budget.

This file proves: (1) the router creates ONE global deadline from the real
registry budget and a shorter writer_deadline (review reserve), (2) the
SAME writer_deadline reaches _analyze_sync() directly (not through
pipeline.run()'s closure), (3) it flows into BOTH search_web() and
generate_json() as one shared, never-reset budget, (4) Brave/Groq search
timeout clamping and Groq's deadline-bound retry-disabling (added for Idea
Generation) work identically here via the SAME shared ProviderChain code,
(5) generation is skipped with a typed writer_deadline_exceeded reason when
search already exhausted the budget, (6) the global deadline reaches
ReviewPipeline.run() so the Reviewer gets its reserved time, and (7)
nothing about successful output, provider order, retries, or other agents
(Idea Generation, Roadmap, Market Footprint) changed.

No real network calls, no real sleeps. Wired into the REAL (unmodified)
ProviderChain/BraveSearchProvider/GroqProvider/ReviewPipeline classes
wherever practical so their own deadline-clamping/skip logic runs for real.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.market_needs_agent import (  # noqa: E402
    MarketNeedsAgent,
    WRITER_DEADLINE_EXCEEDED,
)
from app.models.market_needs_models import MarketNeedsRequest  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import market_needs_router  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    BraveSearchProvider,
    GroqProvider,
    LLMResult,
    ProviderChain,
)


def _request(use_search: bool = True) -> MarketNeedsRequest:
    return MarketNeedsRequest(
        projectTitle="University Thesis Plagiarism Analysis Platform",
        problemStatement=(
            "Academic departments in Lebanon struggle to detect duplicate "
            "research proposals and semantic text similarity across theses."
        ),
        targetUsers="University professors and academic committees",
        domain="Web Development",
        technologies="Python, scikit-learn, PostgreSQL",
        countryContext="Lebanon",
        useSearch=use_search,
    )


def _generate_payload() -> dict:
    return {
        "scoreBreakdown": {
            "problemEvidence": 70, "marketFit": 65, "universityValue": 80,
            "competitionOpportunity": 60, "technologyMomentum": 55,
        },
        "targetSector": "Academic Integrity",
        "problemEvidence": ["Universities report rising plagiarism cases."],
        "similarSolutions": [],
        "lebaneseMarketFit": "Strong fit for Lebanese universities.",
        "universityValue": "High research and operational value.",
        "risks": [],
        "recommendation": "Proceed with a pilot.",
        "nextSteps": [],
    }


def _failed_search_result() -> LLMResult:
    return LLMResult(
        ok=False, provider="none", model=None, text="", data=None,
        error="no search in this test", search_used=False, search_failed=True,
    )


def _successful_search_result() -> LLMResult:
    return LLMResult(
        ok=True, provider="brave", model="brave-llm-context", text="", data=None,
        search_used=True, search_failed=False,
        sources=[{"title": "World Bank report", "url": "https://worldbank.org/x", "snippet": "s"}],
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


def _agent_with_fakes(*, search_provider, generation_provider) -> MarketNeedsAgent:
    agent = MarketNeedsAgent()
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
        return SimpleNamespace(source="fallback", sources=[])

    def generate_candidate_from_result(self, result):
        return None

    def build_safe_fallback(self, request):
        return SimpleNamespace(model_dump=lambda: {"targetSector": "x", "recommendation": "y"})


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

        with patch.object(market_needs_router, "MarketNeedsAgent", return_value=agent_instance), \
             patch.object(market_needs_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(market_needs_router, "build_review_response", return_value={}), \
             patch.object(market_needs_router, "MarketNeedsResponse") as MockResponse:
            MockResponse.model_validate.return_value = SimpleNamespace(review=None)
            asyncio.run(market_needs_router.analyze_market_needs(_request()))

        pipeline = captured_pipeline[0]

        # Test 1: exactly one global deadline, from the real registry budget.
        self.assertIsNotNone(pipeline.captured_global_deadline)
        self.assertEqual(pipeline.config.max_total_seconds, 120.0)

        # Test 3: the global deadline reached ReviewPipeline.run().
        self.assertIsNotNone(pipeline.captured_global_deadline)

        # Test 4: the Writer deadline reached MarketNeedsAgent._analyze_sync.
        self.assertNotEqual(agent_instance.captured_writer_deadline, "not called")
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        # Test 2: writer_deadline < global_deadline by exactly the reserve.
        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, market_needs_router._WRITER_TIME_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 5: search receives the exact same Writer deadline.
# ---------------------------------------------------------------------------


class SearchReceivesWriterDeadlineTests(unittest.TestCase):
    def test_search_web_receives_the_same_deadline_passed_into_analyze_sync(self):
        agent = MarketNeedsAgent()
        captured: dict = {}

        def capturing_search_web(query, *, deadline=None):
            captured["deadline"] = deadline
            return _failed_search_result()

        agent.chain.search_web = capturing_search_web
        agent.chain.generate_json = lambda *a, **kw: LLMResult(
            ok=True, provider="fake", model="m", text="", data=_generate_payload(),
        )

        writer_deadline = time.monotonic() + 90.0
        agent._analyze_sync(_request(), deadline=writer_deadline)

        self.assertIn("deadline", captured)
        self.assertEqual(captured["deadline"], writer_deadline)


# ---------------------------------------------------------------------------
# Tests 6, 7, 8, 9: Brave/Groq search timeout clamping + Groq deadline-bound
# retry disabling, reusing the exact shared provider-level implementation
# added for Idea Generation (not agent-specific -- proven here again for
# this agent's own required test list).
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
        # Test 8: deadline-bound retries are disabled.
        self.assertEqual(captured["max_retries_override"], 0)

    def test_groq_retries_remain_unchanged_without_a_deadline(self):
        provider = GroqProvider()
        provider.enabled = True
        captured: dict = {}

        def fake_client(timeout_override=None, *, max_retries_override=None):
            captured["timeout_override"] = timeout_override
            captured["max_retries_override"] = max_retries_override

            class _Client:
                class chat:
                    class completions:
                        @staticmethod
                        def create(**kwargs):
                            raise RuntimeError("stop")

            return _Client()

        provider._client = fake_client
        provider.search_web("query")  # no writer_budget_seconds -- today's exact behavior

        self.assertIsNone(captured["timeout_override"])
        self.assertIsNone(captured["max_retries_override"])


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
# Test 11: optional search failure behavior unchanged -- Market Demand's own
# contract (search is optional, a failure never blocks generation, and it
# is reflected as low/ungrounded confidence, never fatal).
# ---------------------------------------------------------------------------


class OptionalSearchContractPreservedTests(unittest.TestCase):
    def test_generation_runs_and_result_is_ungrounded_when_search_fails(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        deadline = time.monotonic() + 90.0
        response = agent._analyze_sync(_request(), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 1)
        self.assertNotEqual(response.source, "fallback")
        self.assertFalse(response.grounded_in_live_data)
        self.assertIsNone(agent.last_fallback_reason_code)

    def test_search_failure_without_any_deadline_matches_prior_behavior(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        response = agent._analyze_sync(_request())  # no deadline at all

        self.assertEqual(generation_provider.call_count, 1)
        self.assertNotEqual(response.source, "fallback")
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Tests 12, 14, 15: search and generation share ONE Writer budget; generation
# receives the exact deadline and a correctly clamped remaining time.
# ---------------------------------------------------------------------------


class SharedWriterBudgetTests(unittest.TestCase):
    def test_generation_receives_reduced_remaining_time_after_a_slow_search(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=True)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

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
            agent._analyze_sync(_request(), deadline=deadline)

        self.assertEqual(search_provider.call_count, 1)
        self.assertEqual(generation_provider.call_count, 1)
        # Test 14: generation received the deadline (via writer_budget_seconds,
        # the real per-attempt figure the shared cascade computes from it).
        self.assertIsInstance(generation_provider.received_writer_budget_seconds, float)
        # Test 15: correctly clamped remaining time (~70s = 90 - 20), not a
        # fresh ~90s budget.
        self.assertAlmostEqual(generation_provider.received_writer_budget_seconds, 70.0, delta=1.0)


# ---------------------------------------------------------------------------
# Tests 13, 17, 18: generation skipped after search exhausts the budget,
# with a typed reason that is never mislabeled as a schema failure.
# ---------------------------------------------------------------------------


class GenerationSkippedAfterSearchExhaustsBudgetTests(unittest.TestCase):
    def test_generation_is_not_entered_and_typed_reason_is_set(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

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
            response = agent._analyze_sync(_request(), deadline=deadline)

        # Test 13: generate_json() never called.
        self.assertEqual(generation_provider.call_count, 0)
        # Test 17: exact typed reason.
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        # Deterministic fallback is still a complete response, source=="fallback".
        self.assertEqual(response.source, "fallback")

    def test_deadline_exhaustion_pipeline_status_is_provider_unavailable_never_schema_invalid(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        already_past_deadline = time.monotonic() - 1.0
        raw_result = agent._analyze_sync(_request(), deadline=already_past_deadline)

        from app.review.context import ReviewContext
        from app.review.pipeline import ReviewPipeline as RealReviewPipeline

        context = market_needs_router._build_review_context(_request())
        context.allowed_source_metadata = [s.model_dump() for s in raw_result.sources]

        reviewer = _NeverCalledReviewerAgent()
        pipeline = RealReviewPipeline("MarketNeedsAgent", reviewer_agent=reviewer)

        result = pipeline.run(
            lambda: agent.generate_candidate_from_result(raw_result),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=time.monotonic() + 120.0,
        )

        # Test 18: never mislabeled as schema_invalid.
        self.assertEqual(result.status, "provider_unavailable")
        self.assertNotEqual(result.status, "schema_invalid")
        self.assertFalse(result.usable)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        # Test 20: review never invoked without a usable Writer candidate.
        self.assertEqual(reviewer.call_count, 0)


class _NeverCalledReviewerAgent:
    def __init__(self):
        self.call_count = 0

    def analyze(self, candidate, context, **kwargs):
        self.call_count += 1
        raise AssertionError("reviewer must not be called after a Writer-deadline failure")


# ---------------------------------------------------------------------------
# Test 16: fallback generation provider skipped below the shared threshold.
# ---------------------------------------------------------------------------


class GenerationFallbackSkippingTests(unittest.TestCase):
    def test_second_generation_provider_skipped_when_first_attempt_exhausts_budget(self):
        first = _FakeGenerationProvider("fake-first", ok=False)
        second = _FakeGenerationProvider("fake-second", ok=True)
        agent = _agent_with_fakes(
            search_provider=_FakeSearchProvider("fake-brave", ok=False), generation_provider=first,
        )
        agent.chain = ProviderChain(providers=[first, second], search_providers=[_FakeSearchProvider("fake-brave", ok=False)])

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
            agent._analyze_sync(_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)


# ---------------------------------------------------------------------------
# Test 19: semantic review receives its reserved time when the Writer
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

        global_deadline = time.monotonic() + 120.0
        writer_deadline = global_deadline - market_needs_router._WRITER_TIME_RESERVE_SECONDS
        raw_result = agent._analyze_sync(_request(), deadline=writer_deadline)

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("MarketNeedsAgent", reviewer_agent=reviewer)

        context = market_needs_router._build_review_context(_request())
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
# Test 21: cancellation propagates honestly, never converted into a result.
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
            agent._analyze_sync(_request(), deadline=time.monotonic() + 90.0)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertIsNone(agent.last_fallback_reason_code)

    def test_cancelled_error_from_generation_propagates(self):
        class _CancellingGenerationProvider:
            name = "cancelling-generation"

            def generate_json(self, *args, **kwargs):
                raise asyncio.CancelledError()

        agent = _agent_with_fakes(
            search_provider=_FakeSearchProvider("fake-brave", ok=False),
            generation_provider=_CancellingGenerationProvider(),
        )

        with self.assertRaises(asyncio.CancelledError):
            agent._analyze_sync(_request(), deadline=time.monotonic() + 90.0)

        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 22: successful output unchanged (fast search + fast generation).
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
        response_b = agent_b._analyze_sync(request, deadline=time.monotonic() + 120.0)

        # Excludes analyzed_at -- always differs by a few microseconds
        # between the two independent datetime.now() calls, unrelated to
        # this task's deadline changes.
        dump_a = response_a.model_dump()
        dump_b = response_b.model_dump()
        # MarketNeedsResponse (CamelModel) sets serialize_by_alias=True, so
        # model_dump() keys are the camelCase aliases ("analyzedAt"), not
        # the snake_case field names -- popping the wrong key silently left
        # this comparing two live, always-differing timestamps.
        dump_a.pop("analyzedAt", None)
        dump_b.pop("analyzedAt", None)
        self.assertEqual(dump_a, dump_b)
        self.assertTrue(response_a.grounded_in_live_data)
        self.assertTrue(response_b.grounded_in_live_data)


# ---------------------------------------------------------------------------
# Test 23: provider order unchanged.
# ---------------------------------------------------------------------------


class ProviderOrderUnchangedTests(unittest.TestCase):
    def test_default_search_and_generation_provider_order_is_unchanged(self):
        chain = ProviderChain()
        search_names = [type(p).__name__ for p in chain.search_providers]
        generation_names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(search_names, ["BraveSearchProvider", "GroqProvider"])
        self.assertEqual(generation_names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])


# ---------------------------------------------------------------------------
# Test 24: retry counts unchanged, except the already-established
# deadline-bound Groq search override.
# ---------------------------------------------------------------------------


class RetryCountsUnchangedTests(unittest.TestCase):
    def test_no_extra_generation_attempts_are_added_beyond_the_configured_providers(self):
        first = _FakeGenerationProvider("fake-first", ok=False)
        second = _FakeGenerationProvider("fake-second", ok=False)
        agent = MarketNeedsAgent()
        agent.chain = ProviderChain(providers=[first, second], search_providers=[_FakeSearchProvider("fake-brave", ok=False)])

        agent._analyze_sync(_request(), deadline=time.monotonic() + 120.0)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)

    def test_groq_generation_max_retries_unaffected_by_this_task(self):
        # This task only forces max_retries_override=0 for GroqProvider's
        # SEARCH leg when deadline-bound (see BraveGroqSearchClampTests) --
        # generation's own GroqProvider instance/retry config is untouched.
        provider = GroqProvider(max_retries=2)
        self.assertEqual(provider.max_retries, 2)


# ---------------------------------------------------------------------------
# Test 25: legacy fakes without a `deadline` parameter continue to work.
# ---------------------------------------------------------------------------


class LegacyFakeCompatibilityTests(unittest.TestCase):
    def test_old_style_search_web_and_generate_json_without_deadline_still_work(self):
        class _StrictLegacyChain:
            """Mirrors the exact fake shapes used by
            test_market_needs_agent_search.py: `lambda q: ...` for
            search_web, `def fake_generate_json(prompt, *, use_search=False,
            max_tokens=None)` for generate_json -- neither accepts
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

        agent = MarketNeedsAgent()
        agent.chain = _StrictLegacyChain(
            _failed_search_result(),
            LLMResult(ok=True, provider="fake", model="fake-model", text="", data=_generate_payload()),
        )

        # A deadline IS supplied, but neither fake method accepts it --
        # must not raise TypeError, and must not silently drop the real
        # candidate either.
        response = agent._analyze_sync(_request(), deadline=time.monotonic() + 120.0)

        self.assertEqual(len(agent.chain.search_calls), 1)
        self.assertEqual(len(agent.chain.generate_calls), 1)
        self.assertNotEqual(response.source, "fallback")


# ---------------------------------------------------------------------------
# Test 28: Market Footprint remains completely unchanged by this task.
# ---------------------------------------------------------------------------


class MarketFootprintUnaffectedTests(unittest.TestCase):
    def test_defense_simulator_orchestrator_now_threads_a_deadline(self):
        # MarketFootprintAgent is no longer a valid "unaffected agent"
        # example -- a separate task (see
        # tests/test_market_footprint_writer_deadline.py) gave it the
        # identical deadline/writer_deadline propagation this test file is
        # proving for Market Demand. Defense Simulator was ALSO a genuinely
        # untouched agent when this assertion was first written -- a
        # separate, later freeze-audit fix (FYP-016) closed that gap: its
        # Writer stage previously received no deadline at all (a
        # zero-argument lambda around a zero-argument agent method), so a
        # DeepInfra/Groq retry storm on the "light" tier could silently
        # consume the entire pipeline budget with no deadline-aware
        # safeguard ever engaging. This now asserts the fixed, intentional
        # state instead of the absence that used to be true.
        import inspect as _inspect
        from app.agents.defense_simulator.defense_simulator_orchestrator import (
            DefenseSimulatorOrchestrator,
        )

        sig = _inspect.signature(DefenseSimulatorOrchestrator.generate_questions_candidate)
        self.assertIn("deadline", sig.parameters)

    def test_defense_simulator_router_now_has_a_writer_deadline_reserve_constant(self):
        # See test_defense_simulator_orchestrator_now_threads_a_deadline
        # above for why this flipped from assertFalse to assertTrue.
        from app.routers import defense_simulator as defense_simulator_router

        self.assertTrue(hasattr(defense_simulator_router, "_WRITER_TIME_RESERVE_SECONDS"))


if __name__ == "__main__":
    unittest.main()
