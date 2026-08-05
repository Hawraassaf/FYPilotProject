"""
Tests for Roadmap Writer/ReviewPipeline absolute-deadline propagation.

Root cause this covers: the Writer's own provider cascade previously had NO
deadline at all -- a slow-but-successful DeepInfra/Ollama call (configured
up to 120s/180s, see llm_provider.py's "roadmap" tier timing) could alone
exceed ReviewPipeline's entire ~90s total budget, so a valid, successfully
generated candidate could arrive only for ReviewPipeline.run's own
_time_budget_exceeded check to discard it before the Reviewer ever ran
(status="review_unavailable", no candidate). These tests prove: (1) a
deadline now reaches the provider cascade and clamps/skips accordingly, (2)
the Reviewer still runs when the Writer finishes in budget, (3) the review
reserve is genuinely withheld from the Writer, (4) deadline exhaustion is
reported with honest, typed provenance -- never as a successful AI
generation, and (5) omitting a deadline entirely preserves the exact prior
unbounded behavior.

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

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_roadmap_agent import (  # noqa: E402
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
)
from app.agents.roadmap import fallback_reason  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers import roadmap as roadmap_router  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


def _task(title, task_type="", **overrides):
    payload = {"title": title, "taskType": task_type}
    payload.update(overrides)
    return payload


def _valid_plan_payload() -> dict:
    """A minimal but lifecycle-complete web-project phase plan -- matches
    the fixture already used in test_roadmap_provider_reliability.py /
    test_roadmap_fallback_provenance.py, so it survives
    RoadmapCandidateSchema's real structural validation and the lifecycle-
    coverage gate without being rejected for an unrelated reason."""
    return {
        "roadmapTitle": "Test Roadmap",
        "teamStrategy": "Solo strategy.",
        "finalAdvice": "Ship the MVP first.",
        "phases": [
            {
                "name": "Requirements and Architecture",
                "weeks": 2,
                "goal": "Define requirements and design the database schema.",
                "tasks": [
                    _task("Write functional requirements for the booking workflow"),
                    _task("Design the database schema for bookings and users", "database_design"),
                ],
                "deliverables": ["Requirements document"],
                "skillsToLearn": [],
                "riskWarning": "Scope creep.",
                "checkpoint": "Supervisor reviews requirements.",
            },
            {
                "name": "Core Booking Implementation",
                "weeks": 3,
                "goal": "Implement the core booking workflow and API integration.",
                "tasks": [
                    _task("Implement the core booking workflow endpoint", "crud_api"),
                    _task("Integrate the booking API with the payment endpoint", "external_api_integration"),
                ],
                "deliverables": ["Working booking API"],
                "skillsToLearn": [],
                "riskWarning": "Integration risk.",
                "checkpoint": "Supervisor reviews the API.",
            },
            {
                "name": "Testing and Deployment",
                "weeks": 2,
                "goal": "Test the booking workflow and deploy the final build.",
                "tasks": [
                    _task("Test the booking workflow end to end", "functional_testing"),
                    _task("Write user documentation and deploy the final build", "documentation_presentation"),
                ],
                "deliverables": ["Test report", "Deployed build"],
                "skillsToLearn": [],
                "riskWarning": "Deployment risk.",
                "checkpoint": "Supervisor reviews the final deployment.",
            },
        ],
    }


def _fake_request() -> ProjectRoadmapRequest:
    return ProjectRoadmapRequest(
        ideaTitle="Booking Platform",
        problemStatement="Students need to book study rooms online.",
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
        requiredSkills="C#, SQL",
        missingSkills="",
        difficultyLevel="medium",
        expectedDurationWeeks=8,
        domain="web",
        finalDeliverables="A working booking web app",
        teamSize=1,
        availableHoursPerWeek=15,
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

    def __init__(self, name: str, *, timeout_seconds: float = 180.0, ok: bool = True):
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
                data=_valid_plan_payload(),
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake provider failure", error_category="provider_http_error",
        )


class _StrictLegacyProviderChain:
    """Mirrors _FakeProviderChain from test_roadmap_provider_reliability.py
    -- a provider chain built against the PRE-deadline generate_json
    signature (no cap_timeout_to_deadline parameter at all). Used to prove
    that omitting `deadline` keeps the exact prior call shape (Test 8):
    passing cap_timeout_to_deadline unconditionally would make this fake
    raise TypeError, exactly like the regression caught during development."""

    def __init__(self, result: LLMResult):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt, *, use_search=False, max_tokens=None,
                       reporter=None, schema_description=None, deadline=None):
        self.calls.append({"deadline": deadline})
        return self.result


# ---------------------------------------------------------------------------
# Test 1: provider timeout is clamped to the remaining Writer budget
# ---------------------------------------------------------------------------


class ProviderTimeoutClampingTests(unittest.TestCase):
    def test_provider_receives_writer_budget_seconds_no_greater_than_remaining_deadline(self):
        # Configured timeout (180s, e.g. Ollama) is far larger than the
        # remaining Writer budget (12s) -- the real provider-level clamp is
        # effective_timeout = min(configured, writer_budget_seconds), so
        # asserting the forwarded budget itself proves the wiring that
        # feeds that (unmodified, already-tested) clamp.
        provider = _FakeProvider("fake-deepinfra", timeout_seconds=180.0)
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        deadline = time.monotonic() + 12.0
        agent.generate(_fake_request(), deadline=deadline)

        self.assertEqual(provider.call_count, 1)
        self.assertIsInstance(provider.received_writer_budget_seconds, float)
        self.assertLessEqual(provider.received_writer_budget_seconds, 12.0)
        self.assertGreater(provider.received_writer_budget_seconds, 12.0 - 2.0)  # test-overhead margin


# ---------------------------------------------------------------------------
# Test 2: a fallback provider is never started once too little time remains
# ---------------------------------------------------------------------------


class FallbackProviderSkippedAfterDeadlineTests(unittest.TestCase):
    def test_second_provider_not_started_when_first_attempt_exhausts_the_budget(self):
        first = _FakeProvider("fake-first", ok=False)
        second = _FakeProvider("fake-second", ok=True)
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[first, second])

        real_now = time.monotonic()
        deadline = real_now + 10.0  # enough for the first attempt to start

        # Our fakes return instantly, so there's no real elapsed time to
        # observe -- a fake clock stands in for "the first (failed)
        # provider call actually took 8 real seconds", leaving only ~2s
        # (below ProviderChain's shared _MIN_SECONDS_PER_PROVIDER_ATTEMPT
        # floor of 4.0s) by the time the SECOND provider's budget is
        # checked. Only app.services.llm_provider's time.monotonic is
        # patched -- ProjectRoadmapAgent's own deadline check (a separate
        # module reference) still sees the real, unmocked clock.
        clock_values = iter([real_now, real_now + 8.0, real_now + 8.0])

        def fake_monotonic():
            try:
                return next(clock_values)
            except StopIteration:
                return real_now + 8.0

        with patch("app.services.llm_provider.time.monotonic", side_effect=fake_monotonic):
            agent.generate(_fake_request(), deadline=deadline)

        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)
        self.assertFalse(agent.last_llm_used)


# ---------------------------------------------------------------------------
# Test 3: Reviewer still runs when the Writer finishes within budget
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
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        reviewer = _FakeReviewerAgent([_ok(_reviewer_ok_payload())])
        pipeline = ReviewPipeline("ProjectRoadmapAgent", tier="roadmap", reviewer_agent=reviewer)

        request = _fake_request()
        context = roadmap_router._build_review_context(request)
        global_deadline = time.monotonic() + 90.0
        writer_deadline = global_deadline - roadmap_router._SEMANTIC_REVIEW_RESERVE_SECONDS

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
# Test 4: the Writer cannot consume the review reserve
# ---------------------------------------------------------------------------


class _CapturingAgent:
    """Records the deadline the router forwards to generate_candidate,
    without making any real call."""

    def __init__(self):
        self.request_id = None
        self.last_llm_used = False
        self.last_provider = None
        self.last_model_used = None
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_generation_source = None
        self.last_fallback_reason_code = fallback_reason.PROVIDER_UNAVAILABLE
        self.last_usable_phase_count = 0
        self.last_lifecycle_coverage_passed = True
        self.last_missing_lifecycle_categories: list[str] = []
        self.last_blocked_term_tasks_dropped = 0
        self.captured_writer_deadline: float | None = None

    def generate_candidate(self, request, *, deadline=None):
        self.captured_writer_deadline = deadline
        return None

    def build_safe_fallback(self, request):
        return SimpleNamespace(model_dump=lambda: {"roadmapTitle": "fallback"})


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


class WriterDeadlineReserveTests(unittest.TestCase):
    def test_writer_deadline_is_strictly_before_global_deadline_by_the_review_reserve(self):
        captured_pipeline: list[_CapturingPipeline] = []

        def _pipeline_factory(agent_name, tier):
            pipeline = _CapturingPipeline(agent_name, tier)
            captured_pipeline.append(pipeline)
            return pipeline

        agent_instance = _CapturingAgent()

        with patch.object(roadmap_router, "ProjectRoadmapAgent", return_value=agent_instance), \
             patch.object(roadmap_router, "ReviewPipeline", side_effect=_pipeline_factory), \
             patch.object(roadmap_router, "build_review_response", return_value={}):
            roadmap_router.generate_project_roadmap(_fake_request())

        pipeline = captured_pipeline[0]
        self.assertIsNotNone(pipeline.captured_global_deadline)
        self.assertIsNotNone(agent_instance.captured_writer_deadline)

        reserve = pipeline.captured_global_deadline - agent_instance.captured_writer_deadline
        self.assertAlmostEqual(reserve, roadmap_router._SEMANTIC_REVIEW_RESERVE_SECONDS, places=2)
        self.assertLess(agent_instance.captured_writer_deadline, pipeline.captured_global_deadline)

        # The Writer's own deadline must leave it LESS than the full
        # registry budget -- it can never be told it owns the whole thing.
        writer_budget = agent_instance.captured_writer_deadline - time.monotonic()
        self.assertLess(writer_budget, pipeline.config.max_total_seconds)


# ---------------------------------------------------------------------------
# Test 5: no excessive sleep / hang when time is exhausted
# ---------------------------------------------------------------------------


class NoHangWhenDeadlineExhaustedTests(unittest.TestCase):
    def test_generation_returns_quickly_without_sleeping_when_budget_is_effectively_zero(self):
        provider = _FakeProvider("fake-deepinfra", ok=False)
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[provider])

        # Already exhausted -- below _MIN_SECONDS_PER_PROVIDER_ATTEMPT, so
        # the cascade must skip even the FIRST provider rather than
        # starting (and potentially blocking/retrying inside) a call.
        deadline = time.monotonic() + 0.1

        started = time.monotonic()
        agent.generate(_fake_request(), deadline=deadline)
        elapsed = time.monotonic() - started

        self.assertEqual(provider.call_count, 0)
        self.assertLess(elapsed, 0.5)
        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.WRITER_DEADLINE_EXCEEDED)


# ---------------------------------------------------------------------------
# Test 6: deadline exhaustion has honest, typed provenance
# ---------------------------------------------------------------------------


class DeadlineExhaustionProvenanceTests(unittest.TestCase):
    def test_agent_reports_writer_deadline_exceeded_not_a_generic_failure(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[_FakeProvider("fake", ok=False)])

        already_past_deadline = time.monotonic() - 1.0
        response = agent.generate(_fake_request(), deadline=already_past_deadline)

        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.WRITER_DEADLINE_EXCEEDED)
        # The deterministic fallback is still a complete, valid response --
        # never partial, never mislabeled.
        self.assertGreater(len(response.phases), 0)

    def test_router_classifies_deadline_exhaustion_as_deterministic_fallback_not_ai_generated(self):
        agent = ProjectRoadmapAgent()
        agent.last_fallback_reason_code = fallback_reason.WRITER_DEADLINE_EXCEEDED

        result = SimpleNamespace(status="provider_unavailable")
        code, message = roadmap_router._classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.WRITER_DEADLINE_EXCEEDED)
        self.assertTrue(message)
        self.assertNotIn("ai-generated", message.lower())
        self.assertNotIn("success", message.lower())


# ---------------------------------------------------------------------------
# Test 8: no behavior change when deadline=None (backward compatibility)
# ---------------------------------------------------------------------------


class NoDeadlineBackwardCompatibilityTests(unittest.TestCase):
    def test_omitting_deadline_preserves_the_exact_prior_call_shape(self):
        # Would raise TypeError if cap_timeout_to_deadline were passed
        # unconditionally -- this fake matches the PRE-deadline
        # generate_json signature exactly.
        fake_chain = _StrictLegacyProviderChain(_ok(_valid_plan_payload()))
        agent = ProjectRoadmapAgent()
        agent.provider_chain = fake_chain

        response = agent.generate(_fake_request())

        self.assertTrue(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)
        self.assertEqual(len(fake_chain.calls), 1)
        self.assertIsNone(fake_chain.calls[0]["deadline"])
        self.assertGreater(len(response.phases), 0)


if __name__ == "__main__":
    unittest.main()
