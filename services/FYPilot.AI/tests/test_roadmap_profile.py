"""
Unit tests for app/agents/roadmap/project_profile.py -- project type/risk
detection, lifecycle-category selection, and duration-sensitive phase-count
bounds.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.roadmap import project_profile  # noqa: E402
from app.agents.roadmap.project_profile import ProjectProfileInput  # noqa: E402


def _profile(**overrides):
    defaults = dict(
        idea_title="Test Idea", problem_statement="", required_technologies="",
        required_skills="", missing_skills="", domain="", final_deliverables="",
        difficulty_level="medium", total_weeks=16, team_size=1, hours_per_week=10,
    )
    defaults.update(overrides)
    return project_profile.build_profile(ProjectProfileInput(**defaults))


class ProjectTypeDetectionTests(unittest.TestCase):
    def test_plain_web_app_defaults_to_web(self):
        profile = _profile(problem_statement="A web platform for managing student clubs.")
        self.assertEqual(profile.primary_type, "web")

    def test_nlp_project_detected(self):
        profile = _profile(
            problem_statement="Classify Arabic symptom text using NLP techniques.",
            domain="AI / NLP / healthcare",
        )
        self.assertIn("nlp", profile.project_types)

    def test_iot_project_detected(self):
        profile = _profile(problem_statement="A Raspberry Pi based sensor network for irrigation.")
        self.assertIn("iot", profile.project_types)

    def test_cybersecurity_project_detected(self):
        profile = _profile(problem_statement="An intrusion detection system with penetration testing.")
        self.assertIn("cybersecurity", profile.project_types)


class RiskDetectionTests(unittest.TestCase):
    def test_medical_safety_flag_detected(self):
        profile = _profile(problem_statement="A triage assistant for patient symptom review.")
        self.assertTrue(profile.is_safety_sensitive)

    def test_security_flag_detected_from_auth_keywords(self):
        profile = _profile(problem_statement="A system with authentication and authorization.")
        self.assertTrue(profile.is_security_sensitive)

    def test_missing_skill_burden_flag(self):
        profile = _profile(missing_skills="Arabic NLP, model evaluation, safety validation")
        self.assertIn("missing_skill_burden", profile.risk_flags)


class LifecycleCoverageTests(unittest.TestCase):
    def test_web_project_not_forced_to_contain_model_training(self):
        profile = _profile(problem_statement="A club management web platform.")
        self.assertNotIn("baseline_model", profile.mandatory_lifecycle)
        self.assertNotIn("model_evaluation", profile.mandatory_lifecycle)

    def test_ai_project_requires_data_and_model_lifecycle(self):
        profile = _profile(
            problem_statement="Classify text using a trained NLP model.",
            domain="AI / NLP",
        )
        for category in ("data_sourcing", "data_preprocessing", "baseline_model", "model_evaluation"):
            self.assertIn(category, profile.mandatory_lifecycle)

    def test_medical_project_requires_safety_validation(self):
        profile = _profile(problem_statement="A medical triage assistant for patient symptoms.")
        self.assertIn("safety_validation", profile.mandatory_lifecycle)
        self.assertIn("data_governance_privacy", profile.mandatory_lifecycle)

    def test_coverage_detects_present_categories_from_phase_text(self):
        profile = _profile(problem_statement="A club management web platform.")
        phase_texts = [
            "Requirements and Scope Definition: gather functional requirements",
            "Database Architecture: design the schema",
            "Core Feature Implementation: implement the booking workflow",
            "Integration: connect the frontend to the backend API",
            "Testing and Validation: run functional tests",
            "Documentation: write the technical report",
            "Final Deployment: submit and present the project",
        ]
        covered, missing = project_profile.lifecycle_coverage(profile, phase_texts)
        self.assertEqual(missing, [])

    def test_coverage_flags_missing_mandatory_category(self):
        profile = _profile(problem_statement="A club management web platform.")
        phase_texts = ["Core Feature Implementation: implement the booking workflow"]
        _covered, missing = project_profile.lifecycle_coverage(profile, phase_texts)
        self.assertIn("requirements_scope", missing)
        self.assertIn("testing_validation", missing)


class PhaseCountBoundsTests(unittest.TestCase):
    def test_short_project_gets_small_phase_range(self):
        # Duration band alone suggests (4, 5), but the bound is only ever
        # widened (never narrowed) to make room for genuine mandatory
        # lifecycle coverage -- never below the duration guidance.
        profile = _profile(total_weeks=6)
        self.assertEqual(profile.phase_count_min, 4)
        self.assertGreaterEqual(profile.phase_count_max, 5)
        self.assertLessEqual(profile.phase_count_max, 6)

    def test_16_week_project_gets_wider_range(self):
        profile = _profile(total_weeks=16)
        self.assertGreaterEqual(profile.phase_count_min, 7)
        self.assertLessEqual(profile.phase_count_max, 16)

    def test_phase_count_never_exceeds_total_weeks(self):
        profile = _profile(total_weeks=4)
        self.assertLessEqual(profile.phase_count_max, 4)

    def test_ai_medical_project_widens_upper_bound_for_lifecycle_needs(self):
        default_profile = _profile(total_weeks=16, problem_statement="A club platform.")
        ai_medical_profile = _profile(
            total_weeks=16,
            problem_statement="A medical triage assistant using a trained NLP model.",
            domain="AI / NLP / healthcare",
        )
        self.assertGreater(ai_medical_profile.phase_count_max, default_profile.phase_count_max)


if __name__ == "__main__":
    unittest.main()
