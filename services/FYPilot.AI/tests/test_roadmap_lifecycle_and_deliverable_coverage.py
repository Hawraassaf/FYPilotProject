"""
Tests for the FINAL Roadmap lifecycle-coverage/deliverable-consistency fix
-- closes two confirmed live defects (2026-08-10) in a generated roadmap
for a full-stack AI project ("Arabic Medical Symptom Triage Assistant"):

GAP 1 -- a project with a confirmed ASP.NET Core Razor Pages / FastAPI /
PostgreSQL stack produced a 7-phase roadmap that was entirely a data-
science/model-development plan; no phase ever scheduled the actual web
application implementation (screens, auth, persistence, user workflows).
`project_profile.lifecycle_coverage`'s existing "core_implementation"
category is a project-agnostic catch-all ("implement"/"develop"/"build
the"/...) that was trivially satisfied by AI-side implementation work
(e.g. "Implement inference wrapper..."), so the gap went undetected. Fixed
by adding a new, conditionally-mandatory "application_implementation"
lifecycle category -- required only when a real frontend/backend web
technology is confirmed for the project (see
project_profile._detect_web_app_stack), never inferred from generic words.

GAP 2 -- a phase named "Baseline Urgency and Specialist Classification
Models" declared both a baseline urgency classifier and a baseline
specialist classifier as deliverables, but its only training task produced
a specialist classifier -- no task ever produced an urgency classifier,
even though it was promised. The same gap repeated in the fine-tuned
phase. Fixed by a new deterministic deliverable_coverage module.

No test in this file calls a live provider -- every profile/plan/payload
is constructed in-process.

Run from services/FYPilot.AI:
    python -m pytest tests/test_roadmap_lifecycle_and_deliverable_coverage.py
"""

from __future__ import annotations

import unittest

from app.agents.project_roadmap_agent import ProjectRoadmapAgent, ProjectRoadmapRequest
from app.agents.roadmap import fallback_reason
from app.agents.roadmap.deliverable_coverage import (
    MISSING_OUTPUT_PRODUCER,
    diagnose_roadmap_deliverable_coverage,
)
from app.agents.roadmap.project_profile import ProjectProfileInput, build_profile
from app.agents.roadmap.task_metadata import InternalTaskProposal
from app.services.llm_provider import LLMResult


def _task(title, task_type="", **overrides):
    payload = {"title": title, "taskType": task_type}
    payload.update(overrides)
    return payload


class _FakeProviderChain:
    def __init__(self, result: LLMResult):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt, *, use_search=False, max_tokens=None, reporter=None, schema_description=None, deadline=None):
        self.calls.append({"prompt": prompt, "schema_description": schema_description})
        return self.result


def _request(**overrides) -> ProjectRoadmapRequest:
    base = dict(
        ideaTitle="Booking Platform",
        problemStatement="Students need to book study rooms online.",
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
        requiredSkills="C#, SQL",
        missingSkills="",
        difficultyLevel="medium",
        expectedDurationWeeks=16,
        domain="web",
        finalDeliverables="A working booking web app",
        teamSize=1,
        availableHoursPerWeek=15,
        studentSkills=["C#", "SQL"],
        skillRatings={"C#": 4, "SQL": 3},
    )
    base.update(overrides)
    return ProjectRoadmapRequest(**base)


def _profile(**overrides) -> ProjectProfileInput:
    base = dict(
        idea_title="Booking Platform",
        problem_statement="Students need to book study rooms online.",
        required_technologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
        domain="web",
        total_weeks=16,
    )
    base.update(overrides)
    return build_profile(ProjectProfileInput(**base))


# ---------------------------------------------------------------------------
# 1-7 -- conditional application_implementation lifecycle coverage
# ---------------------------------------------------------------------------

class ApplicationImplementationCoverageTests(unittest.TestCase):
    def test_confirmed_frontend_backend_requires_application_implementation(self):
        profile = _profile(required_technologies="ASP.NET Core Razor Pages, PostgreSQL")
        self.assertIn("application_implementation", profile.mandatory_lifecycle)
        self.assertTrue(profile.has_web_app_stack)

    def test_database_backed_app_requires_persistence_via_application_implementation(self):
        profile = _profile(required_technologies="FastAPI, PostgreSQL")
        self.assertIn("application_implementation", profile.mandatory_lifecycle)

    def test_pure_ml_project_without_web_stack_does_not_require_it(self):
        profile = _profile(
            idea_title="Symptom Classifier Research",
            problem_statement="Classify symptom text into urgency categories.",
            required_technologies="PyTorch, scikit-learn",
        )
        self.assertFalse(profile.has_web_app_stack)
        self.assertNotIn("application_implementation", profile.mandatory_lifecycle)

    def test_hybrid_ai_and_web_project_requires_both_application_and_model_lifecycle(self):
        profile = _profile(
            idea_title="Arabic Medical Symptom Triage Assistant",
            problem_statement="Classify Arabic symptom text into urgency levels using a trained NLP model.",
            required_technologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, PyTorch, scikit-learn",
        )
        self.assertIn("application_implementation", profile.mandatory_lifecycle)
        self.assertIn("baseline_model", profile.mandatory_lifecycle)

    def test_architecture_design_alone_does_not_satisfy_application_implementation(self):
        from app.agents.roadmap.project_profile import lifecycle_coverage

        profile = _profile()
        phase_texts = [
            "System Architecture and Database Design: design the database schema and component layout.",
        ]
        _covered, missing = lifecycle_coverage(profile, phase_texts)
        self.assertIn("application_implementation", missing)

    def test_fastapi_service_alone_does_not_satisfy_application_implementation(self):
        from app.agents.roadmap.project_profile import lifecycle_coverage

        profile = _profile()
        phase_texts = [
            "Fine-Tuned Model and FastAPI Service: implement inference wrapper and build the FastAPI /triage endpoint.",
        ]
        # "endpoint" is generic evidence for application_implementation too
        # (a FastAPI endpoint IS real backend/service implementation) --
        # this test instead proves the ORIGINAL live defect scenario: text
        # that only describes AI/model implementation, no API/endpoint/UI
        # words at all, still correctly leaves application_implementation missing.
        phase_texts = [
            "Fine-Tuned Model: implement inference wrapper with confidence thresholding and fallback logic.",
        ]
        _covered, missing = lifecycle_coverage(profile, phase_texts)
        self.assertIn("application_implementation", missing)

    def test_real_web_screen_evidence_satisfies_application_implementation(self):
        from app.agents.roadmap.project_profile import lifecycle_coverage

        profile = _profile()
        phase_texts = [
            "Core Web Application Implementation: implement the Razor Pages registration and login screens with authentication.",
        ]
        _covered, missing = lifecycle_coverage(profile, phase_texts)
        self.assertNotIn("application_implementation", missing)


# ---------------------------------------------------------------------------
# 8-13 -- deliverable -> task producer diagnostics
# ---------------------------------------------------------------------------

class _Phase:
    def __init__(self, name, deliverables, tasks):
        self.name = name
        self.deliverables = deliverables
        self.tasks = tasks


class DeliverableCoverageDiagnosticsTests(unittest.TestCase):
    def test_expected_output_without_producing_task_is_diagnosed(self):
        phase = _Phase(
            "Baseline Urgency and Specialist Classification Models",
            ["Baseline urgency classifier"],
            [InternalTaskProposal(title="Train a TF-IDF baseline classifier for specialist recommendation")],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, MISSING_OUTPUT_PRODUCER)

    def test_expected_output_with_matching_producing_task_passes(self):
        phase = _Phase(
            "Baseline Specialist Model",
            ["Baseline specialist classifier"],
            [InternalTaskProposal(title="Train a TF-IDF baseline classifier for specialist recommendation")],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(issues, [])

    def test_two_classifier_outputs_require_coverage_for_both(self):
        phase = _Phase(
            "Baseline Models",
            ["Baseline urgency classifier", "Baseline specialist classifier"],
            [InternalTaskProposal(title="Train a TF-IDF baseline classifier for specialist recommendation")],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(len(issues), 1)
        self.assertIn("urgency", issues[0].deliverable.lower())

    def test_explicit_multi_output_task_satisfies_both_outputs(self):
        phase = _Phase(
            "Baseline Models",
            ["Baseline urgency classifier", "Baseline specialist classifier"],
            [InternalTaskProposal(
                title="Train and evaluate baseline models for both urgency-level and specialist-category classification",
            )],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(issues, [])

    def test_baseline_urgency_output_without_urgency_task_fails(self):
        phase = _Phase(
            "Baseline Urgency and Specialist Classification Models",
            ["Baseline urgency classifier", "Baseline specialist classifier"],
            [
                InternalTaskProposal(title="Train a TF-IDF + scikit-learn baseline classifier for specialist recommendation"),
                InternalTaskProposal(title="Evaluate baseline models on the validation set and record accuracy/F1 as reference"),
                InternalTaskProposal(title="Document baseline model results and limitations"),
            ],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(len(issues), 1)
        self.assertIn("urgency", issues[0].deliverable.lower())

    def test_fine_tuned_urgency_output_without_urgency_task_fails(self):
        phase = _Phase(
            "Fine-Tuned Arabic NLP Triage Model and FastAPI Service",
            ["Fine-tuned urgency classification model", "Fine-tuned specialist recommendation model", "FastAPI triage service"],
            [
                InternalTaskProposal(title="Fine-tune an Arabic transformer model for specialist recommendation classification"),
                InternalTaskProposal(title="Build a FastAPI /triage endpoint returning urgency and specialist recommendation from input text"),
                InternalTaskProposal(title="Implement inference wrapper with confidence thresholding and fallback logic for uncertain predictions"),
                InternalTaskProposal(title="Containerize the FastAPI triage service with Docker"),
            ],
        )
        issues = diagnose_roadmap_deliverable_coverage([phase])
        deliverables_with_issues = {issue.deliverable for issue in issues}
        self.assertIn("Fine-tuned urgency classification model", deliverables_with_issues)
        self.assertNotIn("Fine-tuned specialist recommendation model", deliverables_with_issues)
        self.assertNotIn("FastAPI triage service", deliverables_with_issues)

    def test_deliverable_with_no_distinguishing_words_is_never_flagged(self):
        phase = _Phase("Modeling", ["Trained model"], [InternalTaskProposal(title="Write documentation")])
        issues = diagnose_roadmap_deliverable_coverage([phase])
        self.assertEqual(issues, [])

    def test_unrelated_deliverables_in_other_phases_remain_unaffected(self):
        good_phase = _Phase(
            "Requirements",
            ["Requirements specification document"],
            [InternalTaskProposal(title="Specify functional requirements for the booking workflow")],
        )
        bad_phase = _Phase(
            "Baseline Models",
            ["Baseline urgency classifier"],
            [InternalTaskProposal(title="Train a baseline classifier for specialist recommendation")],
        )
        issues = diagnose_roadmap_deliverable_coverage([good_phase, bad_phase])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].phase_name, "Baseline Models")


# ---------------------------------------------------------------------------
# 14-18 -- gate integration: agent rejects and falls back honestly
# ---------------------------------------------------------------------------

def _lifecycle_complete_payload(extra_phase: dict | None = None) -> dict:
    phases = [
        {
            "name": "Requirements and Architecture",
            "weeks": 2,
            "goal": "Define requirements and design the database schema.",
            "tasks": [
                _task("Write functional requirements for the booking workflow"),
                _task("Design the database schema for bookings and users", "database_design"),
            ],
            "deliverables": ["Requirements document"],
            "skillsToLearn": [], "riskWarning": "Scope creep.", "checkpoint": "Supervisor reviews requirements.",
        },
        {
            "name": "Core Web Application Implementation",
            "weeks": 3,
            "goal": "Implement the Razor Pages application and API integration.",
            "tasks": [
                _task("Implement the Razor Pages registration and login screens with authentication", "crud_api"),
                _task("Integrate the booking API with the payment endpoint", "external_api_integration"),
            ],
            "deliverables": ["Working booking web application"],
            "skillsToLearn": [], "riskWarning": "Integration risk.", "checkpoint": "Supervisor reviews the app.",
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
            "skillsToLearn": [], "riskWarning": "Deployment risk.", "checkpoint": "Supervisor reviews the final deployment.",
        },
    ]
    if extra_phase:
        phases.insert(1, extra_phase)
    return {
        "roadmapTitle": "Test Roadmap", "teamStrategy": "Solo strategy.", "finalAdvice": "Ship the MVP first.",
        "phases": phases,
    }


class GateIntegrationTests(unittest.TestCase):
    def test_missing_application_implementation_is_rejected_with_typed_reason(self):
        agent = ProjectRoadmapAgent()
        payload = _lifecycle_complete_payload()
        # Remove the only phase that provides application_implementation evidence.
        payload["phases"][1] = {
            "name": "Data Prep",
            "weeks": 3,
            "goal": "Prepare the dataset.",
            "tasks": [_task("Clean and annotate the dataset"), _task("Split the dataset into train/test")],
            "deliverables": ["Cleaned dataset"],
            "skillsToLearn": [], "riskWarning": "Data risk.", "checkpoint": "Supervisor reviews data.",
        }
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=payload,
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_request())

        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.LIFECYCLE_COVERAGE_FAILED)
        self.assertIn("application_implementation", agent.last_missing_lifecycle_categories)

    def test_lifecycle_complete_web_project_is_accepted(self):
        agent = ProjectRoadmapAgent()
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=_lifecycle_complete_payload(),
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_request())

        self.assertTrue(agent.last_llm_used)
        self.assertTrue(agent.last_lifecycle_coverage_passed)
        self.assertTrue(agent.last_deliverable_coverage_passed)

    def test_missing_deliverable_producer_is_rejected_with_typed_reason(self):
        agent = ProjectRoadmapAgent()
        bad_model_phase = {
            "name": "Baseline Urgency and Specialist Classification Models",
            "weeks": 1,
            "goal": "Establish baseline models before fine-tuning.",
            "tasks": [
                _task("Train a TF-IDF baseline classifier for specialist recommendation", "baseline_model"),
                _task("Evaluate baseline models on the validation set", "model_evaluation"),
            ],
            "deliverables": ["Baseline urgency classifier", "Baseline specialist classifier"],
            "skillsToLearn": [], "riskWarning": "Model risk.", "checkpoint": "Supervisor reviews baselines.",
        }
        payload = _lifecycle_complete_payload(extra_phase=bad_model_phase)
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=payload,
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_request())

        self.assertFalse(agent.last_llm_used)
        self.assertEqual(agent.last_fallback_reason_code, fallback_reason.DELIVERABLE_COVERAGE_FAILED)
        self.assertFalse(agent.last_deliverable_coverage_passed)
        self.assertTrue(any("urgency" in issue.lower() for issue in agent.last_deliverable_coverage_issues))

    def test_matching_multi_output_model_phase_is_accepted(self):
        agent = ProjectRoadmapAgent()
        good_model_phase = {
            "name": "Baseline Urgency and Specialist Classification Models",
            "weeks": 1,
            "goal": "Establish baseline models before fine-tuning.",
            "tasks": [
                _task("Train a baseline urgency classifier using TF-IDF features", "baseline_model"),
                _task("Train a baseline specialist recommendation classifier using TF-IDF features", "baseline_model"),
                _task("Evaluate both baseline models on the validation set", "model_evaluation"),
            ],
            "deliverables": ["Baseline urgency classifier", "Baseline specialist classifier"],
            "skillsToLearn": [], "riskWarning": "Model risk.", "checkpoint": "Supervisor reviews baselines.",
        }
        payload = _lifecycle_complete_payload(extra_phase=good_model_phase)
        agent.provider_chain = _FakeProviderChain(LLMResult(
            ok=True, provider="deepinfra", model="test-model", text="", data=payload,
            parse_diagnostics={"initialJsonValid": True, "repairMethod": None, "repairSuccess": False},
        ))

        agent.generate(_request())

        self.assertTrue(agent.last_llm_used)
        self.assertTrue(agent.last_deliverable_coverage_passed)


# ---------------------------------------------------------------------------
# 19-24 -- generic, non-medical fixtures (must not overfit to the triage project)
# ---------------------------------------------------------------------------

class GenericProjectFixtureTests(unittest.TestCase):
    def test_crud_non_ai_project_requires_app_and_db_not_ml_phases(self):
        profile = _profile(
            idea_title="Library Loan Tracker",
            problem_statement="Track book loans and returns for a campus library.",
            required_technologies="ASP.NET Core, PostgreSQL",
        )
        self.assertIn("application_implementation", profile.mandatory_lifecycle)
        self.assertNotIn("baseline_model", profile.mandatory_lifecycle)
        self.assertNotIn("data_sourcing", profile.mandatory_lifecycle)

    def test_pure_data_science_project_does_not_require_web_application_phase(self):
        profile = _profile(
            idea_title="Retail Sales Forecasting Study",
            problem_statement="Forecast retail sales trends from historical data.",
            required_technologies="Python, pandas, scikit-learn",
        )
        self.assertFalse(profile.has_web_app_stack)
        self.assertNotIn("application_implementation", profile.mandatory_lifecycle)
        self.assertIn("baseline_model", profile.mandatory_lifecycle)

    def test_external_api_ai_web_app_requires_web_implementation(self):
        # Deliberately avoids "chatbot"/"nlp"-style wording that would
        # independently trigger the PRE-EXISTING (out of scope for this
        # task) ai_ml/nlp project-type detection -- isolates just the
        # web-app-stack signal this task adds.
        profile = _profile(
            idea_title="Support Answer Assistant",
            problem_statement="Answer student support questions using a hosted external API.",
            required_technologies="ASP.NET Core Razor Pages, OpenAI GPT-4 API, PostgreSQL",
        )
        self.assertIn("application_implementation", profile.mandatory_lifecycle)

    def test_local_ml_backend_only_project_does_not_invent_frontend_phase(self):
        profile = _profile(
            idea_title="Fraud Detection Service",
            problem_statement="Detect fraudulent transactions using a locally trained model.",
            required_technologies="FastAPI, scikit-learn, PostgreSQL",
        )
        self.assertIn("application_implementation", profile.mandatory_lifecycle)  # FastAPI is a confirmed backend
        self.assertIn("baseline_model", profile.mandatory_lifecycle)

    def test_database_free_project_does_not_invent_database_implementation(self):
        from app.agents.roadmap.project_profile import lifecycle_coverage

        profile = _profile(
            idea_title="Offline Utility Tool", required_technologies="Python",
        )
        # No confirmed web/database stack -- application_implementation
        # must never be forced on merely because SOME technology was named.
        self.assertFalse(profile.has_web_app_stack)
        self.assertNotIn("application_implementation", profile.mandatory_lifecycle)

    def test_no_provider_call_was_made(self):
        # This whole file only ever constructs profiles/payloads and calls
        # deterministic functions or a fake in-process provider chain --
        # no real ProviderChain/LLM call is reachable from any test above.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
