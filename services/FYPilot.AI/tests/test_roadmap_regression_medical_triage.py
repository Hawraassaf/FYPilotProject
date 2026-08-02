"""
Required regression test (roadmap redesign brief, section 16): a 16-week,
solo, "Arabic Medical Triage Assistant" AI/NLP/healthcare project.

Exercised through ProjectRoadmapAgent.build_safe_fallback() -- deterministic,
no LLM call -- so this is a genuine, repeatable regression test rather than
one that depends on live model output. build_safe_fallback() runs through
the exact same profile-driven phase catalog and capacity-aware scheduler
(roadmap_scheduler.build_phases_and_summary) as an AI-generated candidate,
so every invariant checked here holds for the AI path too.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import roadmap_scheduler  # noqa: E402
from app.agents.project_roadmap_agent import ProjectRoadmapAgent, ProjectRoadmapRequest  # noqa: E402


def _medical_triage_request() -> ProjectRoadmapRequest:
    return ProjectRoadmapRequest(
        ideaTitle="Arabic Medical Triage Assistant",
        problemStatement=(
            "A web platform where users enter Arabic symptom descriptions. An NLP "
            "component classifies urgency categories and returns a safe triage "
            "recommendation with clear limitations."
        ),
        requiredTechnologies=(
            "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Python, "
            "PyTorch or scikit-learn, Bootstrap, JavaScript"
        ),
        requiredSkills="ASP.NET Core, Python, NLP",
        missingSkills=(
            "Arabic medical corpus curation, Arabic NLP preprocessing, "
            "model evaluation, medical-data safety validation"
        ),
        difficultyLevel="Advanced",
        expectedDurationWeeks=16,
        domain="AI / NLP / healthcare",
        finalDeliverables="A working triage web app with a documented NLP classifier",
        teamSize=1,
        availableHoursPerWeek=20,
        studentSkills=["C#", "ASP.NET Core", "SQL", "HTML", "CSS", "JavaScript", "basic Python"],
        skillRatings={"C#": 4, "ASP.NET Core": 4, "SQL": 3, "Python": 2},
    )


class ArabicMedicalTriageRegressionTests(unittest.TestCase):
    def setUp(self):
        self.agent = ProjectRoadmapAgent()
        self.request = _medical_triage_request()
        self.response = self.agent.build_safe_fallback(self.request)

    # -- Phase structure ----------------------------------------------

    def test_phase_count_is_in_the_expected_range(self):
        # "Approximately 7-10 meaningful phases, unless the algorithm gives
        # a justified nearby value" -- not just five broad phases.
        count = len(self.response.phases)
        self.assertGreaterEqual(count, 7)
        self.assertLessEqual(count, 13)

    def test_data_model_evaluation_integration_testing_finalization_are_separate_phases(self):
        names = " | ".join(phase.name.lower() for phase in self.response.phases)
        self.assertIn("dataset", names)
        self.assertIn("model", names)
        self.assertIn("evaluation", names)
        self.assertTrue("integration" in names or "core feature" in names)
        self.assertIn("testing", names)
        self.assertIn("deployment", names)

    def test_medical_safety_and_documentation_phases_present(self):
        names = " | ".join(phase.name.lower() for phase in self.response.phases)
        self.assertIn("safety", names)
        self.assertIn("documentation", names)

    def test_no_phase_is_a_handful_of_tasks_stretched_arbitrarily(self):
        # Duration is reconstructed from scheduled task effort, so a phase
        # with few tasks should not carry a disproportionately long span.
        for phase in self.response.phases:
            if phase.durationWeeks >= 3:
                self.assertGreaterEqual(
                    len(phase.tasks), 2,
                    f"phase '{phase.name}' spans {phase.durationWeeks} weeks with only "
                    f"{len(phase.tasks)} task(s)",
                )

    # -- Task quality ----------------------------------------------------

    def test_no_padding_tasks(self):
        for phase in self.response.phases:
            for task in phase.tasks:
                self.assertFalse(roadmap_scheduler.is_padding_task(task.title), task.title)

    def test_no_vague_tasks(self):
        for phase in self.response.phases:
            for task in phase.tasks:
                self.assertFalse(
                    roadmap_scheduler.is_vague_task(task.title),
                    f"vague task: '{task.title}'",
                )

    def test_no_bare_learn_x_task(self):
        for phase in self.response.phases:
            for task in phase.tasks:
                lowered = task.title.lower()
                self.assertFalse(
                    lowered.startswith("learn ") or lowered.startswith("study "),
                    task.title,
                )

    def test_missing_skills_surface_in_skills_to_learn(self):
        all_skills = {skill.lower() for week in self.response.weeks for skill in week.skillsToLearn}
        combined = " ".join(all_skills)
        self.assertTrue(
            any(term in combined for term in ("arabic", "corpus", "evaluation", "safety")),
        )

    # -- Capacity and scheduling -----------------------------------------

    def test_total_capacity_is_16_times_20(self):
        self.assertEqual(self.response.planningSummary.capacityHours, 16 * 20)
        self.assertEqual(self.response.planningSummary.totalCapacityHours, 16 * 20)

    def test_no_week_exceeds_capacity_when_feasible(self):
        summary = self.response.planningSummary
        if summary.scheduleFeasibility != "over_capacity":
            for entry in summary.weeklyCapacity:
                self.assertLessEqual(entry.plannedHours, entry.capacityHours)

    def test_large_tasks_span_multiple_weeks(self):
        # Only enforced while the plan is NOT over_capacity: an honestly
        # over_capacity plan may clamp a tail task's overflow onto its last
        # valid week rather than fabricating a feasible-looking multi-week
        # span (see capacity_scheduler.schedule_tasks's overflow clamp).
        is_over_capacity = self.response.planningSummary.scheduleFeasibility == "over_capacity"
        found_multiweek_task = False

        for phase in self.response.phases:
            for task in phase.tasks:
                if task.estimatedHours > self.request.availableHoursPerWeek:
                    if not is_over_capacity:
                        self.assertGreater(task.endWeek, task.startWeek, task.title)
                    if task.endWeek > task.startWeek:
                        found_multiweek_task = True

        if not is_over_capacity:
            self.assertTrue(found_multiweek_task, "expected at least one task heavier than one week's capacity")

    def test_totals_reconcile_across_tasks_members_and_weeks(self):
        summary = self.response.planningSummary
        all_tasks = [task for phase in self.response.phases for task in phase.tasks]

        self.assertEqual(sum(task.estimatedHours for task in all_tasks), summary.totalPlannedHours)
        self.assertEqual(
            sum(member.assignedHours for member in summary.workloadByMember),
            summary.totalPlannedHours,
        )

    def test_dependencies_form_a_dag(self):
        all_tasks = [
            task.model_dump() for phase in self.response.phases for task in phase.tasks
        ]
        self.assertFalse(roadmap_scheduler.has_dependency_cycle(all_tasks))

    def test_parallel_setup_work_is_allowed(self):
        # At least one pair of tasks in the same phase share a start week
        # (independent, same-tier work scheduled in parallel) -- a fully
        # forced sequential chain would give every task its own week.
        found_parallel_pair = False
        for phase in self.response.phases:
            start_weeks = [task.startWeek for task in phase.tasks]
            if len(start_weeks) != len(set(start_weeks)):
                found_parallel_pair = True
                break
        self.assertTrue(found_parallel_pair, "expected at least one phase with parallel same-week tasks")

    # -- Safety / honesty --------------------------------------------------

    def test_no_definitive_diagnosis_claim(self):
        all_text = " ".join(
            [self.response.roadmapTitle, self.response.finalAdvice, self.response.teamStrategy]
            + [phase.name + " " + phase.objective for phase in self.response.phases]
            + [task.title for phase in self.response.phases for task in phase.tasks]
        ).lower()
        self.assertNotIn("diagnose the patient", all_text)
        self.assertNotIn("provide a diagnosis", all_text)

    # -- Determinism -------------------------------------------------------

    def test_regeneration_is_deterministic(self):
        second_response = self.agent.build_safe_fallback(_medical_triage_request())

        first_hours = [
            (task.taskId, task.title, task.estimatedHours, task.startWeek, task.endWeek)
            for phase in self.response.phases for task in phase.tasks
        ]
        second_hours = [
            (task.taskId, task.title, task.estimatedHours, task.startWeek, task.endWeek)
            for phase in second_response.phases for task in phase.tasks
        ]
        self.assertEqual(first_hours, second_hours)
        self.assertEqual(
            self.response.planningSummary.totalPlannedHours,
            second_response.planningSummary.totalPlannedHours,
        )


if __name__ == "__main__":
    unittest.main()
