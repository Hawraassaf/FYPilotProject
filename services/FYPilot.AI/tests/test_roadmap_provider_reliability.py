"""
Integration tests for how ProjectRoadmapAgent uses the JSON reliability
pipeline: it must request the schema-aware provider-repair path (pass
schema_description), use its own dedicated "roadmap" ProviderChain tier,
and classify which stage actually produced the accepted phase plan
(original / locally repaired / provider repaired) from the LLMResult's
parse_diagnostics -- all without changing the public request/response
contract.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_roadmap_agent import (  # noqa: E402
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
    ProjectRoadmapResponse,
    _ROADMAP_PHASE_PLAN_SCHEMA_DESCRIPTION,
)
from app.services.llm_provider import LLMResult  # noqa: E402


def _task(title, task_type="", **overrides):
    payload = {"title": title, "taskType": task_type}
    payload.update(overrides)
    return payload


def _valid_plan_payload() -> dict:
    """A minimal but lifecycle-complete web-project phase plan -- covers
    requirements/architecture/core/integration/testing/documentation/
    deployment so it survives ProjectRoadmapAgent's lifecycle-coverage
    check without being rejected for an unrelated reason."""
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


class _FakeProviderChain:
    def __init__(self, result: LLMResult):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt, *, use_search=False, max_tokens=None, reporter=None, schema_description=None, deadline=None):
        self.calls.append({"prompt": prompt, "schema_description": schema_description})
        return self.result


class RoadmapAgentUsesSchemaAwareRepairTests(unittest.TestCase):
    def test_schema_description_is_passed_to_the_provider_chain(self):
        agent = ProjectRoadmapAgent()
        fake_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_valid_plan_payload(),
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))
        agent.provider_chain = fake_chain

        agent.generate(_fake_request())

        self.assertEqual(len(fake_chain.calls), 1)
        self.assertEqual(fake_chain.calls[0]["schema_description"], _ROADMAP_PHASE_PLAN_SCHEMA_DESCRIPTION)

    def test_agent_uses_its_own_roadmap_tier_not_the_shared_high_tier(self):
        agent = ProjectRoadmapAgent()
        # ProviderChain doesn't expose its resolved tier directly, but the
        # Ollama leg's timeout is tier-specific -- confirming it resolves
        # to the "roadmap" default (180s) rather than "high"'s implicit
        # 90s default is a reliable proxy for "the right tier was used".
        ollama_provider = next(p for p in agent.provider_chain.providers if p.name == "ollama")
        self.assertEqual(ollama_provider.timeout_seconds, 180.0)

    def test_source_is_original_ai_candidate_when_no_repair_was_needed(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_valid_plan_payload(),
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        response = agent.generate(_fake_request())

        self.assertTrue(agent.last_llm_used)
        self.assertEqual(agent.last_generation_source, "original_ai_candidate")
        self.assertIsInstance(response, ProjectRoadmapResponse)

    def test_source_is_original_ai_candidate_repaired_after_local_repair(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_valid_plan_payload(),
            parse_diagnostics={"initialJsonValid": False, "repairMethod": "local_json_repair", "repairSuccess": True},
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_generation_source, "original_ai_candidate_repaired")

    def test_source_is_provider_repaired_ai_candidate_after_provider_repair(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_valid_plan_payload(),
            parse_diagnostics={"initialJsonValid": False, "repairMethod": "provider_repair", "repairSuccess": True},
        ))

        agent.generate(_fake_request())

        self.assertEqual(agent.last_generation_source, "provider_repaired_ai_candidate")

    def test_falls_back_when_every_repair_path_is_exhausted(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=False, provider="deepinfra", model="test-model", text="", data=None,
            error="Provider returned malformed JSON that could not be repaired.",
            error_category="invalid_json_syntax",
            parse_diagnostics={"initialJsonValid": False, "repairMethod": "provider_repair", "repairSuccess": False},
        ))

        response = agent.generate(_fake_request())

        self.assertFalse(agent.last_llm_used)
        self.assertIsNone(agent.last_generation_source)
        # The safe fallback roadmap is still a complete, valid response.
        self.assertIsInstance(response, ProjectRoadmapResponse)
        self.assertGreater(len(response.weeks), 0)


class PublicContractUnchangedTests(unittest.TestCase):
    """Section 12 'public .NET request/response contracts remain
    unchanged' -- exact field-name/type checks, independent of the JSON
    reliability changes."""

    def test_request_field_names_unchanged(self):
        expected = {
            "ideaTitle", "problemStatement", "requiredTechnologies", "requiredSkills",
            "missingSkills", "difficultyLevel", "expectedDurationWeeks", "domain",
            "finalDeliverables", "teamSize", "availableHoursPerWeek", "studentSkills",
            "skillRatings",
        }
        self.assertEqual(set(ProjectRoadmapRequest.model_fields.keys()), expected)

    def test_response_field_names_unchanged(self):
        expected = {
            "roadmapTitle", "totalWeeks", "difficultyLevel", "teamStrategy", "weeks",
            "finalAdvice", "teamSize", "hoursPerWeekPerMember", "phases",
            "planningSummary", "deferredTasks",
        }
        self.assertEqual(set(ProjectRoadmapResponse.model_fields.keys()), expected)

    def test_existing_request_payload_still_validates(self):
        request = ProjectRoadmapRequest(
            ideaTitle="X", problemStatement="Y", expectedDurationWeeks=10,
        )
        self.assertEqual(request.teamSize, 1)
        self.assertEqual(request.availableHoursPerWeek, 10)


if __name__ == "__main__":
    unittest.main()
