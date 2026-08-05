"""
Tests for Idea Generation Writer/ReviewPipeline absolute-deadline
propagation.

Root cause this covers: ProjectIdeaAgent's Writer stage (the structured
idea-JSON provider_chain.generate_json() call inside generate_ideas(), plus
an emergency direct-Ollama leg that bypasses ProviderChain entirely) had NO
deadline at all -- a slow-but-successful DeepInfra/Groq/Ollama call, or the
600s-hardcoded direct-Ollama fallback, could alone exceed ReviewPipeline's
entire 120s total budget (see app/review/registry.py's
ProjectIdeaAgent.max_total_seconds), so a valid, successfully generated
candidate could arrive only for ReviewPipeline.run's own
_time_budget_exceeded check to discard it before the Reviewer ever ran. This
mirrors Roadmap's identical fix (see test_roadmap_writer_deadline.py) --
same architecture, same shared ProviderChain deadline-clamping/skip logic,
adapted to Idea Generation's own registry budget and two-call (search +
generate) Writer stage.

No real network calls, no real sleeps -- fake providers/reviewer agents
only, wired into the REAL (unmodified) ProviderChain/ReviewPipeline so their
own deadline-clamping/skip logic runs for real. Deadlines use the REAL
time.monotonic() clock with small offsets; since every fake call returns
instantly (no I/O, no sleep), the elapsed wall-clock time between computing
a deadline and consuming it is negligible, keeping this fast and
deterministic without needing to mock the clock itself.

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

from app.agents.project_idea_agent import (  # noqa: E402
    IDEAS_PER_BATCH,
    ProjectIdeaAgent,
    StudentProfile,
    WRITER_DEADLINE_EXCEEDED,
)
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import ideas as ideas_router  # noqa: E402
from app.services.llm_provider import DeepInfraProvider, LLMResult, ProviderChain  # noqa: E402


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


def _agent_with_fake_providers(*providers) -> ProjectIdeaAgent:
    """
    Real ProjectIdeaAgent wired to a REAL ProviderChain built from the given
    fake providers (generation chain only), so ProviderChain's own
    (unmodified, shared) _run_cascade deadline/skip logic runs for real.
    search_web is stubbed to fail fast -- no real Brave/Groq network calls,
    matching this repo's existing test convention (see
    test_project_idea_agent_web_search_firewall.py).
    """
    agent = ProjectIdeaAgent()
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
                data=_valid_ideas_payload(),
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake provider failure", error_category="provider_http_error",
        )


# ---------------------------------------------------------------------------
# Tests 1, 2, 3: router creates one global deadline, a strictly shorter
# writer deadline (by exactly the configured reserve), and forwards it.
# ---------------------------------------------------------------------------


class _CapturingAgent:
    """Records the deadline the router forwards to generate_candidate,
    without making any real call."""

    def __init__(self):
        self.last_llm_used = False
        self.last_provider = None
        self.last_model_used = None
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_sources: list = []
        self.last_fallback_reason_code: str | None = None
        self.captured_writer_deadline: float | None | str = "not called"

    def generate_candidate(self, profile, *, deadline=None):
        self.captured_writer_deadline = deadline
        return None

    def build_safe_fallback(self, profile):
        return {"ideas": []}


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

        with patch.object(ideas_router, "ProjectIdeaAgent", return_value=agent_instance), \
             patch.object(ideas_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(ideas_router, "build_review_response", return_value={}), \
             patch.object(ideas_router, "_response_to_dict", return_value={}):
            ideas_router.generate_ideas({})

        pipeline = captured_pipeline[0]

        # Test 1: exactly one global deadline was created and reached run().
        self.assertIsNotNone(pipeline.captured_global_deadline)

        # Test 3: the route passed a writer deadline to the agent.
        self.assertNotEqual(agent_instance.captured_writer_deadline, "not called")
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        # Test 2: writer_deadline < global_deadline by exactly the reserve.
        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, ideas_router._WRITER_TIME_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        # The Writer's own deadline must leave it LESS than the full
        # registry budget -- it can never be told it owns the whole thing.
        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 4, 5: the deadline reaches ProviderChain.generate_json() and clamps
# the remaining budget forwarded to each provider attempt.
# ---------------------------------------------------------------------------


class ProviderDeadlinePropagationTests(unittest.TestCase):
    def test_provider_receives_writer_budget_seconds_no_greater_than_remaining_deadline(self):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        deadline = time.monotonic() + 12.0
        agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(provider.call_count, 1)
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 12.0)
        self.assertGreater(provider.received_writer_budget_seconds, 12.0 - 2.0)  # test-overhead margin

    def test_no_deadline_means_no_writer_budget_forwarded(self):
        provider = _FakeProvider("fake-deepinfra")
        agent = _agent_with_fake_providers(provider)

        agent.generate_ideas(_profile())

        self.assertEqual(provider.call_count, 1)
        self.assertIsNone(provider.received_writer_budget_seconds)


class SharedProviderTimeoutClampTests(unittest.TestCase):
    """
    Direct tests of DeepInfraProvider's own EXISTING, SHARED timeout clamp
    (effective_timeout = min(configured, writer_budget_seconds)) --
    unmodified by this task, already relied on by Roadmap/SE Documentation.
    ProviderDeadlinePropagationTests above proves the VALUE reaching this
    clamp is correct; these prove the clamp itself picks the right winner in
    both directions (Tests 5 and 6).
    """

    def _provider_with_captured_timeout(self, *, configured_timeout: float) -> tuple[DeepInfraProvider, dict]:
        provider = DeepInfraProvider(model="fake-model", timeout_seconds=configured_timeout)
        provider.enabled = True  # bypass the DEEPINFRA_API_KEY env check
        captured: dict = {}

        def fake_chat_completion_text(*, messages, temperature, max_tokens, use_json_mode, timeout_override=None):
            captured["timeout_override"] = timeout_override
            return "{}", "stop"

        provider._chat_completion_text = fake_chat_completion_text
        return provider, captured

    def test_provider_timeout_is_clamped_when_remaining_budget_is_smaller(self):
        provider, captured = self._provider_with_captured_timeout(configured_timeout=180.0)
        provider.generate_json("prompt", writer_budget_seconds=12.0)
        self.assertEqual(captured["timeout_override"], 12.0)

    def test_configured_timeout_remains_authoritative_when_smaller_than_remaining_budget(self):
        provider, captured = self._provider_with_captured_timeout(configured_timeout=45.0)
        provider.generate_json("prompt", writer_budget_seconds=500.0)
        self.assertEqual(captured["timeout_override"], 45.0)


# ---------------------------------------------------------------------------
# Tests 7, 8: fallback provider skipped/attempted based on remaining budget.
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
        # ProjectIdeaAgent's own time.monotonic() calls (its logging lines,
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
            agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)
        self.assertFalse(agent.last_llm_used)

    def test_second_provider_is_attempted_when_enough_time_remains(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = _agent_with_fake_providers(first, second)

        deadline = time.monotonic() + 60.0
        agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)
        self.assertTrue(agent.last_llm_used)
        self.assertEqual(agent.last_provider, "fake-second")


# ---------------------------------------------------------------------------
# Tests 9, 10, 13: Writer deadline exhaustion has a typed, honest reason,
# is never mislabeled as a schema failure, and never triggers review.
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


class WriterDeadlineTypedResultTests(unittest.TestCase):
    def test_deadline_already_exhausted_skips_every_provider_and_sets_typed_reason(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        agent._call_ollama = lambda *a, **kw: None  # emergency leg must also be skipped, see below

        already_past_deadline = time.monotonic() - 1.0
        started = time.monotonic()
        agent.generate_ideas(_profile(), deadline=already_past_deadline)
        elapsed = time.monotonic() - started

        self.assertEqual(provider.call_count, 0)
        self.assertLess(elapsed, 0.5)
        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)

    def test_writer_deadline_exhaustion_is_provider_unavailable_never_schema_invalid(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        agent._call_ollama = lambda *a, **kw: None

        reviewer = _FakeReviewerAgent([])  # would raise IndexError if ever invoked
        pipeline = ReviewPipeline("ProjectIdeaAgent", tier="high", reviewer_agent=reviewer)

        profile = _profile()
        context = ideas_router._build_review_context(profile)
        already_past_deadline = time.monotonic() - 1.0

        result = pipeline.run(
            lambda: agent.generate_candidate(profile, deadline=already_past_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=time.monotonic() + 120.0,
        )

        self.assertEqual(result.status, "provider_unavailable")
        self.assertNotEqual(result.status, "schema_invalid")
        self.assertFalse(result.usable)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)

        # Test 13: no review after a Writer-deadline failure -- the Reviewer
        # is never invoked when the Writer produced no usable candidate.
        self.assertEqual(len(reviewer.received_candidates), 0)


# ---------------------------------------------------------------------------
# Test 11: review receives its reserved time when the Writer finishes
# within budget.
# ---------------------------------------------------------------------------


class ReviewReceivesReservedTimeTests(unittest.TestCase):
    def test_reviewer_is_invoked_and_candidate_is_accepted(self):
        provider = _FakeProvider("fake-deepinfra", ok=True)
        agent = _agent_with_fake_providers(provider)

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("ProjectIdeaAgent", tier="high", reviewer_agent=reviewer)

        profile = _profile()
        context = ideas_router._build_review_context(profile)
        global_deadline = time.monotonic() + 120.0
        writer_deadline = global_deadline - ideas_router._WRITER_TIME_RESERVE_SECONDS

        result = pipeline.run(
            lambda: agent.generate_candidate(profile, deadline=writer_deadline),
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
# Test 12: client cancellation remains cancellation, never reclassified as
# a Writer-deadline failure.
# ---------------------------------------------------------------------------


class CancellationTests(unittest.TestCase):
    def test_cancelled_error_propagates_and_is_never_reclassified(self):
        class _CancellingProvider:
            name = "cancelling"

            def generate_json(self, *args, **kwargs):
                raise asyncio.CancelledError()

        agent = _agent_with_fake_providers(_CancellingProvider())
        deadline = time.monotonic() + 60.0

        with self.assertRaises(asyncio.CancelledError):
            agent.generate_ideas(_profile(), deadline=deadline)

        # CancelledError is a BaseException (not Exception), so it must
        # never be caught and reclassified by the Writer-deadline handling.
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test: emergency direct-Ollama leg respects the same Writer deadline
# (skip-when-exhausted / clamp-when-remaining) -- not part of ProviderChain,
# so it needs its own coverage.
# ---------------------------------------------------------------------------


class OllamaEmergencyLegDeadlineTests(unittest.TestCase):
    def test_ollama_emergency_leg_is_skipped_when_deadline_already_exhausted(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        calls: list = []
        agent._call_ollama = lambda *a, **kw: calls.append(kw.get("timeout_seconds", "n/a"))

        deadline = time.monotonic() - 1.0
        agent.generate_ideas(_profile(), deadline=deadline)

        self.assertEqual(calls, [])
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)

    def test_ollama_emergency_leg_timeout_is_clamped_to_remaining_budget(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        captured: dict = {}

        def fake_call_ollama(prompt, *, timeout_seconds=None):
            captured["timeout_seconds"] = timeout_seconds
            return None

        agent._call_ollama = fake_call_ollama

        deadline = time.monotonic() + 20.0
        agent.generate_ideas(_profile(), deadline=deadline)

        self.assertIsInstance(captured["timeout_seconds"], float)
        self.assertLessEqual(captured["timeout_seconds"], 20.0)
        self.assertGreater(captured["timeout_seconds"], 20.0 - 2.0)

    def test_ollama_emergency_leg_default_timeout_unchanged_when_no_deadline(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        captured: dict = {}

        def fake_call_ollama(prompt, *, timeout_seconds=None):
            captured["timeout_seconds"] = timeout_seconds
            return None

        agent._call_ollama = fake_call_ollama

        agent.generate_ideas(_profile())  # no deadline -- exactly today's behavior

        self.assertIsNone(captured["timeout_seconds"])


# ---------------------------------------------------------------------------
# Tests 14, 15, 16, 17: backward compatibility -- successful generation,
# non-deadline fallback, provider order, and retry count all unchanged.
# ---------------------------------------------------------------------------


class BackwardCompatibilityTests(unittest.TestCase):
    def test_successful_generation_output_is_identical_regardless_of_deadline(self):
        agent_a = _agent_with_fake_providers(_FakeProvider("fake", ok=True))
        agent_b = _agent_with_fake_providers(_FakeProvider("fake", ok=True))

        profile = _profile()
        ideas_a = agent_a.generate_ideas(profile)
        ideas_b = agent_b.generate_ideas(profile, deadline=time.monotonic() + 120.0)

        self.assertEqual(
            [idea.model_dump() for idea in ideas_a],
            [idea.model_dump() for idea in ideas_b],
        )
        self.assertIsNone(agent_a.last_fallback_reason_code)
        self.assertIsNone(agent_b.last_fallback_reason_code)

    def test_provider_failure_unrelated_to_deadline_still_falls_back_deterministically(self):
        provider = _FakeProvider("fake", ok=False)
        agent = _agent_with_fake_providers(provider)
        agent._call_ollama = lambda *a, **kw: None  # keep this test network-free

        ideas = agent.generate_ideas(_profile())  # no deadline at all -- today's exact path

        self.assertFalse(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(ideas), IDEAS_PER_BATCH)

    def test_default_provider_chain_order_is_unchanged(self):
        chain = ProviderChain(tier="high")
        names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])

    def test_no_extra_generation_attempts_are_added_beyond_the_configured_providers(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=False)
        agent = _agent_with_fake_providers(first, second)
        agent._call_ollama = lambda *a, **kw: None

        agent.generate_ideas(_profile(), deadline=time.monotonic() + 120.0)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)


class LegacyProviderChainCompatibilityTests(unittest.TestCase):
    """Test 20: an older provider-chain test double that does not accept
    `deadline`/`cap_timeout_to_deadline` at all must keep working unchanged
    even when the caller supplies a deadline -- see
    ProjectIdeaAgent._accepts_keyword's compatibility guard."""

    def test_old_style_provider_chain_without_deadline_support_still_works(self):
        class _StrictLegacyProviderChain:
            def __init__(self, result):
                self.result = result
                self.calls: list = []

            def search_web(self, query):
                return _failed_search_result()

            def generate_json(self, prompt, *, use_search=False, max_tokens=None):
                self.calls.append({"prompt": prompt})
                return self.result

        agent = ProjectIdeaAgent()
        agent.provider_chain = _StrictLegacyProviderChain(
            LLMResult(ok=True, provider="fake", model="fake-model", text="", data=_valid_ideas_payload()),
        )

        # A deadline IS supplied, but the fake's generate_json signature has
        # no deadline/cap_timeout_to_deadline parameter at all -- must not
        # raise TypeError, and must not silently drop the real candidate.
        ideas = agent.generate_ideas(_profile(), deadline=time.monotonic() + 120.0)

        self.assertTrue(agent.last_llm_used)
        self.assertEqual(len(agent.provider_chain.calls), 1)
        self.assertEqual(len(ideas), IDEAS_PER_BATCH)
        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 19: another agent (not Idea Generation, not Roadmap) remains
# completely unaffected by this change.
# ---------------------------------------------------------------------------


class OtherAgentsUnaffectedTests(unittest.TestCase):
    def test_project_dna_agent_generate_candidate_signature_is_unaffected(self):
        from app.agents.project_dna_agent import ProjectDNAAgent

        signature = inspect.signature(ProjectDNAAgent.generate_candidate)
        self.assertNotIn("deadline", signature.parameters)


if __name__ == "__main__":
    unittest.main()
