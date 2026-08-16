"""
Tests for Project DNA (ProjectDNAAgent) Writer/ReviewPipeline absolute-
deadline propagation.

Root cause this covers: the Writer's own single generate_json() call
previously had NO deadline at all -- a slow-but-successful DeepInfra/Groq/
Ollama call (each configured up to ~60-90s, see llm_provider.py's default
tier timing) could alone exceed ReviewPipeline's entire 90s total budget
(registry.py's ProjectDNAAgent.max_total_seconds), so a valid, successfully
generated candidate could arrive only for ReviewPipeline.run's own
_time_budget_exceeded check to discard it before the Reviewer ever ran
(status="review_unavailable", no candidate). These tests prove: (1) a
deadline now reaches the provider cascade and clamps/skips accordingly, (2)
the Reviewer still runs when the Writer finishes in budget, (3) the review
reserve is genuinely withheld from the Writer, (4) deadline exhaustion is
reported with honest, typed provenance -- never as a successful AI
generation and never as schema_invalid, and (5) omitting a deadline entirely
preserves the exact prior unbounded behavior.

No real network calls, no real sleeps -- fake providers/reviewer agents
only, wired into the REAL (unmodified) ProviderChain/ReviewPipeline so their
own deadline-clamping/skip logic runs for real.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_dna_agent import (  # noqa: E402
    ProjectDNAAgent,
    ProjectDNARequest,
    WRITER_DEADLINE_EXCEEDED,
)
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import dna as dna_router  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


def _valid_dna_payload() -> dict:
    return {
        "projectDNAType": "Applied Software Engineering Project",
        "overallScore": 78,
        "technicalFitScore": 80,
        "skillMatchScore": 75,
        "innovationScore": 70,
        "feasibilityScore": 82,
        "marketRelevanceScore": 76,
        "dataReadinessScore": 85,
        "scopeClarityScore": 74,
        "supervisorFitScore": 77,
        "riskLevel": "Low",
        "strengths": [
            "Clear academic value.",
            "Realistic stack.",
            "Useful deliverables.",
        ],
        "weaknesses": [
            "Scope must be controlled.",
            "Some skills need practice.",
            "MVP should be prioritized.",
        ],
        "riskProfile": [
            {
                "title": "Scope Control",
                "level": "Low",
                "explanation": "Manageable with discipline.",
                "mitigation": "Start with a small MVP.",
            },
            {
                "title": "MVP Prioritization",
                "level": "Low",
                "explanation": "Focus on the core workflow first.",
                "mitigation": "Define must-have features early.",
            },
        ],
        "requiredSkillsAnalysis": [
            {
                "skillName": "Python",
                "status": "Matched",
                "explanation": "Already in the student's profile.",
            },
        ],
        "recommendedImprovements": [
            "Define the MVP first.",
            "Prepare the schema early.",
            "Implement core features before extras.",
        ],
        "summary": "This project is suitable if the scope is controlled.",
    }


def _fake_request() -> ProjectDNARequest:
    return ProjectDNARequest(
        ideaTitle="Study Room Booking Platform",
        problemStatement="Students need to book study rooms online.",
        targetUsers="University students",
        whyUseful="Saves time finding available rooms.",
        lebaneseMarketRelevance="Useful for Lebanese universities.",
        requiredTechnologies="ASP.NET Core, Python FastAPI, PostgreSQL",
        requiredSkills="C#, SQL",
        missingSkills="",
        difficultyLevel="3",
        datasetNeeded="No for MVP",
        finalDeliverables="A working booking web app",
        domain="Web Development",
        lebaneseSector="Education",
        studentMajor="Computer Science",
        experienceLevel="intermediate",
        availableHoursPerWeek=15,
        teamSize=1,
        studentSkills=["C#", "SQL"],
        skillRatings={"C#": 4, "SQL": 3},
    )


class _FakeProvider:
    """
    Stands in for a real BaseProvider (DeepInfraProvider/GroqProvider/
    OllamaProvider) inside a REAL ProviderChain -- so ProviderChain's own
    (unmodified, shared) _run_cascade deadline/skip logic runs for real,
    while this fake never makes a network call or sleeps.
    """

    def __init__(self, name: str, *, timeout_seconds: float = 90.0, ok: bool = True):
        self.name = name
        self.timeout_seconds = timeout_seconds
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
                data=_valid_dna_payload(),
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake provider failure", error_category="provider_http_error",
        )


class _StrictLegacyProviderChain:
    """Mirrors Roadmap's identical fixture -- a provider chain built against
    the PRE-deadline generate_json signature (no cap_timeout_to_deadline
    parameter at all). Proves that omitting `deadline` keeps the exact prior
    call shape: passing cap_timeout_to_deadline unconditionally would make
    this fake raise TypeError."""

    def __init__(self, result: LLMResult):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt, *, use_search=False, max_tokens=None,
                       reporter=None, schema_description=None, deadline=None):
        self.calls.append({"deadline": deadline})
        return self.result


# ---------------------------------------------------------------------------
# Tests 6, 7: provider timeout is clamped to the remaining Writer budget /
# stays configured when shorter.
# ---------------------------------------------------------------------------


class ProviderTimeoutClampingTests(unittest.TestCase):
    def test_provider_receives_writer_budget_seconds_no_greater_than_remaining_deadline(self):
        provider = _FakeProvider("fake-deepinfra", timeout_seconds=90.0)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        deadline = time.monotonic() + 12.0
        agent.analyze(_fake_request(), deadline=deadline)

        self.assertEqual(provider.call_count, 1)
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 12.0)
        self.assertGreater(provider.received_writer_budget_seconds, 12.0 - 2.0)  # test-overhead margin

    def test_no_unnecessary_expansion_when_configured_timeout_is_shorter(self):
        provider = _FakeProvider("fake-deepinfra", timeout_seconds=5.0)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        deadline = time.monotonic() + 90.0
        agent.analyze(_fake_request(), deadline=deadline)

        # The Writer budget passed is still ~90s; each provider's OWN
        # min(configured, remaining) clamp (unmodified, tested elsewhere)
        # is what keeps the effective timeout at 5s, not this wiring.
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertGreater(provider.received_writer_budget_seconds, 80.0)


# ---------------------------------------------------------------------------
# Test 8: a fallback provider is never started once too little time remains
# ---------------------------------------------------------------------------


class FallbackProviderSkippedAfterDeadlineTests(unittest.TestCase):
    def test_second_provider_not_started_when_first_attempt_exhausts_the_budget(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[first, second])

        real_now = time.monotonic()
        deadline = real_now + 10.0  # enough for the first attempt to start

        clock_values = iter([real_now, real_now + 8.0, real_now + 8.0])

        def fake_monotonic():
            try:
                return next(clock_values)
            except StopIteration:
                return real_now + 8.0

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.analyze(_fake_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)
        self.assertFalse(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 9: fallback provider runs when enough time remains (normal cascade).
# ---------------------------------------------------------------------------


class FallbackProviderRunsWithEnoughTimeTests(unittest.TestCase):
    def test_second_provider_runs_when_first_fails_and_time_remains(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[first, second])

        deadline = time.monotonic() + 90.0
        agent.analyze(_fake_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)
        self.assertTrue(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 10: pre-generation exhaustion prevents any provider call.
# ---------------------------------------------------------------------------


class NoHangWhenDeadlineExhaustedTests(unittest.TestCase):
    def test_generation_returns_quickly_without_calling_a_provider_when_budget_is_exhausted(self):
        provider = _FakeProvider("fake-deepinfra", ok=False)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        # Already exhausted -- below _MIN_SECONDS_PER_PROVIDER_ATTEMPT, so
        # generation must never enter the cascade at all.
        deadline = time.monotonic() + 0.1

        started = time.monotonic()
        agent.analyze(_fake_request(), deadline=deadline)
        elapsed = time.monotonic() - started

        self.assertEqual(provider.call_count, 0)
        self.assertLess(elapsed, 0.5)
        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)


# ---------------------------------------------------------------------------
# Tests 11, 12: typed deadline reason recorded; never classified as a schema
# failure.
# ---------------------------------------------------------------------------


class DeadlineExhaustionProvenanceTests(unittest.TestCase):
    def test_agent_reports_writer_deadline_exceeded_not_a_generic_failure(self):
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[_FakeProvider("fake", ok=False)])

        already_past_deadline = time.monotonic() - 1.0
        response = agent.analyze(_fake_request(), deadline=already_past_deadline)

        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        # The deterministic fallback is still a complete, valid response.
        self.assertTrue(response.summary)

    def test_deadline_exhaustion_pipeline_status_is_provider_unavailable_never_schema_invalid(self):
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[_FakeProvider("fake", ok=False)])

        reviewer = _NeverCalledReviewerAgent()
        pipeline = ReviewPipeline("ProjectDNAAgent", reviewer_agent=reviewer)

        request = _fake_request()
        context = dna_router._build_review_context(request)
        already_past_deadline = time.monotonic() - 1.0

        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=already_past_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=time.monotonic() + 90.0,
        )

        # Test 11/17: never mislabeled as schema_invalid.
        self.assertEqual(result.status, "provider_unavailable")
        self.assertNotEqual(result.status, "schema_invalid")
        self.assertFalse(result.usable)
        self.assertEqual(agent.last_fallback_reason_code, WRITER_DEADLINE_EXCEEDED)
        # Test 13: review never invoked without a usable Writer candidate.
        self.assertEqual(reviewer.call_count, 0)


class _NeverCalledReviewerAgent:
    def __init__(self):
        self.call_count = 0

    def analyze(self, candidate, context, **kwargs):
        self.call_count += 1
        raise AssertionError("reviewer must not be called after a Writer-deadline failure")


# ---------------------------------------------------------------------------
# Tests 3, 14: Reviewer still runs and receives its reserved time when the
# Writer finishes within budget.
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


class ReviewerStillRunsWithinBudgetTests(unittest.TestCase):
    def test_reviewer_is_invoked_and_candidate_is_accepted(self):
        provider = _FakeProvider("fake-deepinfra", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("ProjectDNAAgent", reviewer_agent=reviewer)

        request = _fake_request()
        context = dna_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - dna_router._WRITER_TIME_RESERVE_SECONDS

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
# Tests 1, 2, 3, 4: the router creates one global deadline (from the real
# registry budget) and a strictly shorter Writer deadline (by exactly the
# reserve), forwarding both to the right places.
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
        self.last_fallback_reason_code = None
        self.captured_writer_deadline: float | None = None

    def generate_candidate(self, request, *, deadline=None):
        self.captured_writer_deadline = deadline
        return None

    def build_safe_fallback(self, request):
        return SimpleNamespace(model_dump=lambda: {"summary": "fallback"})


class _CapturingPipeline:
    """Records the deadline the router passes to run(), while resolving
    max_total_seconds from the REAL registry config (so this test stays
    correct if that value is ever retuned)."""

    def __init__(self, agent_name):
        from app.review.registry import get_agent_config
        self.config = get_agent_config(agent_name)
        self.captured_global_deadline: float | None = None

    def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
        self.captured_global_deadline = deadline
        writer_call_fn()
        return SimpleNamespace(
            usable=False, status="provider_unavailable", output={}, outputOrigin="none",
        )


class WriterDeadlineReserveTests(unittest.TestCase):
    def test_router_creates_global_and_writer_deadlines_using_registry_budget(self):
        captured_pipeline: list[_CapturingPipeline] = []

        def _pipeline_factory(agent_name):
            pipeline = _CapturingPipeline(agent_name)
            captured_pipeline.append(pipeline)
            return pipeline

        agent_instance = _CapturingAgent()

        with patch.object(dna_router, "ProjectDNAAgent", return_value=agent_instance), \
             patch.object(dna_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(dna_router, "build_review_response", return_value={}):
            dna_router.analyze_project_dna(_fake_request())

        pipeline = captured_pipeline[0]

        # Test 1: exactly one global deadline, from the real registry budget.
        # 90.0 -> 150.0: raised after a freeze-audit live test measured a
        # real 4-call DeepInfra sequence (Writer -> Reviewer -> Rewrite ->
        # re-Reviewer) needing ~91s, timing out 1s past the old 90s budget
        # and discarding an already-generated, valid, personalized analysis
        # for the generic fallback (allow_unreviewed_output=False here) --
        # see registry.py's ProjectDNAAgent max_total_seconds comment.
        self.assertIsNotNone(pipeline.captured_global_deadline)
        self.assertEqual(pipeline.config.max_total_seconds, 150.0)

        # Test 4: the Writer deadline reached ProjectDNAAgent.
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        # Test 2: writer_deadline < global_deadline by exactly the reserve.
        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, dna_router._WRITER_TIME_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        # The Writer's own deadline must leave it LESS than the full
        # registry budget -- it can never be told it owns the whole thing.
        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 15: cancellation propagates honestly, never converted into a result.
#
# Honest limitation: generate_json here is a synchronous, blocking call.
# Cancellation is only ever observed BEFORE such a call starts or AFTER it
# raises/returns -- this task does not make an in-flight blocking request
# interruptible.
# ---------------------------------------------------------------------------


class CancellationTests(unittest.TestCase):
    def test_cancelled_error_from_generation_propagates(self):
        import asyncio

        class _CancellingProvider:
            name = "cancelling-generation"

            def generate_json(self, *args, **kwargs):
                raise asyncio.CancelledError()

        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[_CancellingProvider()])

        with self.assertRaises(asyncio.CancelledError):
            agent.analyze(_fake_request(), deadline=time.monotonic() + 90.0)

        self.assertIsNone(agent.last_fallback_reason_code)


# ---------------------------------------------------------------------------
# Test 16: successful output unchanged (fast generation, with and without a
# deadline).
# ---------------------------------------------------------------------------


class SuccessfulOutputUnchangedTests(unittest.TestCase):
    def test_output_identical_with_and_without_deadline(self):
        agent_a = ProjectDNAAgent()
        agent_a.provider_chain = ProviderChain(providers=[_FakeProvider("fake-a", ok=True)])
        agent_b = ProjectDNAAgent()
        agent_b.provider_chain = ProviderChain(providers=[_FakeProvider("fake-b", ok=True)])

        request = _fake_request()
        response_a = agent_a.analyze(request)
        response_b = agent_b.analyze(request, deadline=time.monotonic() + 90.0)

        self.assertEqual(response_a.model_dump(), response_b.model_dump())
        self.assertTrue(agent_a.last_llm_used)
        self.assertTrue(agent_b.last_llm_used)


# ---------------------------------------------------------------------------
# Test 17: non-deadline provider failure remains unchanged.
# ---------------------------------------------------------------------------


class NonDeadlineFailureUnchangedTests(unittest.TestCase):
    def test_ordinary_provider_failure_does_not_get_a_deadline_reason_code(self):
        provider = _FakeProvider("fake", ok=False)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        response = agent.analyze(_fake_request(), deadline=time.monotonic() + 90.0)

        self.assertFalse(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertTrue(response.summary)


# ---------------------------------------------------------------------------
# Test 18: provider order remains unchanged.
# ---------------------------------------------------------------------------


class ProviderOrderUnchangedTests(unittest.TestCase):
    def test_default_provider_order_is_unchanged(self):
        chain = ProviderChain()
        generation_names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(generation_names, ["DeepInfraProvider", "GroqProvider", "OllamaProvider"])


# ---------------------------------------------------------------------------
# Test 19: retry counts unchanged -- exactly one attempt per configured
# provider, no extras added by this task.
# ---------------------------------------------------------------------------


class RetryCountsUnchangedTests(unittest.TestCase):
    def test_no_extra_attempts_are_added_beyond_the_configured_providers(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=False)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[first, second])

        agent.analyze(_fake_request(), deadline=time.monotonic() + 90.0)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 1)


# ---------------------------------------------------------------------------
# Test 20: no behavior change when deadline=None (backward compatibility) --
# old strict fakes without a deadline keyword keep working.
# ---------------------------------------------------------------------------


class NoDeadlineBackwardCompatibilityTests(unittest.TestCase):
    def test_omitting_deadline_preserves_the_exact_prior_call_shape(self):
        # Would raise TypeError if cap_timeout_to_deadline/deadline were
        # passed unconditionally -- this fake matches the PRE-deadline
        # generate_json signature exactly.
        fake_chain = _StrictLegacyProviderChain(_ok(_valid_dna_payload()))
        agent = ProjectDNAAgent()
        agent.provider_chain = fake_chain

        response = agent.analyze(_fake_request())

        self.assertTrue(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(fake_chain.calls), 1)
        self.assertIsNone(fake_chain.calls[0]["deadline"])
        self.assertTrue(response.summary)


# ---------------------------------------------------------------------------
# Test 26: another agent (Market Footprint) remains unchanged -- proves this
# task did not opt every agent into deadline propagation.
# ---------------------------------------------------------------------------


class OtherAgentUnaffectedTests(unittest.TestCase):
    def test_market_footprint_agent_signature_is_unaffected(self):
        import inspect as _inspect
        from app.agents.market_footprint_agent import MarketFootprintAgent

        sig = _inspect.signature(MarketFootprintAgent.generate_candidate_from_result)
        self.assertNotIn("deadline", sig.parameters)


# ---------------------------------------------------------------------------
# Tests 12, 13, 14: final-output provenance correction. A Writer call can
# succeed (agent.last_llm_used=True, agent.last_provider="deepinfra") and
# still be discarded by a later review-pipeline stage (result.usable=False
# for a reason OTHER than Writer-deadline exhaustion, e.g. the semantic
# Reviewer rejecting it or review_unavailable) -- in that case the response
# actually shown to the student is agent.build_safe_fallback()'s
# deterministic template, so the response's provenance fields (llmUsed,
# provider, modelUsed, source, outputOrigin, fallbackUsed) must describe
# THAT displayed output, never the discarded Writer attempt.
# ---------------------------------------------------------------------------


def _critical_issue_payload():
    return {
        "strengths": [],
        "issues": [
            {
                "severity": "critical",
                "requiresCorrection": True,
                "category": "contradiction",
                "affectedField": "summary",
                "description": "Contradicts a trusted skill rating.",
                "revisionInstruction": "Do not contradict trusted skill ratings.",
            },
        ],
        "qualityScore": 10,
        "overallAssessment": "Not usable.",
    }


class _RejectingReviewerAgent:
    """Always finds the SAME blocking critical issue -- forces
    ReviewDecisionEngine to require a rewrite, and (paired with
    _StillCriticalRewriteAgent below, which never fixes it) to exhaust
    DNA's max_semantic_rewrites=1 and end in status="rejected", usable=False
    -- the same shape of failure as a real semantic Reviewer rejecting a
    genuinely low-quality AI response, distinct from a Writer-deadline
    failure."""

    def __init__(self):
        self.call_count = 0

    def analyze(self, candidate, context, **kwargs):
        self.call_count += 1
        return LLMResult(
            ok=True, provider="fake-reviewer", model="fake-model", text="",
            data=_critical_issue_payload(),
        )


class _StillCriticalRewriteAgent:
    """Produces a rewritten candidate that the (always-rejecting) reviewer
    above will flag as critical again -- so the one rewrite attempt DNA's
    max_semantic_rewrites=1 allows is consumed without resolving the issue,
    and the pipeline settles on status="rejected", usable=False."""

    def rewrite(self, candidate, blocking_issues, context, *, agent_name, deadline=None):
        return LLMResult(
            ok=True, provider="fake-rewriter", model="fake-model", text="",
            data=_valid_dna_payload(),
        )


class WriterSuccessButReviewFailureProvenanceTests(unittest.TestCase):
    def _run_with_rejecting_reviewer(self):
        provider = _FakeProvider("fake-deepinfra", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        reviewer = _RejectingReviewerAgent()
        pipeline = ReviewPipeline(
            "ProjectDNAAgent",
            reviewer_agent=reviewer,
            rewrite_agent=_StillCriticalRewriteAgent(),
        )

        request = _fake_request()
        context = dna_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - dna_router._WRITER_TIME_RESERVE_SECONDS

        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=writer_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )
        return agent, result

    def test_writer_succeeded_but_pipeline_result_is_not_usable(self):
        # Precondition sanity check: the Writer really did succeed even
        # though the overall pipeline result ends up unusable.
        agent, result = self._run_with_rejecting_reviewer()

        self.assertTrue(agent.last_llm_used)
        self.assertEqual(agent.last_provider, "fake-deepinfra")
        self.assertFalse(result.usable)

    def test_router_response_does_not_label_fallback_as_ai_generated(self):
        agent, result = self._run_with_rejecting_reviewer()

        with patch.object(dna_router, "ProjectDNAAgent", return_value=agent), \
             patch.object(dna_router, "ReviewPipeline") as pipeline_cls:
            pipeline_cls.return_value.run.return_value = result
            pipeline_cls.return_value.config.max_total_seconds = 90.0

            response = dna_router.analyze_project_dna(_fake_request())

        # Test 12/14: the displayed output is the deterministic fallback,
        # so provenance must say so -- never DeepInfra/AI-generated, even
        # though the Writer itself succeeded.
        self.assertFalse(response["llmUsed"])
        self.assertEqual(response["source"], "dynamic-fallback")
        self.assertIsNone(response["provider"])
        self.assertIsNone(response["modelUsed"])
        self.assertEqual(response["outputOrigin"], "deterministic_fallback")
        self.assertTrue(response["fallbackUsed"])
        self.assertIsNotNone(response["fallbackReasonCode"])
        self.assertNotEqual(response["fallbackReasonCode"], WRITER_DEADLINE_EXCEEDED)

        # The Writer's own real attempt is preserved as diagnostic-only
        # metadata, distinct from the final-output provenance fields above.
        self.assertEqual(len(response["providerAttempts"]), 1)
        self.assertEqual(response["providerAttempts"][0]["provider"], "fake-deepinfra")
        self.assertTrue(response["providerAttempts"][0]["success"])

        # The analysis body actually shown is the deterministic template,
        # not the (real, AI-produced) candidate the Writer generated.
        self.assertEqual(
            response["analysis"]["summary"],
            agent.build_safe_fallback(_fake_request()).model_dump()["summary"],
        )


class SuccessfulReviewedOutputProvenanceUnchangedTests(unittest.TestCase):
    def test_approved_ai_output_is_labeled_as_ai_generated(self):
        provider = _FakeProvider("fake-deepinfra", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("ProjectDNAAgent", reviewer_agent=reviewer)

        request = _fake_request()
        context = dna_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - dna_router._WRITER_TIME_RESERVE_SECONDS

        result = pipeline.run(
            lambda: agent.generate_candidate(request, deadline=writer_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )

        with patch.object(dna_router, "ProjectDNAAgent", return_value=agent), \
             patch.object(dna_router, "ReviewPipeline") as pipeline_cls:
            pipeline_cls.return_value.run.return_value = result
            pipeline_cls.return_value.config.max_total_seconds = 90.0

            response = dna_router.analyze_project_dna(request)

        # Test 12: a successful, reviewed AI result remains marked as
        # AI-origin output -- this task must not regress the normal path.
        self.assertTrue(result.usable)
        self.assertTrue(response["llmUsed"])
        self.assertEqual(response["source"], "fake-deepinfra")
        self.assertEqual(response["provider"], "fake-deepinfra")
        self.assertEqual(response["outputOrigin"], "ai_generated")
        self.assertFalse(response["fallbackUsed"])
        self.assertIsNone(response["fallbackReasonCode"])


# ---------------------------------------------------------------------------
# Test 3: the Writer's effective budget is ~125s (150s total - 25s reserve).
# ---------------------------------------------------------------------------


class WriterEffectiveBudgetTests(unittest.TestCase):
    def test_writer_effective_budget_is_approximately_125_seconds(self):
        # 65.0 -> 125.0 follows ProjectDNAAgent's registry max_total_seconds
        # 90.0 -> 150.0 change (see WriterDeadlineReserveTests above for
        # why) -- the reserve itself (_WRITER_TIME_RESERVE_SECONDS) is
        # unchanged, so the Writer simply gets the same +60s the total
        # budget grew by.
        pipeline = ReviewPipeline("ProjectDNAAgent")
        global_deadline = time.monotonic() + pipeline.config.max_total_seconds
        writer_deadline = global_deadline - dna_router._WRITER_TIME_RESERVE_SECONDS

        writer_budget = writer_deadline - time.monotonic()

        self.assertAlmostEqual(writer_budget, 125.0, delta=0.5)


# ---------------------------------------------------------------------------
# Test 7: the Reviewer receives the SAME absolute global deadline passed to
# ReviewPipeline.run -- not a freshly-computed one, and not the Writer's
# earlier deadline.
# ---------------------------------------------------------------------------


class ReviewerReceivesGlobalDeadlineTests(unittest.TestCase):
    def test_reviewer_analyze_is_invoked_with_the_global_deadline(self):
        provider = _FakeProvider("fake-deepinfra", ok=True)
        agent = ProjectDNAAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        captured_kwargs: dict = {}

        class _CapturingReviewerAgent:
            def analyze(self, candidate, context, **kwargs):
                captured_kwargs.update(kwargs)
                return _ok(_reviewer_ok_payload())

        pipeline = ReviewPipeline("ProjectDNAAgent", reviewer_agent=_CapturingReviewerAgent())

        request = _fake_request()
        context = dna_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - dna_router._WRITER_TIME_RESERVE_SECONDS

        pipeline.run(
            lambda: agent.generate_candidate(request, deadline=writer_deadline),
            context,
            writer_trusted_parts=context.trusted_text_fields(),
            writer_untrusted_parts=context.untrusted_text_fields(),
            deadline=global_deadline,
        )

        self.assertIn("deadline", captured_kwargs)
        # The SAME absolute deadline object/value passed into run(), not the
        # earlier, separate writer_deadline reserved for the Writer alone.
        self.assertEqual(captured_kwargs["deadline"], global_deadline)
        self.assertNotEqual(captured_kwargs["deadline"], writer_deadline)


# ---------------------------------------------------------------------------
# Test 15: allow_unreviewed_output remains False for ProjectDNAAgent -- an
# unreviewed candidate (e.g. Reviewer timeout) must never be shown as if it
# had passed semantic review, since DNA's rubric explicitly checks for
# skill-rating contradictions.
# ---------------------------------------------------------------------------


class AllowUnreviewedOutputUnchangedTests(unittest.TestCase):
    def test_project_dna_agent_still_requires_a_successful_review(self):
        from app.review.registry import get_agent_config

        config = get_agent_config("ProjectDNAAgent")
        self.assertFalse(config.allow_unreviewed_output)


# ---------------------------------------------------------------------------
# Test 16: this task did not alter any OTHER agent's registry timing budget.
# ---------------------------------------------------------------------------


class OtherAgentRegistryTimingUnchangedTests(unittest.TestCase):
    def test_other_agents_keep_their_documented_timing_budgets(self):
        from app.review.registry import get_agent_config

        # Values match the budgets documented inline in registry.py/roadmap.py
        # at the time this task started -- this only guards against an
        # accidental edit to a shared file, not a deliberate future retune.
        # ProjectDNAAgent's own 90.0 -> 150.0 IS one such deliberate later
        # retune (see WriterDeadlineReserveTests above) -- updated here to
        # match, not a case of this guard catching an accidental edit.
        self.assertEqual(get_agent_config("ProjectDNAAgent").max_total_seconds, 150.0)
        # ProjectRoadmapAgent's own 360.0 -> 540.0 is another such later,
        # unrelated retune (see test_roadmap_timeout_adjustment.py's
        # OtherAgentsUnchangedTests for the live-measured rationale).
        self.assertEqual(get_agent_config("ProjectRoadmapAgent").max_total_seconds, 540.0)
        self.assertEqual(get_agent_config("SEDocumentationAgent").max_total_seconds, 1200.0)


if __name__ == "__main__":
    unittest.main()
