"""
Regression tests for the "Phase 10 held 103h in a single week" live defect
and the two related fixes shipped alongside it:

1. Phase-LOCAL capacity validation (capacity_scheduler.diagnose_phase_
   capacity) -- a phase's own planned effort is now checked against the
   capacity its OWN scheduled span actually provides, independent of the
   whole-project scheduleFeasibility/utilizationPercentage figures, which
   can read as only marginally over capacity in aggregate while one
   individual phase is locally impossible.
2. Duplicate-deliverable dedup (roadmap_scheduler._normalize_deliverable)
   -- a multi-week phase's deliverables list no longer shows the same
   artifact twice as "Progress update: X" (from an intermediate week) and
   plain "X" (from the phase's own final week).

Also covers the Writer-prompt wording fixes for dependency-aware early
task placement and Ollama's dev-tool-vs-runtime-component distinction --
those are prompt-guidance changes (no deterministic LLM output to assert
on), so these tests only guard the prompt TEXT itself against silent
regression, not actual LLM behavior. See the accompanying report for that
limitation.

Every test here exercises pure, deterministic functions (capacity_
scheduler, roadmap_scheduler, task_taxonomy, project_profile) -- none of
this module's imports touch app.services.llm_provider or any network
client, so nothing in this file can make a live provider call.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import roadmap_scheduler  # noqa: E402
from app.agents.roadmap import capacity_scheduler, project_profile, task_taxonomy  # noqa: E402
from app.agents.roadmap.project_profile import ProjectProfileInput  # noqa: E402
from app.agents.project_roadmap_agent import ProjectRoadmapAgent, ProjectRoadmapRequest  # noqa: E402

# The exact 12 task-effort figures from the confirmed live Phase 10 defect
# (Arabic Medical Symptom Triage Assistant roadmap): 12 tasks, 103h total,
# reported as "Week 16, Duration: 1 week" for a 1-person/20h-per-week team.
_LIVE_PHASE_10_TASK_HOURS = [8, 10, 7, 7, 10, 4, 8, 15, 7, 16, 6, 5]


def _week(number, phase_title, tasks=None, deliverables=None, skills=None, goal="goal"):
    return {
        "weekNumber": number,
        "phaseTitle": phase_title,
        "mainGoal": goal,
        "tasks": tasks or ["Implement task A", "Implement task B"],
        "deliverables": deliverables or ["Deliverable"],
        "teamResponsibilities": ["r1"],
        "skillsToLearn": skills or [],
        "riskWarning": "risk",
        "checkpoint": "checkpoint",
    }


def _medical_triage_request(**overrides) -> ProjectRoadmapRequest:
    defaults = dict(
        ideaTitle="Arabic Medical Symptom Triage Assistant",
        problemStatement=(
            "A Razor Pages web platform where users enter Arabic symptom "
            "descriptions and receive an urgency category and specialist "
            "recommendation from a locally trained NLP model served via FastAPI."
        ),
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, PyTorch, Ollama",
        requiredSkills="ASP.NET Core, Python, NLP",
        missingSkills="Arabic NLP preprocessing, model evaluation, medical-data safety validation",
        difficultyLevel="advanced",
        expectedDurationWeeks=16,
        domain="Healthcare",
        finalDeliverables="A working triage assistant with trained classifiers",
        teamSize=1,
        availableHoursPerWeek=20,
        studentSkills=["ASP.NET Core", "Python"],
        skillRatings={"ASP.NET Core": 3, "Python": 3},
    )
    defaults.update(overrides)
    return ProjectRoadmapRequest(**defaults)


class PhaseCapacityDiagnosticTests(unittest.TestCase):
    """capacity_scheduler.diagnose_phase_capacity -- direct, exact-number
    coverage using the confirmed live Phase 10 fixture."""

    def test_1_person_20h_week_1_week_phase_with_103h_is_a_capacity_violation(self):
        phases_out = [{
            "phaseId": "P10",
            "name": "Final Integration and Testing",
            "durationWeeks": 1,
            "tasks": [{"estimatedHours": h} for h in _LIVE_PHASE_10_TASK_HOURS],
        }]

        issues = capacity_scheduler.diagnose_phase_capacity(
            phases_out, team_size=1, hours_per_week_per_member=20,
        )

        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["phaseId"], "P10")
        self.assertEqual(issue["plannedHours"], 103)
        self.assertEqual(issue["availableCapacityHours"], 20)
        self.assertEqual(issue["overloadHours"], 83)
        self.assertAlmostEqual(issue["utilizationPercentage"], 515.0, delta=0.1)

    def test_same_phase_requires_at_least_six_weeks_unchanged(self):
        phases_out = [{
            "phaseId": "P10", "name": "Final Integration and Testing", "durationWeeks": 1,
            "tasks": [{"estimatedHours": h} for h in _LIVE_PHASE_10_TASK_HOURS],
        }]
        issues = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        # ceil(103 / 20) = 6
        self.assertEqual(issues[0]["requiredMinWeeks"], 6)

    def test_40h_phase_across_2_weeks_passes_at_20h_per_week(self):
        phases_out = [{
            "phaseId": "P1", "name": "Setup", "durationWeeks": 2,
            "tasks": [{"estimatedHours": 20}, {"estimatedHours": 20}],
        }]
        issues = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        self.assertEqual(issues, [])

    def test_project_total_may_fit_while_one_phase_is_still_locally_overloaded(self):
        # Whole-project per-week ledger reads comfortably feasible...
        planned_hours_by_week = {week: 15 for week in range(1, 17)}
        feasibility = capacity_scheduler.compute_feasibility(
            planned_hours_by_week, nominal_week_capacity=20,
            hours_per_week_per_member=20, team_size=1, deferred_count=0,
        )
        self.assertEqual(feasibility["scheduleFeasibility"], "feasible")

        # ...yet one individual phase, taken on its own, is still impossible.
        phases_out = [{
            "phaseId": "P10", "name": "Final Integration and Testing", "durationWeeks": 1,
            "tasks": [{"estimatedHours": h} for h in _LIVE_PHASE_10_TASK_HOURS],
        }]
        issues = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        self.assertEqual(len(issues), 1)

    def test_project_total_over_capacity_and_overloaded_phase_both_report_correctly(self):
        planned_hours_by_week = {week: 20 for week in range(1, 16)}
        planned_hours_by_week[16] = 103
        feasibility = capacity_scheduler.compute_feasibility(
            planned_hours_by_week, nominal_week_capacity=20,
            hours_per_week_per_member=20, team_size=1, deferred_count=0,
        )
        self.assertEqual(feasibility["scheduleFeasibility"], "over_capacity")
        self.assertEqual(feasibility["overloadHours"], 83)

        phases_out = [{
            "phaseId": "P10", "name": "Final Integration and Testing", "durationWeeks": 1,
            "tasks": [{"estimatedHours": h} for h in _LIVE_PHASE_10_TASK_HOURS],
        }]
        issues = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        self.assertEqual(issues[0]["overloadHours"], 83)

    def test_diagnostic_never_mutates_or_reduces_task_hours(self):
        tasks = [{"estimatedHours": h} for h in _LIVE_PHASE_10_TASK_HOURS]
        phases_out = [{"phaseId": "P10", "name": "Final Phase", "durationWeeks": 1, "tasks": tasks}]

        before = [t["estimatedHours"] for t in tasks]
        issues_first = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        issues_second = capacity_scheduler.diagnose_phase_capacity(phases_out, 1, 20)
        after = [t["estimatedHours"] for t in tasks]

        self.assertEqual(before, after)
        self.assertEqual(sum(after), 103)
        self.assertEqual(issues_first, issues_second)


class DependencyStageOrderingTests(unittest.TestCase):
    """Guards the stage-tier invariants the earlier-placement prompt
    guidance (and the existing within-phase scheduler) depend on: UI/auth
    work is not model-dependent; model integration/testing genuinely is."""

    def test_ui_and_auth_stages_precede_model_training(self):
        self.assertLess(
            task_taxonomy.task_stage("simple_ui"), task_taxonomy.task_stage("model_training_tuning"),
        )
        self.assertLessEqual(
            task_taxonomy.task_stage("security"), task_taxonomy.task_stage("model_training_tuning"),
        )

    def test_model_training_precedes_evaluation_precedes_testing(self):
        self.assertLess(
            task_taxonomy.task_stage("model_training_tuning"), task_taxonomy.task_stage("model_evaluation"),
        )
        self.assertLess(
            task_taxonomy.task_stage("model_evaluation"), task_taxonomy.task_stage("functional_testing"),
        )

    def test_external_integration_stage_is_after_foundation_stages(self):
        self.assertGreater(
            task_taxonomy.task_stage("external_api_integration"), task_taxonomy.task_stage("setup_config"),
        )


class EndToEndOverloadedTerminalPhaseTests(unittest.TestCase):
    """Reproduces the qualitative live defect end-to-end through
    build_phases_and_summary (not just the direct diagnostic call above):
    an early-phase pileup pins the last phase's floor to the final week,
    and its own tasks' hours exceed what that single week can hold."""

    def test_overloaded_terminal_phase_produces_a_named_warning_and_diagnostic(self):
        weeks = [
            _week(1, "Requirements", tasks=["Define requirements"]),
            _week(2, "Architecture and Database Design", tasks=["Design database schema"]),
            _week(3, "Core Application UI", tasks=["Build the main dashboard page"]),
            _week(4, "Final Integration and Testing", tasks=[
                "Implement the core booking business logic workflow",
                "Train and tune the recommendation model",
                "Integrate the external payment gateway API",
            ]),
        ]

        phases, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 4, 1, 20)

        final_phase = phases[-1]
        self.assertEqual(final_phase["durationWeeks"], 1)
        planned = sum(t["estimatedHours"] for t in final_phase["tasks"])
        self.assertGreater(planned, 20)

        self.assertTrue(summary["phaseCapacityIssues"], summary["phaseCapacityIssues"])
        issue = summary["phaseCapacityIssues"][0]
        self.assertEqual(issue["phaseId"], final_phase["phaseId"])
        self.assertEqual(issue["plannedHours"], planned)
        self.assertGreaterEqual(issue["requiredMinWeeks"], 2)

        self.assertTrue(
            any(
                final_phase["name"] in warning and "cannot fit" in warning
                for warning in summary["warnings"]
            ),
            summary["warnings"],
        )
        # The target duration is never silently changed by this diagnostic.
        self.assertEqual(summary["totalWeeks"], 4)

    def test_non_overloaded_project_reports_no_phase_capacity_issues(self):
        weeks = [
            _week(1, "Requirements", tasks=["Define requirements"]),
            _week(2, "Architecture and Database Design", tasks=["Design database schema"]),
        ]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 2, 1, 40)
        self.assertEqual(summary["phaseCapacityIssues"], [])


class DuplicateDeliverableDedupTests(unittest.TestCase):
    """roadmap_scheduler._normalize_deliverable / the deduped_deliverables
    computation in build_phases_and_summary -- confirmed live defect:
    'Progress update: Database ERD and schema script; Database ERD and
    schema script; Architecture diagram; API contract document'."""

    def test_normalize_deliverable_strips_prefix_and_is_a_noop_otherwise(self):
        prefix = roadmap_scheduler.PROGRESS_UPDATE_PREFIX
        self.assertEqual(roadmap_scheduler._normalize_deliverable(f"{prefix}X"), "X")
        self.assertEqual(roadmap_scheduler._normalize_deliverable("X"), "X")
        self.assertEqual(roadmap_scheduler._normalize_deliverable(f"  {prefix}X  "), "X")

    def test_progress_update_prefixed_deliverable_collapses_with_its_plain_form(self):
        prefix = roadmap_scheduler.PROGRESS_UPDATE_PREFIX
        weeks = [
            _week(
                1, "Architecture and Database Design",
                deliverables=[f"{prefix}Database ERD and schema script"],
            ),
            _week(
                2, "Architecture and Database Design",
                deliverables=[
                    "Database ERD and schema script", "Architecture diagram", "API contract document",
                ],
            ),
        ]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 2, 1, 40)
        deliverables = phases[0]["deliverables"]

        self.assertEqual(deliverables.count("Database ERD and schema script"), 1)
        self.assertNotIn(f"{prefix}Database ERD and schema script", deliverables)
        # Genuinely distinct deliverables are preserved, not collapsed away.
        self.assertIn("Architecture diagram", deliverables)
        self.assertIn("API contract document", deliverables)

    def test_single_week_phase_deliverables_remain_unchanged(self):
        weeks = [_week(
            1, "Requirements",
            deliverables=["Problem statement document", "Competitor analysis"],
        )]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, 40)
        self.assertEqual(phases[0]["deliverables"], ["Problem statement document", "Competitor analysis"])

    def test_week_deliverables_uses_the_shared_progress_update_prefix_constant(self):
        agent = ProjectRoadmapAgent()
        phase = {"name": "Phase", "deliverables": ["Some deliverable"]}
        result = agent._week_deliverables(phase, is_last_week_of_phase=False, is_buffer_week=False)
        self.assertEqual(result, [f"{roadmap_scheduler.PROGRESS_UPDATE_PREFIX}Some deliverable"])


class WriterPromptWordingTests(unittest.TestCase):
    """Prompt-guidance regression guards -- these confirm the WORDING the
    Writer LLM sees hasn't silently regressed, not actual LLM output (no
    live provider call is made anywhere in this file)."""

    def _profile_and_request(self):
        request = _medical_triage_request()
        profile = project_profile.build_profile(ProjectProfileInput(
            idea_title=request.ideaTitle,
            problem_statement=request.problemStatement,
            required_technologies=request.requiredTechnologies,
            required_skills=request.requiredSkills,
            missing_skills=request.missingSkills,
            domain=request.domain,
            final_deliverables=request.finalDeliverables,
            difficulty_level=request.difficultyLevel,
            total_weeks=16,
            team_size=request.teamSize,
            hours_per_week=request.availableHoursPerWeek,
            student_skills=request.studentSkills,
            skill_ratings=request.skillRatings,
        ))
        return request, profile

    def test_prompt_distinguishes_ollama_dev_tool_from_runtime_architecture(self):
        request, profile = self._profile_and_request()
        agent = ProjectRoadmapAgent()
        prompt = agent._build_phase_prompt(request, 16, profile)

        self.assertIn("Ollama", prompt)
        lowered = prompt.lower()
        self.assertIn("development", lowered)
        self.assertIn("runtime", lowered)
        self.assertIn("do not describe ollama as part of the deployed/runtime architecture", lowered)

    def test_prompt_instructs_dependency_aware_early_placement_of_app_foundation_work(self):
        request, profile = self._profile_and_request()
        agent = ProjectRoadmapAgent()
        prompt = agent._build_phase_prompt(request, 16, profile)

        lowered = prompt.lower()
        self.assertIn("true technical prerequisites", lowered)
        self.assertIn("do not defer it to the final phase", lowered)
        self.assertIn("do not silently pile everything left into the last phase", lowered)


if __name__ == "__main__":
    unittest.main()
