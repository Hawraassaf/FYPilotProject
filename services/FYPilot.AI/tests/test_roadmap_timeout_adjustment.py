"""
Tests for the Roadmap-only timeout adjustment (90s -> 240s -> 360s -> 540s
total budget).

Root cause / intent this covers: the Roadmap pipeline's original 90s total
budget could not fit DeepInfra's own single-attempt cap
(ROADMAP_DEEPINFRA_TIMEOUT_SECONDS) even once, let alone leave any budget for
a Groq/Ollama fallback after it -- see routers/roadmap.py's
_SEMANTIC_REVIEW_RESERVE_SECONDS docstring. A first pass raised the total to
240s / DeepInfra cap to 120s, which was still observed live cutting DeepInfra
off mid-response. A second pass raised the total to 360s (registry.py's
ProjectRoadmapAgent.max_total_seconds) and the DeepInfra single-attempt cap
to 280s (further raised from an intermediate 240s after switching the
roadmap tier's model to Claude Sonnet 5, which needed more than 240s to
finish a full detailed 13-phase plan), keeping the review reserve at 60s
(effective Writer budget 300s).

This batch raises the total again, 360s -> 540s: live-observed a genuine
(non-retry-storm, already-fixed-separately) case where a rewrite attempt hit
Anthropic's own ~120s timeout, the DeepInfra fallback attempt then ALSO hit
~116s before timing out, and those two consecutive real provider slowdowns
alone consumed nearly the entire remaining 360s budget after a ~105s Writer
call. 540s keeps a 60s safety margin under the .NET side's HttpClient.Timeout
for this endpoint (AiServiceClient.cs's shared 600s _http client) while
giving real headroom for two slow-but-real provider attempts during
Reviewer/Rewrite to both complete instead of racing the deadline. The review
reserve itself stays 60s (a FLOOR guaranteeing the Writer can never consume
the entire budget, not a ceiling on how much time Reviewer/Rewrite actually
get -- see registry.py's ProjectRoadmapAgent comment), so the effective
Writer budget is now 480s. DeepInfra remains the paid primary provider
(never disabled/reordered) and the provider order (Anthropic -> DeepInfra ->
Groq -> Ollama) is unchanged. These tests also cover the misleading
"transport=success" log line that used to print even when the whole
provider cascade returned no usable output, and confirm every OTHER agent's
max_total_seconds is untouched by this change.

No real network calls, no real sleeps -- fake providers only, wired into the
REAL (unmodified) ProviderChain so its own timeout/clamping logic runs for
real.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import time
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_roadmap_agent import (  # noqa: E402
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
)
from app.review.registry import get_agent_config  # noqa: E402
from app.routers import roadmap as roadmap_router  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    LLMResult,
    ProviderChain,
    _deepinfra_timing_for_tier,
)


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


# ---------------------------------------------------------------------------
# Test 1: the 540s / 480s / 60s timing contract
# ---------------------------------------------------------------------------


class RoadmapTimingContractTests(unittest.TestCase):
    def test_registry_total_budget_is_540_seconds(self):
        config = get_agent_config("ProjectRoadmapAgent")
        self.assertEqual(config.max_total_seconds, 540.0)

    def test_review_reserve_is_60_seconds(self):
        self.assertEqual(roadmap_router._SEMANTIC_REVIEW_RESERVE_SECONDS, 60.0)

    def test_effective_writer_budget_is_480_seconds(self):
        config = get_agent_config("ProjectRoadmapAgent")
        writer_budget = config.max_total_seconds - roadmap_router._SEMANTIC_REVIEW_RESERVE_SECONDS
        self.assertEqual(writer_budget, 480.0)

    def test_router_derives_writer_deadline_from_the_540_60_split(self):
        started = time.monotonic()
        global_deadline = started + get_agent_config("ProjectRoadmapAgent").max_total_seconds
        writer_deadline = global_deadline - roadmap_router._SEMANTIC_REVIEW_RESERVE_SECONDS

        self.assertAlmostEqual(writer_deadline - started, 480.0, delta=0.05)
        self.assertAlmostEqual(global_deadline - started, 540.0, delta=0.05)


# ---------------------------------------------------------------------------
# Test 2: DeepInfra's single-attempt cap stays at 240s
# ---------------------------------------------------------------------------


class DeepInfraAttemptCapTests(unittest.TestCase):
    def test_roadmap_tier_deepinfra_timeout_is_280_seconds(self):
        timing = _deepinfra_timing_for_tier("roadmap")
        self.assertEqual(timing["timeout_seconds"], 280.0)

    def test_280s_deepinfra_cap_fits_inside_the_480s_writer_budget_with_room_for_fallback(self):
        # Raised from 240s -> 280s after live testing showed Claude Sonnet 5
        # (the "roadmap" tier's model as of the Sonnet switch) sometimes
        # needs more than 240s to finish a full detailed 13-phase plan.
        # writer_budget itself was later raised 300.0 -> 480.0 (the total
        # 360s -> 540s change, module docstring above) -- the fallback
        # window this margin describes is no longer the narrow 20s it used
        # to be (accepted at the time because Groq was independently
        # exhausting its own daily rate limit); it's now a comfortable 200s,
        # which is exactly the live-observed gap (two consecutive real
        # provider timeouts nearly exhausting the old, narrower budget) this
        # retune exists to close.
        writer_budget = 480.0
        deepinfra_cap = _deepinfra_timing_for_tier("roadmap")["timeout_seconds"]
        remaining_for_fallback = writer_budget - deepinfra_cap

        self.assertLess(deepinfra_cap, writer_budget)
        self.assertAlmostEqual(remaining_for_fallback, 200.0, delta=0.5)


# ---------------------------------------------------------------------------
# Test 3: fallback (Groq/Ollama) still gets started after a slow DeepInfra
# attempt, using the real ProviderChain cascade + deadline clamp
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, name: str, *, ok: bool = True, consumes_seconds: float = 0.0):
        self.name = name
        self._ok = ok
        self._consumes_seconds = consumes_seconds
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
                data={
                    "roadmapTitle": "Test Roadmap",
                    "teamStrategy": "Solo strategy.",
                    "finalAdvice": "Ship the MVP first.",
                    "phases": [
                        {
                            "name": "Requirements and Architecture",
                            "weeks": 2,
                            "goal": "Define requirements and design the database schema.",
                            "tasks": [
                                {"localId": "T1", "title": "Write functional requirements for booking",
                                 "taskType": "documentation_presentation"},
                                {"localId": "T2", "title": "Design the database schema for bookings",
                                 "taskType": "database_design"},
                            ],
                            "deliverables": ["Requirements document"],
                            "skillsToLearn": [],
                            "riskWarning": "Scope creep.",
                            "checkpoint": "Supervisor reviews requirements.",
                        },
                        {
                            "name": "Core Booking Implementation",
                            "weeks": 3,
                            "goal": "Implement the core booking workflow.",
                            "tasks": [
                                {"localId": "T1", "title": "Implement the core booking workflow endpoint",
                                 "taskType": "crud_api"},
                                {"localId": "T2", "title": "Integrate the booking API with the payment endpoint",
                                 "taskType": "external_api_integration"},
                            ],
                            "deliverables": ["Working booking API"],
                            "skillsToLearn": [],
                            "riskWarning": "Integration risk.",
                            "checkpoint": "Supervisor reviews the API.",
                        },
                        {
                            "name": "Testing and Deployment",
                            "weeks": 2,
                            "goal": "Test and deploy the final build.",
                            "tasks": [
                                {"localId": "T1", "title": "Test the booking workflow end to end",
                                 "taskType": "functional_testing"},
                                {"localId": "T2", "title": "Write user documentation and deploy the final build",
                                 "taskType": "documentation_presentation"},
                            ],
                            "deliverables": ["Test report", "Deployed build"],
                            "skillsToLearn": [],
                            "riskWarning": "Deployment risk.",
                            "checkpoint": "Supervisor reviews the final deployment.",
                        },
                    ],
                },
            )

        return LLMResult(
            ok=False, provider=self.name, model="fake-model", text="", data=None,
            error="fake provider failure", error_category="provider_http_error",
        )


class FallbackAvailabilityTests(unittest.TestCase):
    def test_groq_fallback_still_starts_after_deepinfra_fails_within_the_writer_budget(self):
        deepinfra = _FakeProvider("fake-deepinfra", ok=False)
        groq = _FakeProvider("fake-groq", ok=True)
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[deepinfra, groq])

        writer_deadline = time.monotonic() + 480.0
        agent.generate(_fake_request(), deadline=writer_deadline)

        self.assertEqual(deepinfra.call_count, 1)
        self.assertEqual(groq.call_count, 1)
        self.assertTrue(agent.last_llm_used)
        self.assertEqual(agent.last_provider, "fake-groq")


# ---------------------------------------------------------------------------
# Test 4: provider order is preserved (DeepInfra -> Groq -> Ollama)
# ---------------------------------------------------------------------------


class ProviderOrderTests(unittest.TestCase):
    def test_roadmap_tier_provider_chain_order_is_anthropic_groq_deepinfra_ollama(self):
        # Anthropic (direct API) leads -- a brief Groq-first experiment was
        # reverted after live testing showed gpt-oss-120b failing the
        # deliverable-coverage content gate on every attempt (2/2 full
        # requests, 4/4 individual attempts), falling through to the
        # deterministic fallback template every time despite much faster
        # per-call latency than Claude. See ProviderChain.__init__'s
        # "roadmap"-only comment.
        #
        # Groq before DeepInfra (steps 1/2) is still "roadmap"-only: the
        # student has a paid Groq subscription with full gpt-oss-120b
        # access, so Groq is tried for its speed ahead of DeepInfra as the
        # backup leg. "se_documentation" (unaffected by either change)
        # still keeps DeepInfra before Groq -- see AnthropicProvider's
        # docstring for why that tier prepends Anthropic at all.
        chain = ProviderChain(tier="roadmap")
        names = [type(p).__name__ for p in chain.providers]
        self.assertEqual(names, ["AnthropicProvider", "GroqProvider", "DeepInfraProvider", "OllamaProvider"])


# ---------------------------------------------------------------------------
# Test 5: the misleading "transport=success" log is corrected when no
# provider returned valid output
# ---------------------------------------------------------------------------


class TransportLogCorrectionTests(unittest.TestCase):
    def test_transport_logged_as_failure_when_the_whole_cascade_returns_no_output(self):
        # Mirrors ProviderChain._run_cascade's real "all providers failed"
        # result shape: provider="none", error_category=None -- this is
        # exactly the case that used to print "transport=success" even
        # though no provider ever returned usable output.
        agent = ProjectRoadmapAgent()
        agent.provider_chain = ProviderChain(providers=[_FakeProvider("fake", ok=False)])

        with self.assertLogs("fypilot-roadmap-agent", level="INFO") as captured:
            agent.generate(_fake_request(), deadline=None)

        provider_output_lines = [line for line in captured.output if "roadmap.provider_output" in line]
        self.assertTrue(provider_output_lines, "expected a roadmap.provider_output log line")
        self.assertIn("transport=failure", provider_output_lines[-1])
        self.assertNotIn("transport=success", provider_output_lines[-1])


# ---------------------------------------------------------------------------
# Test 6: no other agent's timing/prompts/schemas/scheduling changed
# ---------------------------------------------------------------------------


class OtherAgentsUnchangedTests(unittest.TestCase):
    def test_other_agent_total_budgets_are_unchanged(self):
        expected = {
            "FypMentorAgent": 90.0,
            "SEDocumentationAgent": 1200.0,
            "ProjectIdeaAgent": 120.0,
            # 90.0 -> 150.0: a later, unrelated freeze-audit fix (see
            # test_project_dna_writer_deadline.py's
            # OtherAgentRegistryTimingUnchangedTests for the live-measured
            # rationale) -- not something this roadmap-tuning task touched.
            "ProjectDNAAgent": 150.0,
            "IdeaComparisonAgent": 45.0,
            "MarketFootprintAgent": 150.0,
            "MarketNeedsAgent": 120.0,
            "DefenseQuestionAgent": 90.0,
            "DefenseEvaluatorAgent": 90.0,
        }

        for agent_name, expected_seconds in expected.items():
            with self.subTest(agent=agent_name):
                config = get_agent_config(agent_name)
                self.assertEqual(config.max_total_seconds, expected_seconds)

    def test_other_tiers_deepinfra_timeout_still_unaffected_by_roadmap_tuning(self):
        standard_timing = _deepinfra_timing_for_tier("standard")
        roadmap_timing = _deepinfra_timing_for_tier("roadmap")

        self.assertNotEqual(standard_timing.get("timeout_seconds"), roadmap_timing.get("timeout_seconds"))


if __name__ == "__main__":
    unittest.main()
