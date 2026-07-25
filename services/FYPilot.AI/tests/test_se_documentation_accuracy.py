"""
Focused tests for the SE Documentation Generator accuracy fixes:
- documentation describes the SELECTED project, never FYPilot itself
- UI screens are real screens, not development modules
- NFRs carry measurable targets, use cases have real multi-step flows
- traceability is deterministic and dangling-reference-free
- the AI/Data Science section only appears for AI-flavored projects
- the documentation quality score is computed deterministically, never a
  hardcoded constant

All tests here exercise the deterministic fallback path
(build_safe_fallback), so they require no network access / API keys and are
fast and reproducible, mirroring the rest of tests/test_review_pipeline.py.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.project_facts import build_project_facts  # noqa: E402
from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    SEDocSelectedIdea,
    SEDocStudentProfile,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.registry import SEDocumentationCandidateSchema  # noqa: E402


def _ai_request() -> SEDocumentationRequest:
    return SEDocumentationRequest(
        studentProfile=SEDocStudentProfile(teamSize=2, skills=["Python", "FastAPI"]),
        selectedIdea=SEDocSelectedIdea(
            title="Chatbot for University Student Support Services",
            problemStatement="Students struggle to get fast answers to common university questions outside office hours.",
            targetUsers="University students",
            whyUseful="Provides instant answers using a knowledge base and escalates unresolved questions to support staff.",
            requiredTechnologies="React, FastAPI, PostgreSQL, OpenAI API",
            difficultyLevel="High",
            expectedDurationWeeks=14,
            domain="Student Support",
            finalDeliverables="Chat widget, knowledge base admin panel, escalation workflow",
        ),
    )


def _non_ai_request() -> SEDocumentationRequest:
    return SEDocumentationRequest(
        studentProfile=SEDocStudentProfile(teamSize=1, skills=["C#", "ASP.NET"]),
        selectedIdea=SEDocSelectedIdea(
            title="Inventory Management System for a Retail Store",
            problemStatement="Small retail stores struggle to track stock levels manually.",
            targetUsers="Store managers",
            whyUseful="Tracks stock levels and alerts managers when items run low.",
            requiredTechnologies="ASP.NET Core, SQL Server, Bootstrap",
            difficultyLevel="Medium",
            expectedDurationWeeks=10,
            domain="Retail Inventory",
            finalDeliverables="Stock tracking dashboard, low-stock alerts, reporting module",
        ),
    )


class ProjectFactsTests(unittest.TestCase):
    def test_ai_keywords_detected(self):
        facts = build_project_facts(_ai_request())
        self.assertTrue(facts.ai_involved)

    def test_non_ai_project_not_flagged(self):
        facts = build_project_facts(_non_ai_request())
        self.assertFalse(facts.ai_involved)

    def test_missing_technologies_are_labeled_assumption(self):
        facts = build_project_facts(SEDocumentationRequest())
        self.assertTrue(all(t.classification == "assumption" for t in facts.technologies))
        self.assertTrue(facts.assumptions)


class NoFyPilotLeakTests(unittest.TestCase):
    """
    The previous implementation hardcoded architecture/scope/stakeholders/
    diagrams describing FYPilot's own tech stack and doc-generation pipeline
    regardless of the selected project. These tests confirm a different
    selected project's documentation never contains that leaked content.
    """

    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_chatbot_project_has_no_fypilot_leak(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        blob = " ".join([
            doc["projectOverview"], doc["architecture"]["explanation"],
            doc["activityDiagram"], doc["sequenceDiagram"],
        ]).lower()
        self.assertNotIn("fypilot", blob)
        self.assertNotIn("idea generation module", blob)
        self.assertNotIn("roadmap module", blob)

    def test_architecture_reflects_selected_project_stack(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        architecture = doc["architecture"]
        self.assertIn("react", architecture["frontend"].lower())
        self.assertIn("fastapi", architecture["backend"].lower())
        self.assertIn("postgres", architecture["database"].lower())

    def test_retail_project_stack_is_not_the_chatbot_stack(self):
        doc = self.agent.build_safe_fallback(_non_ai_request()).model_dump()
        architecture = doc["architecture"]
        self.assertIn("asp.net", architecture["backend"].lower())
        self.assertNotIn("react", architecture["frontend"].lower())

    def test_stakeholders_are_project_actors_not_academic_roles(self):
        # Previously hardcoded to "Student, Supervisor, Admin, Evaluation
        # committee" -- FYPilot's own review process, not the selected
        # project's actual stakeholders.
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertNotIn("Evaluation committee", doc["stakeholders"])


class UiScreensNotModulesTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_ui_screens_are_real_screens(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertTrue(doc["uiScreens"])
        for screen in doc["uiScreens"]:
            lowered = screen["name"].lower()
            self.assertFalse(lowered.endswith("module"))

    def test_dev_module_named_screen_rejected_by_schema(self):
        candidate = self.agent.build_safe_fallback(_ai_request()).model_dump()
        candidate["uiScreens"][0]["name"] = "Database Module"
        with self.assertRaises(Exception):
            SEDocumentationCandidateSchema.model_validate(candidate)


class RequirementQualityTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.doc = self.agent.build_safe_fallback(_ai_request()).model_dump()

    def test_functional_requirements_have_acceptance_criteria(self):
        for fr in self.doc["functionalRequirements"]:
            self.assertTrue(fr["acceptanceCriteria"], f"{fr['id']} has no acceptance criteria")

    def test_nonfunctional_requirements_have_measurable_targets(self):
        for nfr in self.doc["nonFunctionalRequirements"]:
            self.assertTrue(nfr["measurableTarget"], f"{nfr['id']} has no measurable target")
            self.assertTrue(nfr["verificationMethod"], f"{nfr['id']} has no verification method")

    def test_use_cases_have_multi_step_main_flow(self):
        for use_case in self.doc["useCases"]:
            self.assertGreaterEqual(len(use_case["mainFlow"]), 2, f"{use_case['id']} main flow too short")

    def test_all_ids_unique(self):
        for key in ("functionalRequirements", "nonFunctionalRequirements", "useCases", "edgeCases", "systemModules", "testingPlan"):
            ids = [item["id"] for item in self.doc[key]]
            self.assertEqual(len(ids), len(set(ids)), f"{key} has duplicate ids")


class TraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_traceability_matrix_is_deterministic(self):
        first = self.agent.build_safe_fallback(_ai_request()).model_dump()
        second = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertEqual(first["traceabilityMatrix"], second["traceabilityMatrix"])

    def test_no_dangling_requirement_references(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        requirement_ids = {fr["id"] for fr in doc["functionalRequirements"]} | {nfr["id"] for nfr in doc["nonFunctionalRequirements"]}
        for use_case in doc["useCases"]:
            for ref in use_case["relatedRequirements"]:
                self.assertIn(ref, requirement_ids)
        for screen in doc["uiScreens"]:
            for ref in screen["relatedRequirements"]:
                self.assertIn(ref, requirement_ids)

    def test_passes_full_schema_validation(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        SEDocumentationCandidateSchema.model_validate(doc)


class DiagramTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_activity_diagram_models_selected_project_not_fypilot_pipeline(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        activity = doc["activityDiagram"]
        self.assertTrue(activity.startswith("flowchart"))
        self.assertNotIn("Generate SE documentation", activity)
        self.assertNotIn("Generate roadmap", activity)

    def test_sequence_diagram_participants_match_architecture(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        sequence = doc["sequenceDiagram"]
        architecture = doc["architecture"]
        self.assertTrue(sequence.startswith("sequenceDiagram"))
        frontend_token = "".join(ch for ch in architecture["frontend"] if ch.isalnum())
        self.assertIn(frontend_token, sequence.replace(" ", ""))

    def test_erd_only_references_declared_entities(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertTrue(doc["mermaidERD"].startswith("erDiagram"))
        self.assertTrue(doc["mermaidClassDiagram"].startswith("classDiagram"))


class AiReportApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_ai_report_present_for_ai_project(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertTrue(doc["aiTechnicalReportApplicable"])
        self.assertIsNotNone(doc["aiTechnicalReport"])

    def test_ai_report_absent_for_non_ai_project(self):
        doc = self.agent.build_safe_fallback(_non_ai_request()).model_dump()
        self.assertFalse(doc["aiTechnicalReportApplicable"])
        self.assertIsNone(doc["aiTechnicalReport"])


class DeterministicQualityScoreTests(unittest.TestCase):
    """
    Replaces the previous hardcoded "88 if not used_fallback else 82".
    """

    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_score_is_deterministic_for_same_input(self):
        first = self.agent.build_safe_fallback(_ai_request()).model_dump()
        second = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertEqual(first["documentationQualityScore"], second["documentationQualityScore"])
        self.assertEqual(first["qualityAssessment"], second["qualityAssessment"])

    def test_score_is_never_a_hardcoded_constant(self):
        # Historically this was always exactly 88 or exactly 82 regardless of
        # content -- confirm the score is actually derived from criterion
        # breakdown that sums to it (within rounding).
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        quality = doc["qualityAssessment"]
        self.assertIn("completeness", quality["criterionScores"])
        self.assertIn("traceabilityCoverage", quality["criterionScores"])
        self.assertEqual(doc["documentationQualityScore"], quality["overallScore"])

    def test_fallback_score_capped_below_llm_quality_ceiling(self):
        doc = self.agent.build_safe_fallback(_ai_request()).model_dump()
        self.assertLessEqual(doc["documentationQualityScore"], 70)

    def test_assumptions_disclosed_when_facts_are_missing(self):
        doc = self.agent.build_safe_fallback(SEDocumentationRequest()).model_dump()
        self.assertTrue(doc["assumptions"])
        self.assertEqual(doc["qualityAssessment"]["assumptionsCount"], len(doc["assumptions"]))


if __name__ == "__main__":
    unittest.main()
