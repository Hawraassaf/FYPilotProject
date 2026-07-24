"""
Unit tests for app/agents/roadmap_scheduler.py -- the deterministic
dynamic-duration/task-estimation/duplicate-prevention/team-allocation/
overload-resolution/weekly-capacity logic behind the Project Roadmap
improvement and its stabilization batch. Pure-function tests here; the
integration with ReviewPipeline (including the "Rewrite cannot change
locked schedule/workload values" guarantee) is covered in
test_review_pipeline.py's Roadmap section.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import roadmap_scheduler  # noqa: E402
from app.agents.project_roadmap_agent import (  # noqa: E402
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
)


def _week(number, phase_title, tasks=None, deliverables=None, goal="goal"):
    return {
        "weekNumber": number,
        "phaseTitle": phase_title,
        "mainGoal": goal,
        "tasks": tasks or ["Implement task A", "Implement task B"],
        "deliverables": deliverables or ["Deliverable"],
        "teamResponsibilities": ["r1"],
        "skillsToLearn": [],
        "riskWarning": "risk",
        "checkpoint": "checkpoint",
    }


class TotalWeeksNormalizationTests(unittest.TestCase):
    def test_reuses_real_requested_duration(self):
        self.assertEqual(roadmap_scheduler.normalize_total_weeks(16), 16)
        self.assertEqual(roadmap_scheduler.normalize_total_weeks(20), 20)

    def test_clamps_only_pathological_values(self):
        self.assertEqual(roadmap_scheduler.normalize_total_weeks(0), 4)
        self.assertEqual(roadmap_scheduler.normalize_total_weeks(-5), 4)
        self.assertEqual(roadmap_scheduler.normalize_total_weeks(500), 30)
        self.assertEqual(roadmap_scheduler.normalize_total_weeks("not a number"), 10)


class PhaseDurationAllocationTests(unittest.TestCase):
    def test_durations_are_not_all_one_week(self):
        durations = roadmap_scheduler.allocate_phase_durations([1, 1, 1, 3, 2, 1], 16)
        self.assertFalse(all(d == 1 for d in durations))

    def test_different_weights_produce_different_allocations(self):
        low_effort = roadmap_scheduler.allocate_phase_durations([1, 1, 1, 1], 12)
        high_core = roadmap_scheduler.allocate_phase_durations([1, 1, 5, 1], 12)
        self.assertNotEqual(low_effort, high_core)
        self.assertGreater(high_core[2], low_effort[2])

    def test_total_span_always_fits_total_weeks(self):
        for weights in ([1, 1, 1], [1, 2, 3, 4], [5, 1, 1, 1, 1, 1, 1]):
            for total in (4, 6, 10, 16, 24):
                durations = roadmap_scheduler.allocate_phase_durations(weights, total)
                self.assertEqual(sum(durations), total)
                self.assertTrue(all(d >= 1 for d in durations))

    def test_more_phases_than_weeks_never_exceeds_total(self):
        durations = roadmap_scheduler.allocate_phase_durations([1] * 10, 4)
        self.assertEqual(sum(durations), 4)
        self.assertEqual(len(durations), 4)


class TaskHourEstimationTests(unittest.TestCase):
    def test_hours_are_positive_and_within_realistic_bounds(self):
        for title in [
            "Configure the development environment",
            "Design the login page",
            "Implement the core booking workflow",
            "Integrate Groq-to-Gemini-to-Ollama provider fallback",
            "Test the booking workflow end to end",
        ]:
            hours, days = roadmap_scheduler.estimate_task_hours(title)
            self.assertGreater(hours, 0)
            self.assertGreater(days, 0)
            self.assertLessEqual(hours, 60)

    def test_same_text_always_yields_same_hours(self):
        a = roadmap_scheduler.estimate_task_hours("Implement OAuth integration with Google Calendar")
        b = roadmap_scheduler.estimate_task_hours("Implement OAuth integration with Google Calendar")
        self.assertEqual(a, b)

    def test_complex_task_gets_more_hours_than_simple_task(self):
        simple_hours, _ = roadmap_scheduler.estimate_task_hours("Set up the development environment")
        complex_hours, _ = roadmap_scheduler.estimate_task_hours(
            "Implement OAuth integration with Google Calendar FreeBusy API"
        )
        self.assertGreater(complex_hours, simple_hours)

    def test_working_days_always_consistent_with_hours(self):
        # estimatedWorkingDays = ceil(estimatedHours / EFFECTIVE_HOURS_PER_DAY)
        # -- the one documented rule, never contradictory (e.g. 24h/"1 day").
        for hours in (2, 6, 10, 24, 48, 60):
            days = roadmap_scheduler.compute_working_days(hours)
            self.assertEqual(days, math.ceil(hours / roadmap_scheduler.EFFECTIVE_HOURS_PER_DAY))


class TaskPriorityClassificationTests(unittest.TestCase):
    def test_critical_examples(self):
        for title in [
            "Design the database schema for bookings",
            "Implement core backend booking logic",
            "Implement authentication and login",
            "Implement core AI integration for recommendations",
        ]:
            self.assertEqual(roadmap_scheduler.classify_task_priority(title), "critical")

    def test_high_examples(self):
        for title in [
            "Write integration tests for the booking API",
            "Prepare deployment configuration for production",
        ]:
            self.assertEqual(roadmap_scheduler.classify_task_priority(title), "high")

    def test_optional_examples(self):
        for title in [
            "Build an extra dashboard for advanced analytics",
            "Add advanced visual polish to the homepage",
            "Stretch integration with a third-party calendar",
        ]:
            self.assertEqual(roadmap_scheduler.classify_task_priority(title), "optional")

    def test_mandatory_phrase_is_never_optional_even_with_optional_keyword(self):
        # "polish" alone would land in the optional bucket, but an explicit
        # mandatory/required/essential qualifier must override that.
        priority = roadmap_scheduler.classify_task_priority(
            "Required final polish pass on the mandatory core workflow screens"
        )
        self.assertNotEqual(priority, "optional")

    def test_phase_context_contributes_to_classification(self):
        priority = roadmap_scheduler.classify_task_priority(
            "Add extra dashboard widgets", phase_name="Advanced Analytics Enhancements"
        )
        self.assertEqual(priority, "optional")


class DuplicateTaskDetectionTests(unittest.TestCase):
    def test_lexically_different_duplicates_are_detected(self):
        self.assertTrue(roadmap_scheduler.are_duplicate_tasks(
            "Implement authentication", "Develop user authentication system"))
        self.assertTrue(roadmap_scheduler.are_duplicate_tasks(
            "Test API endpoints", "Perform API endpoint testing"))
        self.assertTrue(roadmap_scheduler.are_duplicate_tasks(
            "Create database schema", "Design database tables and schema"))

    def test_genuinely_different_tasks_are_not_merged(self):
        self.assertFalse(roadmap_scheduler.are_duplicate_tasks(
            "Unit test authentication service", "Integration test login workflow"))

    def test_deduplicate_tasks_keeps_more_specific_phrasing(self):
        result = roadmap_scheduler.deduplicate_tasks([
            "Implement authentication",
            "Develop user authentication system",
            "Unit test authentication service",
            "Integration test login workflow",
        ])
        self.assertEqual(len(result), 3)

    def test_padding_template_tasks_are_recognized(self):
        self.assertTrue(roadmap_scheduler.is_padding_task("Review and refine: implement backend"))
        self.assertTrue(roadmap_scheduler.is_padding_task("Test and verify: implement backend"))
        self.assertFalse(roadmap_scheduler.is_padding_task("Implement backend logic"))


class WorkloadReconciliationTests(unittest.TestCase):
    """Section 13 'Workload reconciliation' tests 1-5."""

    def test_solo_assignment_totals_equal_total_planned_hours(self):
        weeks = [_week(i, "Phase", tasks=[f"Implement part {i}"]) for i in range(1, 4)]
        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 1, 40)
        self.assertEqual(summary["workloadByMember"][0]["assignedHours"], summary["totalPlannedHours"])

    def test_two_member_collaboration_splits_without_duplication(self):
        weeks = [_week(1, "Integration", tasks=[
            "Implement OAuth integration with Google Calendar FreeBusy API",
        ])]
        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 2, 40)
        task = phases[0]["tasks"][0]
        self.assertEqual(len(task["memberAllocations"]), 2)
        total_allocated = sum(a["allocatedHours"] for a in task["memberAllocations"])
        self.assertEqual(total_allocated, task["estimatedHours"])
        total_workload = sum(m["assignedHours"] for m in summary["workloadByMember"])
        self.assertEqual(total_workload, summary["totalPlannedHours"])

    def test_three_member_workloads_sum_to_total_planned_hours(self):
        weeks = [
            _week(i, f"Phase {i}", tasks=[f"Implement feature {i}", f"Design feature {i}"])
            for i in range(1, 5)
        ]
        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 4, 3, 40)
        total_workload = sum(m["assignedHours"] for m in summary["workloadByMember"])
        self.assertEqual(total_workload, summary["totalPlannedHours"])

    def test_allocation_percentages_sum_to_100(self):
        weeks = [_week(1, "Integration", tasks=[
            "Implement OAuth integration with Google Calendar FreeBusy API",
        ])]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 3, 40)
        task = phases[0]["tasks"][0]
        self.assertEqual(sum(a["allocationPercentage"] for a in task["memberAllocations"]), 100)

    def test_allocated_hours_match_percentages(self):
        allocations = roadmap_scheduler.allocate_task_hours(20, "complex", [0, 0], 2)
        self.assertEqual(allocations[0]["allocatedHours"], round(20 * 0.7))
        self.assertEqual(allocations[1]["allocatedHours"], 20 - round(20 * 0.7))
        self.assertEqual(sum(a["allocatedHours"] for a in allocations), 20)


class BuildPhasesAndSummaryTests(unittest.TestCase):
    def test_task_ids_are_unique(self):
        weeks = [
            _week(1, "Phase A", tasks=["Implement feature X", "Design schema for X"]),
            _week(2, "Phase A", tasks=["Test feature X"]),
            _week(3, "Phase B", tasks=["Implement feature Y"]),
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 1, 40)
        all_ids = [task["taskId"] for phase in phases for task in phase["tasks"]]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_dependencies_reference_existing_ids(self):
        weeks = [
            _week(1, "Phase A", tasks=["Implement feature X", "Design schema for X"]),
            _week(2, "Phase B", tasks=["Test feature X"]),
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 2, 1, 40)
        valid_ids = {task["taskId"] for phase in phases for task in phase["tasks"]}
        valid_ids |= {phase["phaseId"] for phase in phases}
        for phase in phases:
            for task in phase["tasks"]:
                for dependency in task["dependencies"]:
                    self.assertIn(dependency, valid_ids)

    def test_no_circular_dependencies_in_real_output(self):
        weeks = [_week(i, "Phase A", tasks=[f"Implement part {i}"]) for i in range(1, 4)]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 1, 40)
        all_tasks = [task for phase in phases for task in phase["tasks"]]
        self.assertFalse(roadmap_scheduler.has_dependency_cycle(all_tasks))

    def test_cycle_detection_catches_a_crafted_cycle(self):
        tasks = [
            {"taskId": "A", "dependencies": ["B"]},
            {"taskId": "B", "dependencies": ["A"]},
        ]
        self.assertTrue(roadmap_scheduler.has_dependency_cycle(tasks))

    def test_team_size_one_assigns_everything_to_member_one(self):
        weeks = [
            _week(i, "Phase", tasks=[f"Implement part {i}", f"Design part {i}"])
            for i in range(1, 4)
        ]
        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 1, 40)
        for phase in phases:
            for task in phase["tasks"]:
                self.assertEqual(task["assignedMembers"], ["Member 1"])
        self.assertEqual(len(summary["workloadByMember"]), 1)

    def test_team_size_two_divides_independent_tasks(self):
        weeks = [
            _week(i, f"Phase {i}", tasks=[f"Implement feature {i}", f"Design feature {i}", f"Test feature {i}"])
            for i in range(1, 5)
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 4, 2, 40)
        assigned_members = {
            member for phase in phases for task in phase["tasks"] for member in task["assignedMembers"]
        }
        self.assertIn("Member 1", assigned_members)
        self.assertIn("Member 2", assigned_members)

    def test_team_size_three_allows_collaboration_on_complex_tasks(self):
        weeks = [_week(1, "Integration", tasks=["Implement OAuth integration with Google Calendar FreeBusy API"])]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 3, 40)
        task = phases[0]["tasks"][0]
        self.assertEqual(task["complexity"], "complex")
        self.assertGreaterEqual(len(task["assignedMembers"]), 1)

    def test_assigned_member_indices_never_exceed_team_size(self):
        weeks = [
            _week(i, f"Phase {i}", tasks=[f"Implement part {i}", f"Design part {i}"])
            for i in range(1, 6)
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 5, 2, 40)
        for phase in phases:
            for task in phase["tasks"]:
                for member in task["assignedMembers"]:
                    index = int(member.replace("Member", "").strip())
                    self.assertLessEqual(index, 2)

    def test_workload_totals_match_assigned_task_hours(self):
        weeks = [_week(i, f"Phase {i}", tasks=[f"Implement part {i}"]) for i in range(1, 4)]
        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 2, 40)
        all_tasks = [task for phase in phases for task in phase["tasks"]]
        total_from_tasks = sum(task["estimatedHours"] for task in all_tasks)
        self.assertEqual(total_from_tasks, summary["totalPlannedHours"])

    def test_padding_tasks_are_excluded_from_structured_output(self):
        weeks = [_week(1, "Phase", tasks=[
            "Implement feature X",
            "Review and refine: implement feature X",
            "Test and verify: implement feature X",
        ])]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, 40)
        titles = [task["title"] for task in phases[0]["tasks"]]
        self.assertEqual(titles, ["Implement feature X"])


class OverloadResolutionTests(unittest.TestCase):
    """Section 13 'Overload resolution' tests 6-15."""

    def _overloaded_weeks(self):
        # Deliberately far beyond any reasonable capacity: several complex/
        # critical tasks plus several clearly-optional ones.
        return [
            _week(1, "Core Backend", tasks=[
                "Implement core backend booking logic",
                "Design the database schema for bookings",
            ]),
            _week(2, "AI Integration", tasks=[
                "Implement OAuth integration with Google Calendar FreeBusy API",
                "Implement machine learning recommendation engine",
            ]),
            _week(3, "Polish", tasks=[
                "Build an extra dashboard for advanced analytics",
                "Add advanced visual polish to the homepage",
                "Stretch integration with a bonus feature enhancement",
            ]),
        ]

    def test_optional_tasks_are_deferred_when_capacity_exceeded(self):
        phases, summary, deferred = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4  # tiny capacity: 12h total
        )
        self.assertGreater(len(deferred), 0)
        self.assertTrue(all(task["priority"] in ("optional", "medium") for task in deferred))

    def test_essential_tasks_are_never_incorrectly_deferred(self):
        phases, summary, deferred = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4
        )
        deferred_titles = {task["title"] for task in deferred}
        self.assertNotIn("Implement core backend booking logic", deferred_titles)
        self.assertNotIn("Design the database schema for bookings", deferred_titles)

    def test_deferred_hours_reduce_adjusted_planned_hours(self):
        phases, summary, deferred = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4
        )
        self.assertLess(summary["adjustedPlannedHours"], summary["originalPlannedHours"])
        self.assertEqual(
            summary["originalPlannedHours"] - summary["adjustedPlannedHours"],
            summary["deferredHours"],
        )

    def test_deferred_tasks_remain_visible(self):
        _, _, deferred = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4
        )
        for task in deferred:
            self.assertIn("title", task)
            self.assertIn("estimatedHours", task)
            self.assertIn("reasonDeferred", task)
            self.assertIn("originalPhase", task)
            self.assertIn("priority", task)

    def test_dependencies_rebuilt_after_deferral(self):
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4
        )
        valid_ids = {task["taskId"] for phase in phases for task in phase["tasks"]}
        valid_ids |= {phase["phaseId"] for phase in phases}
        for phase in phases:
            for task in phase["tasks"]:
                for dependency in task["dependencies"]:
                    self.assertIn(dependency, valid_ids)

    def test_no_active_task_depends_on_a_deferred_task(self):
        phases, _, deferred = roadmap_scheduler.build_phases_and_summary(
            self._overloaded_weeks(), 3, 1, 4
        )
        remaining_titles = {task["title"] for phase in phases for task in phase["tasks"]}
        deferred_titles = {task["title"] for task in deferred}
        self.assertEqual(remaining_titles & deferred_titles, set())

    def test_feasible_after_reduction_state_when_capacity_restored(self):
        # Capacity is set to comfortably exceed the critical task's own
        # hours (computed directly, not guessed) plus a small margin, so
        # deferring the optional task is guaranteed to fully resolve the
        # overload -- this exercises the *logic*, not a fragile fixed
        # capacity number that depends on the hash-derived hour estimate.
        critical_title = "Implement core backend booking logic"
        critical_hours, _ = roadmap_scheduler.estimate_task_hours(critical_title)
        total_weeks = 2

        weeks = [
            _week(1, "Core", tasks=[critical_title]),
            _week(2, "Polish", tasks=["Build an extra dashboard for advanced analytics"]),
        ]
        # Total capacity (total_weeks * hours_per_week * teamSize) just
        # above the critical task's own hours, so the plan is only
        # feasible once the optional task is deferred.
        hours_per_week = math.ceil((critical_hours + 3) / total_weeks)
        _, summary, deferred = roadmap_scheduler.build_phases_and_summary(
            weeks, total_weeks, 1, hours_per_week
        )
        self.assertGreater(len(deferred), 0)
        self.assertEqual(summary["scheduleFeasibility"], "feasible_after_scope_reduction")

    def test_over_capacity_state_when_essential_work_still_exceeds_capacity(self):
        weeks = [_week(1, "Core Backend", tasks=[
            "Implement core backend booking logic",
            "Design the database schema for bookings",
            "Implement authentication and login",
        ])]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, 1)
        self.assertEqual(summary["scheduleFeasibility"], "over_capacity")
        self.assertGreater(summary["overloadHours"], 0)

    def test_recommended_additional_weeks_calculated_correctly(self):
        weeks = [_week(1, "Core Backend", tasks=[
            "Implement core backend booking logic",
            "Design the database schema for bookings",
        ])]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, 1)
        expected = math.ceil(summary["overloadHours"] / (1 * 1))
        self.assertEqual(summary["recommendedAdditionalWeeks"], expected)

    def test_feasible_roadmap_is_not_modified_unnecessarily(self):
        weeks = [_week(i, f"Phase {i}", tasks=[f"Implement part {i}"]) for i in range(1, 3)]
        _, summary, deferred = roadmap_scheduler.build_phases_and_summary(weeks, 2, 2, 40)
        self.assertEqual(deferred, [])
        self.assertEqual(summary["scheduleFeasibility"], "feasible")


class WeeklyCapacityTests(unittest.TestCase):
    """Section 13 'Weekly capacity' tests 16-20."""

    def test_solo_member_weekly_capacity_present(self):
        weeks = [_week(i, f"Phase {i}", tasks=[f"Implement part {i}"]) for i in range(1, 4)]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 1, 40)
        self.assertEqual(len(summary["weeklyCapacity"]), 3)
        for entry in summary["weeklyCapacity"]:
            self.assertEqual(entry["capacityHours"], 40)

    def test_multi_member_weekly_capacity_reflects_team_size(self):
        weeks = [_week(i, f"Phase {i}", tasks=[f"Implement part {i}"]) for i in range(1, 4)]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 3, 3, 10)
        for entry in summary["weeklyCapacity"]:
            self.assertEqual(entry["capacityHours"], 30)

    def test_weekly_utilization_calculation_is_correct(self):
        weeks = [_week(1, "Phase", tasks=["Implement part 1"])]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, 40)
        entry = summary["weeklyCapacity"][0]
        expected = round((entry["plannedHours"] / entry["capacityHours"]) * 100, 1)
        self.assertEqual(entry["utilizationPercentage"], expected)

    def test_no_task_scheduled_before_its_dependency(self):
        weeks = [
            _week(1, "Phase A", tasks=["Implement part 1", "Implement part 2"]),
            _week(2, "Phase B", tasks=["Implement part 3"]),
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 2, 1, 40)
        week_by_id = {
            task["taskId"]: task["startWeek"]
            for phase in phases for task in phase["tasks"]
        }
        for phase in phases:
            for task in phase["tasks"]:
                for dependency in task["dependencies"]:
                    self.assertLessEqual(week_by_id[dependency], task["startWeek"])

    def test_weekly_capacity_covers_every_week(self):
        weeks = [_week(i, f"Phase {i}", tasks=[f"Implement part {i}"]) for i in range(1, 6)]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 5, 1, 40)
        self.assertEqual([entry["week"] for entry in summary["weeklyCapacity"]], [1, 2, 3, 4, 5])


class FallbackRoadmapTests(unittest.TestCase):
    def _request(self, **overrides):
        defaults = dict(
            ideaTitle="Test Idea", problemStatement="p", requiredTechnologies="",
            requiredSkills="", missingSkills="", difficultyLevel="medium",
            expectedDurationWeeks=16, domain="", finalDeliverables="",
            teamSize=1, availableHoursPerWeek=10, studentSkills=[], skillRatings={},
        )
        defaults.update(overrides)
        return ProjectRoadmapRequest(**defaults)

    def test_fallback_respects_requested_total_weeks(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(self._request(expectedDurationWeeks=18))
        self.assertEqual(response.totalWeeks, 18)
        self.assertEqual(sum(phase.durationWeeks for phase in response.phases), 18)

    def test_fallback_respects_team_size(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(self._request(teamSize=3, expectedDurationWeeks=16))
        self.assertEqual(len(response.planningSummary.workloadByMember), 3)

    def test_fallback_has_no_duplicate_task_titles(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(self._request(expectedDurationWeeks=16))
        all_titles = [task.title for phase in response.phases for task in phase.tasks]

        for i in range(len(all_titles)):
            for j in range(i + 1, len(all_titles)):
                self.assertFalse(
                    roadmap_scheduler.are_duplicate_tasks(all_titles[i], all_titles[j]),
                    f"'{all_titles[i]}' and '{all_titles[j]}' were flagged as duplicates",
                )

    def test_fallback_phase_durations_are_not_all_one_week(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(self._request(expectedDurationWeeks=16))
        durations = [phase.durationWeeks for phase in response.phases]
        self.assertFalse(all(d == 1 for d in durations))

    def test_fallback_workload_reconciles_exactly(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(self._request(teamSize=3, expectedDurationWeeks=16))
        total_workload = sum(m.assignedHours for m in response.planningSummary.workloadByMember)
        self.assertEqual(total_workload, response.planningSummary.totalPlannedHours)

    def test_fallback_never_labels_overloaded_plan_feasible(self):
        # Extremely tight capacity: 1 member, 1 hour/week, 4 weeks minimum.
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(
            self._request(teamSize=1, availableHoursPerWeek=1, expectedDurationWeeks=4)
        )
        if response.planningSummary.overloadHours > 0:
            self.assertEqual(response.planningSummary.scheduleFeasibility, "over_capacity")


if __name__ == "__main__":
    unittest.main()
