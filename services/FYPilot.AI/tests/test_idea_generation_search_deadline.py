"""
Tests for Idea Generation SEARCH-STAGE absolute-deadline propagation.

Follow-up to test_idea_generation_writer_deadline.py, which propagated the
Writer deadline into the structured-JSON generation call
(provider_chain.generate_json()) but left the pre-generation search/
enrichment call (provider_chain.search_web()) completely deadline-unaware --
confirmed by inspection: every test in that file stubs search_web with a
fail-fast fake and never asserts what (if anything) reaches it. A slow or
retrying search leg could alone consume the entire Writer budget before
generation ever started, leaving generate_json() correctly clamped but with
no useful time left.

This file proves: (1) the SAME absolute Writer deadline used by generate_json
now also reaches search_web(), (2) each search provider's own configured
timeout is clamped to whatever budget remains, (3) a search provider is
skipped once too little time remains (reusing the shared
_MIN_SECONDS_PER_PROVIDER_ATTEMPT floor, never a new threshold), (4) Groq's
own SDK-level retries cannot multiply an already-clamped timeout past the
remaining budget, (5) generation re-checks the remaining budget BEFORE
entering its own provider cascade rather than relying only on that cascade
to discover exhaustion, (6) search and generation consume ONE shared budget
(the deadline is never reset or recomputed), and (7) the existing optional-
search contract (search failure is never fatal) is preserved exactly.

Honest limitation (do not overstate elsewhere): Brave/Groq's search_web
calls are synchronous, blocking network calls. Nothing in this codebase (or
this task) makes an in-flight `requests.post`/groq-SDK call abortable
mid-flight. Cancellation (asyncio.CancelledError) is only ever OBSERVED
before such a call starts or after it raises/returns -- never used to
interrupt one that is already running. The cancellation tests below test
exactly that: propagation before/after a call, never mid-call interruption.

No real network calls, no real sleeps. Wired into the REAL (unmodified)
ProviderChain/BraveSearchProvider/GroqProvider classes wherever practical so
their own deadline-clamping/skip logic runs for real; fakes are used only
for the actual network boundary (requests.post / the groq SDK client).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_idea_agent import (  # noqa: E402
    IDEAS_PER_BATCH,
    ProjectIdeaAgent,
    StudentProfile,
    WRITER_DEADLINE_EXCEEDED,
)
from app.services.llm_provider import (  # noqa: E402
    BraveSearchProvider,
    GroqProvider,
    LLMResult,
    ProviderChain,
)


def _profile() -> StudentProfile:
    return StudentProfile(
        studentSkills=["Python", "C#"],
        skillRatings={"Python": 3, "C#": 2},
        major="Computer Science",
        experienceLevel=2,
        preferredDomain="Web Development",
        targetDifficulty=3,
        availableHoursPerWeek=10,
        teamSize=2,
        projectGoals=["Build something useful for Lebanon"],
    )


def _valid_ideas_payload() -> dict:
    return {
        "ideas": [
            {"title": f"Test Idea {i}", "problemStatement": "A concrete problem statement."}
            for i in range(1, 5)
        ]
    }


def _failed_search_result() -> LLMResult:
    return LLMResult(
        ok=False, provider="none", model=None, text="", data=None,
        error="no search in this test", search_used=False, search_failed=True,
    )


def _successful_search_result(sources: list[dict[str, str]] | None = None) -> LLMResult:
    return LLMResult(
        ok=True, provider="brave", model="brave-llm-context", text="", data=None,
        search_used=True, search_failed=False,
        sources=sources or [{"title": "Source", "url": "https://worldbank.org/x", "snippet": "s"}],
    )


class _FakeGenerationProvider:
    """Stands in for a generation-chain BaseProvider inside a REAL
    ProviderChain -- mirrors test_idea_generation_writer_deadline.py's
    _FakeProvider."""

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
            return LLMResult(
                ok=True, provider=self.name, model="fake-model", text="",
                data=_valid_ideas_payload(),
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake generation provider failure", error_category="provider_http_error",
        )


class _FakeSearchProvider:
    """Stands in for a real search-chain BaseProvider (BraveSearchProvider/
    GroqProvider) inside a REAL ProviderChain -- so ProviderChain.search_web's
    own (unmodified in effect, shared) deadline/skip logic runs for real."""

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


def _agent_with_fakes(
    *, search_provider, generation_provider,
) -> ProjectIdeaAgent:
    agent = ProjectIdeaAgent()
    agent.provider_chain = ProviderChain(
        providers=[generation_provider],
        search_providers=[search_provider],
    )
    return agent


# ---------------------------------------------------------------------------
# Test 1: search receives the Writer deadline.
# ---------------------------------------------------------------------------


class SearchReceivesWriterDeadlineTests(unittest.TestCase):
    def test_search_web_receives_the_same_deadline_passed_into_generate_ideas(self):
        agent = ProjectIdeaAgent()
        captured: dict = {}

        def capturing_search_web(query, *, deadline=None):
            captured["deadline"] = deadline
            return _failed_search_result()

        agent.provider_chain.search_web = capturing_search_web
        agent.provider_chain.generate_json = lambda *a, **kw: LLMResult(
            ok=True, provider="fake", model="m", text="", data=_valid_ideas_payload(),
        )

        writer_deadline = time.monotonic() + 90.0
        agent.generate_ideas(_profile(), deadline=writer_deadline)

        self.assertIn("deadline", captured)
        self.assertEqual(captured["deadline"], writer_deadline)

    def test_no_deadline_means_search_receives_no_deadline(self):
        agent = ProjectIdeaAgent()
        captured: dict = {}

        def capturing_search_web(query, *, deadline=None):
            captured["deadline"] = deadline
            return _failed_search_result()

        agent.provider_chain.search_web = capturing_search_web
        agent.provider_chain.generate_json = lambda *a, **kw: LLMResult(
            ok=True, provider="fake", model="m", text="", data=_valid_ideas_payload(),
        )

        agent.generate_ideas(_profile())  # no deadline at all

        self.assertIn("deadline", captured)
        self.assertIsNone(captured["deadline"])


# ---------------------------------------------------------------------------
# Tests 2, 3: search provider timeout clamping (both directions).
# ---------------------------------------------------------------------------


class BraveSearchTimeoutClampTests(unittest.TestCase):
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

    def test_brave_configured_timeout_remains_authoritative_when_smaller(self):
        provider = BraveSearchProvider()
        provider.enabled = True
        provider.api_key = "fake-key"
        provider.timeout_seconds = 10.0
        captured: dict = {}

        def fake_post(url, *, headers, json, timeout):
            captured["timeout"] = timeout
            import requests
            raise requests.exceptions.Timeout()

        with patch("app.services.llm_provider.requests.post", side_effect=fake_post):
            provider.search_web("query", writer_budget_seconds=500.0)

        self.assertEqual(captured["timeout"], 10.0)

    def test_brave_no_writer_budget_preserves_configured_timeout(self):
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
            provider.search_web("query")  # no writer_budget_seconds -- today's exact behavior

        self.assertEqual(captured["timeout"], 30.0)


class GroqSearchTimeoutAndRetryTests(unittest.TestCase):
    """Correction applied: a supplied deadline must ALSO force single-attempt
    semantics (max_retries=0) for this one search call -- otherwise the groq
    SDK's own default retry-with-backoff (max_retries=2) could re-attempt an
    already-clamped timeout up to 3 times, multiplying effective latency to
    roughly 3x the remaining Writer budget instead of bounding it."""

    def _capture_client_args(self, provider: GroqProvider) -> dict:
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
        return captured

    def test_groq_timeout_is_clamped_when_remaining_budget_is_smaller(self):
        provider = GroqProvider()
        provider.enabled = True
        provider.timeout_seconds = 60.0
        captured = self._capture_client_args(provider)

        provider.search_web("query", writer_budget_seconds=7.0)

        self.assertEqual(captured["timeout_override"], 7.0)

    def test_groq_configured_timeout_remains_authoritative_when_smaller(self):
        provider = GroqProvider()
        provider.enabled = True
        provider.timeout_seconds = 6.0
        captured = self._capture_client_args(provider)

        provider.search_web("query", writer_budget_seconds=500.0)

        self.assertEqual(captured["timeout_override"], 6.0)

    def test_groq_retries_are_disabled_when_a_deadline_is_involved(self):
        provider = GroqProvider(max_retries=None)  # SDK default (2) would otherwise apply
        provider.enabled = True
        captured = self._capture_client_args(provider)

        provider.search_web("query", writer_budget_seconds=12.0)

        self.assertEqual(captured["max_retries_override"], 0)

    def test_groq_no_deadline_preserves_existing_retry_and_timeout_behavior(self):
        provider = GroqProvider()
        provider.enabled = True
        captured = self._capture_client_args(provider)

        provider.search_web("query")  # no writer_budget_seconds at all -- today's exact behavior

        self.assertIsNone(captured["timeout_override"])
        self.assertIsNone(captured["max_retries_override"])


# ---------------------------------------------------------------------------
# Test 4: search provider skipped once too little time remains.
# ---------------------------------------------------------------------------


class SearchProviderSkippingTests(unittest.TestCase):
    def test_search_provider_not_started_when_deadline_already_exhausted(self):
        provider = _FakeSearchProvider("fake-brave")
        chain = ProviderChain(providers=[], search_providers=[provider])

        deadline = time.monotonic() - 1.0
        result = chain.search_web("query", deadline=deadline)

        self.assertEqual(provider.call_count, 0)
        self.assertFalse(result.ok)

    def test_second_search_provider_skipped_when_first_attempt_exhausts_budget(self):
        first = _FakeSearchProvider("fake-brave", ok=False)
        second = _FakeSearchProvider("fake-groq", ok=True)
        chain = ProviderChain(providers=[], search_providers=[first, second])

        real_now = time.monotonic()
        deadline = real_now + 10.0
        elapsed_seconds = {"value": 0.0}
        real_first_search = first.search_web

        def first_search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 8.0
            return real_first_search(*args, **kwargs)

        first.search_web = first_search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            chain.search_web("query", deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)

    def test_second_search_provider_attempted_when_enough_time_remains(self):
        first = _FakeSearchProvider("fake-brave", ok=False)
        second = _FakeSearchProvider("fake-groq", ok=True)
        chain = ProviderChain(providers=[], search_providers=[first, second])

        deadline = time.monotonic() + 60.0
        result = chain.search_web("query", deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)
        self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# Test 5, 9: optional-search contract preserved -- search failure (for any
# reason, deadline-related or not) never blocks generation while enough
# Writer time remains.
# ---------------------------------------------------------------------------


class OptionalSearchContractPreservedTests(unittest.TestCase):
    def test_generation_runs_with_empty_evidence_when_search_fails_and_time_remains(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        deadline = time.monotonic() + 90.0
        ideas = agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 1)
        self.assertTrue(agent.last_llm_used)
        self.assertFalse(agent.last_search_used)
        self.assertTrue(agent.last_search_failed)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(ideas), IDEAS_PER_BATCH)

    def test_search_failure_without_any_deadline_matches_prior_behavior(self):
        """No deadline at all -- proves this task changed nothing about the
        pre-existing optional-search contract for callers that don't opt in."""
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        ideas = agent.generate_ideas(_profile())  # no deadline

        self.assertEqual(generation_provider.call_count, 1)
        self.assertTrue(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(ideas), IDEAS_PER_BATCH)


# ---------------------------------------------------------------------------
# Tests 6, 8: search and generation share ONE Writer budget -- the deadline
# is never reset or recomputed between stages, regardless of whether search
# succeeds slowly or fails.
# ---------------------------------------------------------------------------


class SharedWriterBudgetTests(unittest.TestCase):
    def test_generation_receives_reduced_remaining_time_after_a_slow_successful_search(self):
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
            agent.generate_ideas(_profile(), deadline=deadline)

        # The deadline itself is an absolute value -- unchanged. What
        # naturally shrinks is the REMAINING time computed against it.
        self.assertEqual(search_provider.call_count, 1)
        self.assertEqual(generation_provider.call_count, 1)
        self.assertIsInstance(generation_provider.received_writer_budget_seconds, float)
        self.assertAlmostEqual(generation_provider.received_writer_budget_seconds, 70.0, delta=1.0)

    def test_search_failure_does_not_reset_the_budget_for_generation(self):
        search_provider = _FakeSearchProvider("fake-brave", ok=False)
        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(search_provider=search_provider, generation_provider=generation_provider)

        real_now = time.monotonic()
        deadline = real_now + 90.0
        elapsed_seconds = {"value": 0.0}
        real_search = search_provider.search_web

        def search_and_advance_clock(*args, **kwargs):
            elapsed_seconds["value"] = 25.0  # simulate a slow timeout/failure
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.generate_ideas(_profile(), deadline=deadline)

        # Generation must see ~65s remaining (90 - 25), NOT a fresh ~90s --
        # proving the search failure did not reset/recompute the budget.
        self.assertIsInstance(generation_provider.received_writer_budget_seconds, float)
        self.assertAlmostEqual(generation_provider.received_writer_budget_seconds, 65.0, delta=1.0)
        self.assertLess(generation_provider.received_writer_budget_seconds, 90.0)


# ---------------------------------------------------------------------------
# Test 7: generation does not start when search leaves insufficient time.
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
            # Search alone consumes 87s of the 90s budget, leaving only 3s --
            # below _MIN_SECONDS_PER_PROVIDER_ATTEMPT (4.0s).
            elapsed_seconds["value"] = 87.0
            return real_search(*args, **kwargs)

        search_provider.search_web = search_and_advance_clock

        def fake_monotonic():
            return real_now + elapsed_seconds["value"]

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(generation_provider.call_count, 0)
        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)


# ---------------------------------------------------------------------------
# Test 10: cancellation during search propagates honestly.
#
# See module docstring's "Honest limitation" section -- these tests prove
# propagation of an already-raised CancelledError, never mid-request
# interruption of a live blocking call (which this task does not add and
# does not claim to add).
# ---------------------------------------------------------------------------


class SearchCancellationTests(unittest.TestCase):
    def test_cancelled_error_from_search_propagates_and_generation_is_never_called(self):
        class _CancellingSearchProvider:
            name = "cancelling-search"

            def search_web(self, *args, **kwargs):
                # Represents CancelledError observed immediately after this
                # (already-completed or never-started) call -- not an
                # in-flight network request being aborted mid-flight.
                raise asyncio.CancelledError()

        generation_provider = _FakeGenerationProvider(ok=True)
        agent = _agent_with_fakes(
            search_provider=_CancellingSearchProvider(), generation_provider=generation_provider,
        )

        with self.assertRaises(asyncio.CancelledError):
            agent.generate_ideas(_profile(), deadline=time.monotonic() + 90.0)

        self.assertEqual(generation_provider.call_count, 0)
        # CancelledError is a BaseException (not Exception), so it must
        # never be caught and reclassified as a Writer-deadline failure.
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 11: a legacy search fake without a `deadline` parameter keeps working.
# ---------------------------------------------------------------------------


class LegacySearchFakeCompatibilityTests(unittest.TestCase):
    def test_old_style_search_web_without_deadline_support_still_works(self):
        class _StrictLegacySearchChain:
            """Mirrors the exact fake shape used by
            test_idea_generation_knowledge_base.py's
            _generate_ideas_via_router helper (patched at the ProviderChain
            class level: `def fake_search_web(self, query):`) -- no
            `deadline`, no `**kwargs`."""

            def __init__(self, search_result, generate_result):
                self.search_result = search_result
                self.generate_result = generate_result
                self.search_calls: list = []

            def search_web(self, query):
                self.search_calls.append(query)
                return self.search_result

            def generate_json(self, prompt, *, use_search=False, max_tokens=None):
                return self.generate_result

        agent = ProjectIdeaAgent()
        agent.provider_chain = _StrictLegacySearchChain(
            _failed_search_result(),
            LLMResult(ok=True, provider="fake", model="fake-model", text="", data=_valid_ideas_payload()),
        )

        # A deadline IS supplied, but the fake's search_web signature has no
        # deadline parameter at all -- must not raise TypeError, and must
        # not silently drop the real candidate either.
        ideas = agent.generate_ideas(_profile(), deadline=time.monotonic() + 120.0)

        self.assertEqual(len(agent.provider_chain.search_calls), 1)
        self.assertTrue(agent.last_llm_used)
        self.assertEqual(len(ideas), IDEAS_PER_BATCH)


# ---------------------------------------------------------------------------
# Test 13: successful output unchanged (fast search + fast generation).
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

        profile = _profile()
        ideas_a = agent_a.generate_ideas(profile)
        ideas_b = agent_b.generate_ideas(profile, deadline=time.monotonic() + 120.0)

        self.assertEqual(
            [idea.model_dump() for idea in ideas_a],
            [idea.model_dump() for idea in ideas_b],
        )
        self.assertTrue(agent_a.last_llm_used)
        self.assertTrue(agent_b.last_llm_used)
        self.assertTrue(agent_a.last_search_used)
        self.assertTrue(agent_b.last_search_used)


# ---------------------------------------------------------------------------
# Test 14: search and generation provider ordering is unchanged.
# ---------------------------------------------------------------------------


class ProviderOrderUnchangedTests(unittest.TestCase):
    def test_default_search_provider_order_is_unchanged(self):
        chain = ProviderChain(tier="high")
        names = [type(p).__name__ for p in chain.search_providers]
        self.assertEqual(names, ["BraveSearchProvider", "GroqProvider"])

    def test_default_generation_provider_order_is_unchanged(self):
        chain = ProviderChain(tier="high")
        names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])


if __name__ == "__main__":
    unittest.main()
