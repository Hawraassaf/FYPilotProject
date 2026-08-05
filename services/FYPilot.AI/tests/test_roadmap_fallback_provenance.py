"""
Tests for Roadmap fallback provenance: every rejection point inside
ProjectRoadmapAgent must set a precise, typed fallback_reason_code (never
just "provider_unavailable" for everything), and the router must turn that
into an honest outputOrigin/fallbackUsed/fallbackReasonCode/
fallbackReasonMessage response -- never labeling a deterministic fallback
as AI-generated, and never collapsing distinct failure categories together.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
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
from app.services.llm_provider import LLMResult  # noqa: E402


def _task(title, task_type="", **overrides):
    payload = {"title": title, "taskType": task_type}
    payload.update(overrides)
    return payload


def _valid_plan_payload(phase_count: int = 3) -> dict:
    base_phases = [
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
    ]
    return {
        "roadmapTitle": "Test Roadmap",
        "teamStrategy": "Solo strategy.",
        "finalAdvice": "Ship the MVP first.",
        "phases": base_phases[:phase_count],
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


class _FakeProviderChain:
    def __init__(self, result=None, exception=None):
        self.result = result
        self.exception = exception
        self.calls: list[dict] = []

    def generate_json(self, prompt, *, use_search=False, max_tokens=None, reporter=None, schema_description=None, deadline=None):
        self.calls.append({"prompt": prompt})
        if self.exception is not None:
            raise self.exception
        return self.result


class AgentFallbackReasonClassificationTests(unittest.TestCase):
    """Each distinct rejection point inside ProjectRoadmapAgent must set its
    own precise last_fallback_reason_code, never a single generic value."""

    def test_transport_failure_maps_to_provider_unavailable(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="deepinfra", model=None, text="", data=None,
            error="Connection refused", error_category="transport_failure",
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_UNAVAILABLE)

    def test_timeout_category_maps_to_provider_timeout(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="deepinfra", model=None, text="", data=None,
            error="Request timed out", error_category="timeout",
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_TIMEOUT)

    def test_http_429_maps_to_rate_limited(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="groq", model=None, text="", data=None,
            error="HTTP 429 Too Many Requests", error_category="provider_http_error",
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_RATE_LIMITED)

    def test_http_401_maps_to_authentication_failed(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="deepinfra", model=None, text="", data=None,
            error="HTTP 401 Unauthorized", error_category="provider_http_error",
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_AUTHENTICATION_FAILED)

    def test_invalid_json_syntax_maps_to_json_parse_failed(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="deepinfra", model="test-model", text="", data=None,
            error="Provider returned malformed JSON that could not be repaired.",
            error_category="invalid_json_syntax",
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.JSON_PARSE_FAILED)

    def test_exception_raised_by_provider_chain_maps_to_provider_unavailable(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(exception=RuntimeError("boom"))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_UNAVAILABLE)

    def test_exception_mentioning_timeout_maps_to_provider_timeout(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(exception=TimeoutError("read timeout exceeded"))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.PROVIDER_TIMEOUT)

    def test_fewer_than_three_usable_phases_maps_to_insufficient_usable_phases(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="",
            data=_valid_plan_payload(phase_count=1),
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.INSUFFICIENT_USABLE_PHASES)
        self.assertEqual(agent.last_usable_phase_count, 1)

    def test_lifecycle_gap_maps_to_lifecycle_coverage_failed(self):
        agent = ProjectRoadmapAgent()
        # Three phases, all about requirements -- deliberately missing
        # core_implementation/testing_validation/final_delivery so the
        # blocking lifecycle gate rejects it.
        payload = {
            "roadmapTitle": "Test Roadmap",
            "teamStrategy": "Solo strategy.",
            "finalAdvice": "Ship the MVP first.",
            "phases": [
                {
                    "name": f"Requirements Discussion Round {i}",
                    "weeks": 1,
                    "goal": "Discuss requirements.",
                    "tasks": [
                        _task(f"Interview stakeholder group {i} about requirements"),
                        _task(f"Write meeting notes for requirements round {i}"),
                    ],
                    "deliverables": ["Meeting notes"],
                    "skillsToLearn": [],
                    "riskWarning": "Scope creep.",
                    "checkpoint": "Supervisor reviews notes.",
                }
                for i in range(1, 4)
            ],
        }
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=payload,
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.LIFECYCLE_COVERAGE_FAILED)
        self.assertFalse(agent.last_lifecycle_coverage_passed)
        self.assertGreater(len(agent.last_missing_lifecycle_categories), 0)

    def test_blocked_term_tasks_are_counted(self):
        agent = ProjectRoadmapAgent()
        payload = _valid_plan_payload(phase_count=3)
        # Add a React task alongside two genuine tasks so the phase still
        # survives the "at least 2 real tasks" floor, but the blocked term
        # is dropped and counted.
        payload["phases"][0]["tasks"].append(_task("Set up the React frontend project"))
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=payload,
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_fake_request())

        self.assertTrue(agent.last_llm_used)
        self.assertEqual(agent.last_blocked_term_tasks_dropped, 1)

    def test_successful_generation_leaves_fallback_reason_code_none(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_valid_plan_payload(),
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_fake_request())

        self.assertTrue(agent.last_llm_used)
        self.assertIsNone(agent.last_fallback_reason_code)


class RouterFallbackClassificationTests(unittest.TestCase):
    """_classify_fallback must map every non-usable PipelineResult.status to
    a distinct typed reason, and never fall through to "unknown" for a
    status it already knows how to name."""

    def test_provider_unavailable_status_defers_to_agent_reason_code(self):
        from app.routers.roadmap import _classify_fallback

        result = SimpleNamespace(status="provider_unavailable")
        agent = SimpleNamespace(last_fallback_reason_code=fallback_reason.INSUFFICIENT_USABLE_PHASES)

        code, message = _classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.INSUFFICIENT_USABLE_PHASES)
        self.assertTrue(message)

    def test_firewall_blocked_maps_to_blocked_content(self):
        from app.routers.roadmap import _classify_fallback

        result = SimpleNamespace(status="firewall_blocked")
        agent = SimpleNamespace(last_fallback_reason_code=None)

        code, _message = _classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.BLOCKED_CONTENT)

    def test_schema_invalid_maps_to_schema_invalid(self):
        from app.routers.roadmap import _classify_fallback

        result = SimpleNamespace(status="schema_invalid")
        agent = SimpleNamespace(last_fallback_reason_code=None)

        code, _message = _classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.SCHEMA_INVALID)

    def test_rejected_maps_to_semantic_rewrite_failed(self):
        from app.routers.roadmap import _classify_fallback

        result = SimpleNamespace(status="rejected")
        agent = SimpleNamespace(last_fallback_reason_code=None)

        code, _message = _classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.SEMANTIC_REWRITE_FAILED)

    def test_review_unavailable_maps_to_reviewer_unavailable(self):
        from app.routers.roadmap import _classify_fallback

        result = SimpleNamespace(status="review_unavailable")
        agent = SimpleNamespace(last_fallback_reason_code=None)

        code, _message = _classify_fallback(result, agent)

        self.assertEqual(code, fallback_reason.REVIEWER_UNAVAILABLE)


class RouterResponseShapeTests(unittest.TestCase):
    """End-to-end: the actual FastAPI route function must build an honest
    outputOrigin/fallbackUsed/fallbackReasonCode response, never label a
    deterministic fallback as ai_generated."""

    def _run_route(self, *, usable: bool, status: str, output: dict, agent_patches: dict):
        from app.routers import roadmap as roadmap_router

        fake_pipeline_result = SimpleNamespace(
            usable=usable, status=status, output=output, outputOrigin="writer",
        )

        request = _fake_request()

        with patch.object(roadmap_router, "ReviewPipeline") as MockPipeline, \
             patch.object(roadmap_router, "ProjectRoadmapAgent") as MockAgent, \
             patch.object(roadmap_router, "build_review_response", return_value={"status": status}):
            mock_agent_instance = MockAgent.return_value
            mock_agent_instance.build_safe_fallback.return_value = SimpleNamespace(
                model_dump=lambda: {"roadmapTitle": "Fallback Roadmap"},
            )
            for key, value in agent_patches.items():
                setattr(mock_agent_instance, key, value)

            MockPipeline.return_value.run.return_value = fake_pipeline_result

            return roadmap_router.generate_project_roadmap(request)

    def test_usable_result_is_labeled_ai_generated_not_fallback(self):
        response = self._run_route(
            usable=True, status="approved", output={"roadmapTitle": "Real Roadmap"},
            agent_patches=dict(
                last_llm_used=True, last_provider="deepinfra", last_model_used="test-model",
                last_error=None, last_raw_llm_response=None, last_generation_source="original_ai_candidate",
                last_usable_phase_count=3, last_lifecycle_coverage_passed=True,
                last_missing_lifecycle_categories=[], last_blocked_term_tasks_dropped=0,
            ),
        )

        self.assertEqual(response["outputOrigin"], "ai_generated")
        self.assertFalse(response["fallbackUsed"])
        self.assertIsNone(response["fallbackReasonCode"])
        self.assertEqual(response["roadmap"], {"roadmapTitle": "Real Roadmap"})
        self.assertIn("requestId", response)

    def test_non_usable_result_is_labeled_deterministic_fallback(self):
        response = self._run_route(
            usable=False, status="provider_unavailable", output={},
            agent_patches=dict(
                last_llm_used=False, last_provider=None, last_model_used=None,
                last_error="No AI provider returned a valid roadmap phase plan.",
                last_raw_llm_response=None, last_generation_source=None,
                last_fallback_reason_code=fallback_reason.PROVIDER_TIMEOUT,
                last_usable_phase_count=0, last_lifecycle_coverage_passed=True,
                last_missing_lifecycle_categories=[], last_blocked_term_tasks_dropped=0,
            ),
        )

        self.assertEqual(response["outputOrigin"], "deterministic_fallback")
        self.assertTrue(response["fallbackUsed"])
        self.assertEqual(response["fallbackReasonCode"], fallback_reason.PROVIDER_TIMEOUT)
        self.assertTrue(response["fallbackReasonMessage"])
        # The roadmap actually shown is the safe fallback, never the empty
        # non-usable candidate.
        self.assertEqual(response["roadmap"], {"roadmapTitle": "Fallback Roadmap"})


if __name__ == "__main__":
    unittest.main()
