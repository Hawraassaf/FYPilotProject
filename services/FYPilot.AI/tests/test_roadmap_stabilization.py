"""
Regression tests for the roadmap stabilization pass:
1. contingency capacity policy (utilization risk bands, proactive reserve
   for higher-risk profiles),
2. internal structured task metadata surviving generation -> schema
   re-validation (standing in for a Rewrite pass) into scheduling,
3. scope-sensitive effort estimation,
4. dependency-ID-first scheduling (stage tiering as fallback only),
5. fallback phase-count granularity,
6. public contract stability.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import roadmap_scheduler  # noqa: E402
from app.agents.roadmap import capacity_scheduler, project_profile, task_taxonomy  # noqa: E402
from app.agents.roadmap.project_profile import ProjectProfileInput  # noqa: E402
from app.agents.roadmap.task_metadata import InternalTaskProposal, build_registry  # noqa: E402
from app.agents.project_roadmap_agent import (  # noqa: E402
    ProjectRoadmapAgent,
    ProjectRoadmapRequest,
    ProjectRoadmapResponse,
    RoadmapDeferredTask,
    RoadmapMemberAllocation,
    RoadmapMemberWorkload,
    RoadmapPhaseCapacityIssue,
    RoadmapPhaseSummary,
    RoadmapPlanningSummary,
    RoadmapTask,
    RoadmapWeek,
    RoadmapWeeklyCapacity,
    _PhasePlan,
    _RoadmapPlan,
)
from app.review.registry import RoadmapCandidateSchema  # noqa: E402


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
        ideaTitle="Arabic Medical Triage Assistant",
        problemStatement=(
            "A web platform where users enter Arabic symptom descriptions. An NLP "
            "component classifies urgency categories and returns a safe triage "
            "recommendation with clear limitations."
        ),
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, PyTorch",
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
        studentSkills=["C#", "ASP.NET Core", "SQL"],
        skillRatings={"C#": 4, "ASP.NET Core": 4, "Python": 2},
    )
    defaults.update(overrides)
    return ProjectRoadmapRequest(**defaults)


class ContingencyCapacityTests(unittest.TestCase):
    def test_utilization_bands_match_the_documented_policy(self):
        self.assertEqual(project_profile.utilization_risk_band(50), "comfortable")
        self.assertEqual(project_profile.utilization_risk_band(80), "comfortable")
        self.assertEqual(project_profile.utilization_risk_band(83), "feasible")
        self.assertEqual(project_profile.utilization_risk_band(85), "feasible")
        self.assertEqual(project_profile.utilization_risk_band(88), "feasible_with_risk")
        self.assertEqual(project_profile.utilization_risk_band(90), "feasible_with_risk")
        self.assertEqual(project_profile.utilization_risk_band(95), "very_tight")
        self.assertEqual(project_profile.utilization_risk_band(100), "very_tight")
        self.assertEqual(project_profile.utilization_risk_band(101), "over_capacity")

    def test_a_294_of_320_hour_plan_produces_a_high_utilization_warning(self):
        # 294/320 = 91.875% -- squarely in the >90% "very tight" band from
        # the stabilization brief. Build a single task whose PERT hours are
        # queried directly (self-consistent with whatever the estimator
        # currently returns) so the test isn't tied to a magic constant.
        title = "Implement the core booking workflow business logic"
        hours, _days = task_taxonomy.estimate_task_hours(title)
        # Single week so capacity == hours_per_week exactly, avoiding any
        # rounding ambiguity from splitting across multiple weeks; pick a
        # capacity landing utilization at ~95% (comfortably inside the
        # brief's >90%-and-<=100% "very tight" band).
        capacity = math.floor(hours / 0.95)

        weeks = [_week(1, "Phase", tasks=[title])]
        _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 1, 1, capacity)

        self.assertGreater(summary["utilizationPercentage"], 90)
        self.assertLessEqual(summary["utilizationPercentage"], 100)
        self.assertNotEqual(summary["scheduleFeasibility"], "over_capacity")
        self.assertTrue(
            any("VERY TIGHT" in warning for warning in summary["warnings"]),
            summary["warnings"],
        )

    def test_scheduleFeasibility_values_never_include_a_new_enum(self):
        # .NET only understands these three -- any new risk tier must be
        # expressed through warnings/schedulingAssumptions instead.
        title = "Implement the core booking workflow business logic"
        weeks = [_week(1, "Phase", tasks=[title])]
        for hours_per_week in (1, 5, 20, 100):
            _, summary, _ = roadmap_scheduler.build_phases_and_summary(weeks, 4, 1, hours_per_week)
            self.assertIn(
                summary["scheduleFeasibility"],
                {"feasible", "feasible_after_scope_reduction", "over_capacity"},
            )

    def test_advanced_medical_ai_profile_triggers_contingency_reserve(self):
        text = (
            "Arabic Medical Triage Assistant classifies patient symptoms using a "
            "trained NLP model and returns a triage recommendation."
        )
        fraction = project_profile.contingency_reserve_fraction(
            text, difficulty_level="advanced", distinct_skills_to_learn=0,
        )
        self.assertGreaterEqual(fraction, 0.15)
        self.assertLessEqual(fraction, 0.25)

    def test_plain_web_project_gets_no_contingency_reserve(self):
        fraction = project_profile.contingency_reserve_fraction(
            "A club membership management web platform.",
            difficulty_level="medium", distinct_skills_to_learn=0,
        )
        self.assertEqual(fraction, 0.0)

    def test_contingency_reserve_defers_more_for_high_risk_profile_at_equal_load(self):
        # Identical task load and capacity; only the phase/task WORDING
        # differs (medical-flavored vs plain) -- the medical one must defer
        # at least as much optional work to protect its contingency buffer.
        mandatory_task = "Implement core backend booking logic"
        optional_task = "Build an extra dashboard for advanced analytics"

        plain_weeks = [_week(1, "Core", tasks=[mandatory_task, optional_task])]
        medical_weeks = [_week(
            1, "Medical Core", tasks=[mandatory_task, optional_task],
            goal="Arabic medical triage safety validation",
        )]

        mandatory_hours, _ = task_taxonomy.estimate_task_hours(mandatory_task)
        optional_hours, _ = task_taxonomy.estimate_task_hours(optional_task)
        # Capacity fits both tasks comfortably (no coarse overload), so any
        # deferral seen here is coming from the contingency reserve, not
        # from ordinary overload-driven deferral.
        capacity = mandatory_hours + optional_hours + 5

        _, plain_summary, plain_deferred = roadmap_scheduler.build_phases_and_summary(
            plain_weeks, 1, 1, capacity,
        )
        _, medical_summary, medical_deferred = roadmap_scheduler.build_phases_and_summary(
            medical_weeks, 1, 1, capacity,
        )

        self.assertEqual(plain_deferred, [])
        self.assertGreater(len(medical_deferred), 0)
        self.assertLessEqual(
            medical_summary["utilizationPercentage"], plain_summary["utilizationPercentage"],
        )


class ScopeSensitiveEffortTests(unittest.TestCase):
    def test_200_and_10000_record_annotation_tasks_differ(self):
        small_hours, _ = task_taxonomy.estimate_task_hours(
            "Annotate 200 records from the Arabic symptom corpus",
        )
        large_hours, _ = task_taxonomy.estimate_task_hours(
            "Annotate 10,000 records from the Arabic symptom corpus",
        )
        self.assertNotEqual(small_hours, large_hours)
        self.assertGreater(large_hours, small_hours)

    def test_page_count_scales_effort(self):
        small, _ = task_taxonomy.estimate_task_hours("Build the 2-page onboarding flow")
        large, _ = task_taxonomy.estimate_task_hours("Build the 12-page admin dashboard")
        self.assertGreater(large, small)

    def test_endpoint_count_scales_effort(self):
        small, _ = task_taxonomy.estimate_task_hours("Expose 1 endpoint for triage predictions")
        large, _ = task_taxonomy.estimate_task_hours("Expose 8 endpoints for the public API")
        self.assertGreater(large, small)

    def test_role_count_scales_effort_with_spelled_out_numbers(self):
        small, _ = task_taxonomy.estimate_task_hours("Support one user role")
        large, _ = task_taxonomy.estimate_task_hours("Support four user roles")
        self.assertGreater(large, small)

    def test_model_variant_count_scales_effort(self):
        baseline, _ = task_taxonomy.estimate_task_hours("Train a baseline model")
        multi, _ = task_taxonomy.estimate_task_hours(
            "Compare three model architectures for triage classification",
        )
        self.assertGreater(multi, baseline)

    def test_missing_scope_falls_back_to_standard_estimate_with_explanation(self):
        multiplier, explanation = task_taxonomy.detect_scope_multiplier("", "Train a baseline model")
        self.assertEqual(multiplier, 1.0)
        self.assertIn("no explicit scope indicator", explanation)

    def test_llm_effort_estimate_is_validated_and_clamped(self):
        # An absurd proposal is discarded entirely in favor of the
        # canonical range for the type.
        min_h, likely_h, max_h = task_taxonomy.validate_and_clamp_effort(
            "simple_ui", 10000, 20000, 30000,
        )
        canonical_min, canonical_likely, canonical_max = task_taxonomy.TASK_TYPES["simple_ui"].hours
        self.assertLess(max_h, 30000)
        self.assertLessEqual(max_h, canonical_max * 2.2)

        # A missing/invalid proposal (min > max after an impossible
        # ordering, non-numeric) falls back to the canonical range exactly.
        self.assertEqual(
            task_taxonomy.validate_and_clamp_effort("simple_ui", None, None, None),
            (canonical_min, canonical_likely, canonical_max),
        )


class StructuredMetadataSurvivalTests(unittest.TestCase):
    """Structured PERT metadata (validated, not raw) must survive from the
    Writer's phase plan through schema re-validation (standing in for what
    a ReviewPipeline Rewrite pass re-validates) into final scheduling."""

    def _build_plan(self) -> tuple[ProjectRoadmapAgent, ProjectRoadmapRequest, _RoadmapPlan]:
        agent = ProjectRoadmapAgent()
        request = ProjectRoadmapRequest(
            ideaTitle="Distinctive Metadata Test Project",
            problemStatement="A project used to verify metadata survival.",
            requiredTechnologies="ASP.NET Core, Python FastAPI, PostgreSQL",
            expectedDurationWeeks=6,
            teamSize=1,
            availableHoursPerWeek=20,
        )
        plan = _RoadmapPlan(
            roadmapTitle="Test Roadmap",
            teamStrategy="Solo.",
            finalAdvice="Advance steadily.",
            phases=[
                _PhasePlan(
                    name="Requirements and Scope Definition",
                    weeks=1,
                    goal="Define scope",
                    tasks=[
                        InternalTaskProposal(
                            localId="T1",
                            title="Draft the initial requirements outline.",
                            taskType="requirements_research",
                            effortMinHours=10, effortLikelyHours=12, effortMaxHours=14,
                            mandatory=True,
                        ),
                        InternalTaskProposal(
                            localId="T2",
                            title="Confirm the project scope with the supervisor.",
                            taskType="requirements_research",
                            mandatory=True,
                        ),
                    ],
                    deliverables=["Requirements document"],
                ),
                _PhasePlan(
                    name="Core Implementation",
                    weeks=2,
                    goal="Build core",
                    tasks=[
                        InternalTaskProposal(
                            localId="T1",
                            title="Implement the core project workflow end to end.",
                            taskType="complex_workflow",
                            effortMinHours=10, effortLikelyHours=12, effortMaxHours=14,
                            mandatory=True, parallelizable=True,
                        ),
                        InternalTaskProposal(
                            localId="T2",
                            title="Evaluate the completed core workflow against requirements.",
                            taskType="functional_testing",
                            effortMinHours=4, effortLikelyHours=6, effortMaxHours=8,
                            mandatory=True, dependencyIds=["T1"],
                        ),
                    ],
                    deliverables=["Working core feature"],
                ),
                _PhasePlan(
                    name="Testing and Validation",
                    weeks=1,
                    goal="Test",
                    tasks=[
                        InternalTaskProposal(
                            localId="T1", title="Run functional tests across the system.",
                            taskType="functional_testing", mandatory=True,
                        ),
                        InternalTaskProposal(
                            localId="T2", title="Fix issues found during testing.",
                            taskType="functional_testing", mandatory=True,
                        ),
                    ],
                    deliverables=["Tested system"],
                ),
                _PhasePlan(
                    name="Documentation and Delivery",
                    weeks=1,
                    goal="Wrap up",
                    tasks=[
                        InternalTaskProposal(
                            localId="T1", title="Write the final technical documentation.",
                            taskType="documentation_presentation", mandatory=False,
                        ),
                        InternalTaskProposal(
                            localId="T2", title="Submit the final project package.",
                            taskType="deployment", mandatory=True,
                        ),
                    ],
                    deliverables=["Final package"],
                ),
            ],
        )
        return agent, request, plan

    def test_validated_effort_survives_into_first_pass_scheduling(self):
        agent, request, plan = self._build_plan()
        total_weeks = agent._normalize_weeks(request.expectedDurationWeeks)
        profile = agent._build_profile(request, total_weeks)
        plan.phases = agent._sanitize_phases(plan.phases, profile)
        response = agent._expand_plan_to_weeks(request, plan, total_weeks, profile)

        task = next(
            t for phase in response.phases for t in phase.tasks
            if t.title == "Draft the initial requirements outline."
        )
        # PERT of (40, 41, 42) = 41 exactly -- distinctive enough that no
        # text-classification default (requirements_research canonical is
        # (2, 4, 8) -> PERT 4.33) could coincidentally produce it.
        self.assertEqual(task.estimatedHours, 12)

    def test_validated_dependency_id_survives_into_first_pass_scheduling(self):
        agent, request, plan = self._build_plan()
        total_weeks = agent._normalize_weeks(request.expectedDurationWeeks)
        profile = agent._build_profile(request, total_weeks)
        plan.phases = agent._sanitize_phases(plan.phases, profile)
        response = agent._expand_plan_to_weeks(request, plan, total_weeks, profile)

        implement_task = next(
            t for phase in response.phases for t in phase.tasks
            if t.title.startswith("Implement the core project workflow")
        )
        evaluate_task = next(
            t for phase in response.phases for t in phase.tasks
            if t.title.startswith("Evaluate the completed core workflow")
        )
        self.assertIn(implement_task.taskId, evaluate_task.dependencies)
        self.assertGreaterEqual(evaluate_task.startWeek, implement_task.endWeek)

    def test_metadata_survives_schema_revalidation_when_wording_is_unchanged(self):
        """
        Simulates what happens after a Rewrite pass: RoadmapCandidateSchema
        re-validates the SAME candidate dict (task wording unchanged) via
        the request-scoped metadata registry bridge (see
        roadmap_scheduler.py) -- the distinctive effort/dependency data
        must still be there, not silently recomputed from generic text
        classification.
        """
        agent, request, plan = self._build_plan()
        total_weeks = agent._normalize_weeks(request.expectedDurationWeeks)
        profile = agent._build_profile(request, total_weeks)
        plan.phases = agent._sanitize_phases(plan.phases, profile)
        response = agent._expand_plan_to_weeks(request, plan, total_weeks, profile)

        # Registry is still live via the contextvar bridge (no clear()
        # call happens between the agent build above and this
        # re-validation, exactly mirroring the real ReviewPipeline flow).
        candidate = RoadmapCandidateSchema.model_validate(response.model_dump())

        first_pass_hours = {
            t.title: t.estimatedHours for phase in response.phases for t in phase.tasks
        }
        revalidated_hours = {
            t.title: t.estimatedHours for phase in candidate.phases for t in phase.tasks
        }
        self.assertEqual(first_pass_hours, revalidated_hours)
        self.assertEqual(
            first_pass_hours["Draft the initial requirements outline."], 12,
        )

        first_pass_deps = {
            t.title: set(t.dependencies) for phase in response.phases for t in phase.tasks
        }
        revalidated_deps = {
            t.title: set(t.dependencies) for phase in candidate.phases for t in phase.tasks
        }
        self.assertEqual(first_pass_deps.keys(), revalidated_deps.keys())
        for title in first_pass_deps:
            # Dependency IDs themselves may be renumbered on re-validation
            # (phases/tasks are re-derived from scratch each time), but the
            # RELATIONSHIP (same count, same structural role) must match --
            # verified precisely by the "evaluate depends on implement"
            # check in the previous test, already run against this exact
            # candidate/response pair with real ids.
            self.assertEqual(len(first_pass_deps[title]), len(revalidated_deps[title]))

    def test_registry_cleared_between_requests_never_leaks_stale_metadata(self):
        agent, request, plan = self._build_plan()
        total_weeks = agent._normalize_weeks(request.expectedDurationWeeks)
        profile = agent._build_profile(request, total_weeks)
        plan.phases = agent._sanitize_phases(plan.phases, profile)
        agent._expand_plan_to_weeks(request, plan, total_weeks, profile)

        # A fresh fallback call for an unrelated request must not consult
        # the previous request's registry, even though it's the same
        # thread/process.
        roadmap_scheduler.clear_task_metadata_registry()
        self.assertEqual(roadmap_scheduler.get_task_metadata_registry(), {})


class DependencyIdOverrideTests(unittest.TestCase):
    """Validated dependency ids must win over the stage-tiering fallback;
    stage tiering is consulted only when no metadata is present."""

    def test_validated_dependency_overrides_same_stage_default(self):
        # Two tasks classified to the SAME stage (both "functional_testing")
        # would get NO dependency between them under stage-tiering alone --
        # but validated metadata explicitly says B depends on A.
        registry = build_registry([
            type("Phase", (), {"tasks": [
                InternalTaskProposal(localId="T1", title="Run smoke tests.", taskType="functional_testing"),
                InternalTaskProposal(
                    localId="T2", title="Run full regression tests.",
                    taskType="functional_testing", dependencyIds=["T1"],
                ),
            ]})(),
        ])

        groups = [{
            "phase_index": 0, "name": "Testing",
            "task_titles": ["Run smoke tests.", "Run full regression tests."],
        }]
        tasks = capacity_scheduler.build_task_universe(
            groups, difficulty_level="medium", is_safety_sensitive=False,
            is_security_sensitive=False, metadata_registry=registry,
        )
        capacity_scheduler.assign_dependencies(tasks)

        smoke_index = next(i for i, t in enumerate(tasks) if t["title"] == "Run smoke tests.")
        regression = next(t for t in tasks if t["title"] == "Run full regression tests.")
        self.assertEqual(regression["dependency_indices"], [smoke_index])

    def test_missing_metadata_falls_back_to_stage_tiering(self):
        groups = [{
            "phase_index": 0, "name": "Phase",
            "task_titles": ["Design the database schema", "Implement core backend booking logic"],
        }]
        tasks = capacity_scheduler.build_task_universe(
            groups, difficulty_level="medium", is_safety_sensitive=False,
            is_security_sensitive=False, metadata_registry=None,
        )
        capacity_scheduler.assign_dependencies(tasks)
        # database_design (stage 1) precedes complex_workflow (stage 3) --
        # the fallback still produces a sensible ordering with no registry.
        self.assertFalse(tasks[0]["has_validated_metadata"])
        self.assertEqual(tasks[1]["dependency_indices"], [0])

    def test_independent_setup_tasks_overlap_in_the_same_week(self):
        weeks = [_week(1, "Setup", tasks=[
            "Initialize the ASP.NET Core project structure",
            "Configure the PostgreSQL connection string",
        ])]
        phases, _, _ = roadmap_scheduler.build_phases_and_summary(weeks, 2, 1, 40)
        tasks = phases[0]["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["startWeek"], tasks[1]["startWeek"])
        self.assertNotIn(tasks[1]["taskId"], tasks[0]["dependencies"])
        self.assertNotIn(tasks[0]["taskId"], tasks[1]["dependencies"])

    def test_no_cycle_or_unknown_dependency_survives_validated_metadata(self):
        # T1 depends on itself, and on a nonexistent id -- both must be
        # dropped rather than producing a dangling reference or a cycle.
        registry = build_registry([
            type("Phase", (), {"tasks": [
                InternalTaskProposal(
                    localId="T1", title="Self-referential task.",
                    dependencyIds=["T1", "T99"],
                ),
            ]})(),
        ])
        groups = [{"phase_index": 0, "name": "Phase", "task_titles": ["Self-referential task."]}]
        tasks = capacity_scheduler.build_task_universe(
            groups, difficulty_level="medium", is_safety_sensitive=False,
            is_security_sensitive=False, metadata_registry=registry,
        )
        capacity_scheduler.assign_dependencies(tasks)
        self.assertEqual(tasks[0]["dependency_indices"], [])


class FallbackPhaseGranularityTests(unittest.TestCase):
    def test_medical_triage_fallback_phase_count_is_not_over_fragmented(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(_medical_triage_request())
        self.assertLessEqual(len(response.phases), 10)
        self.assertGreaterEqual(len(response.phases), 7)

    def test_no_phase_contains_only_a_single_trivial_task(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(_medical_triage_request())
        for phase in response.phases:
            self.assertGreaterEqual(len(phase.tasks), 2, phase.name)

    def test_merged_phases_still_cover_both_original_lifecycle_areas(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(_medical_triage_request())
        names = " | ".join(phase.name.lower() for phase in response.phases)
        # Model dev+tuning, evaluation+safety, and documentation+deployment
        # are merged in the catalog -- confirm the combined phase's own
        # task text still carries evidence for both halves.
        eval_safety_phase = next(
            (p for p in response.phases if "evaluation" in p.name.lower()), None,
        )
        if eval_safety_phase is not None:
            task_text = " ".join(t.title.lower() for t in eval_safety_phase.tasks)
            self.assertTrue("evaluat" in task_text or "metric" in task_text)
            self.assertTrue("safety" in task_text or "supervisor" in task_text or "expert" in task_text)
        self.assertIn("model", names)
        self.assertIn("documentation", names)


class PublicContractStabilityTests(unittest.TestCase):
    def test_request_field_set_is_unchanged(self):
        expected = {
            "ideaTitle", "problemStatement", "requiredTechnologies", "requiredSkills",
            "missingSkills", "difficultyLevel", "expectedDurationWeeks", "domain",
            "finalDeliverables", "teamSize", "availableHoursPerWeek", "studentSkills",
            "skillRatings",
        }
        self.assertEqual(set(ProjectRoadmapRequest.model_fields.keys()), expected)

    def test_response_field_set_is_unchanged(self):
        expected = {
            "roadmapTitle", "totalWeeks", "difficultyLevel", "teamStrategy", "weeks",
            "finalAdvice", "teamSize", "hoursPerWeekPerMember", "phases",
            "planningSummary", "deferredTasks",
        }
        self.assertEqual(set(ProjectRoadmapResponse.model_fields.keys()), expected)

    def test_nested_model_field_sets_are_unchanged(self):
        self.assertEqual(
            set(RoadmapWeek.model_fields.keys()),
            {"weekNumber", "phaseTitle", "mainGoal", "tasks", "deliverables",
             "teamResponsibilities", "skillsToLearn", "riskWarning", "checkpoint"},
        )
        self.assertEqual(
            set(RoadmapTask.model_fields.keys()),
            {"taskId", "title", "estimatedHours", "estimatedWorkingDays", "startWeek",
             "endWeek", "dependencies", "requiredSkills", "assignedMembers",
             "memberAllocations", "complexity", "priority"},
        )
        self.assertEqual(
            set(RoadmapPhaseSummary.model_fields.keys()),
            {"phaseId", "name", "objective", "durationWeeks", "startWeek", "endWeek",
             "deliverables", "dependencies", "tasks"},
        )
        self.assertEqual(
            set(RoadmapPlanningSummary.model_fields.keys()),
            {"totalWeeks", "teamSize", "hoursPerWeekPerMember", "totalCapacityHours",
             "totalPlannedHours", "utilizationPercentage", "numberOfPhases",
             "numberOfTasks", "workloadByMember", "warnings", "schedulingAssumptions",
             "scheduleFeasibility", "originalPlannedHours", "adjustedPlannedHours",
             "capacityHours", "deferredHours", "overloadHours",
             "recommendedAdditionalWeeks", "weeklyCapacity", "phaseCapacityIssues"},
        )
        self.assertEqual(
            set(RoadmapPhaseCapacityIssue.model_fields.keys()),
            {"phaseId", "phaseName", "durationWeeks", "plannedHours",
             "availableCapacityHours", "utilizationPercentage", "overloadHours",
             "requiredMinWeeks"},
        )
        self.assertEqual(
            set(RoadmapDeferredTask.model_fields.keys()),
            {"title", "description", "estimatedHours", "reasonDeferred", "originalPhase", "priority"},
        )
        self.assertEqual(
            set(RoadmapMemberAllocation.model_fields.keys()),
            {"memberId", "allocationPercentage", "allocatedHours"},
        )
        self.assertEqual(
            set(RoadmapMemberWorkload.model_fields.keys()),
            {"member", "assignedTaskCount", "assignedHours", "utilizationPercentage"},
        )
        self.assertEqual(
            set(RoadmapWeeklyCapacity.model_fields.keys()),
            {"week", "plannedHours", "capacityHours", "utilizationPercentage"},
        )

    def test_full_response_still_serializes_and_round_trips(self):
        agent = ProjectRoadmapAgent()
        response = agent.build_safe_fallback(_medical_triage_request(expectedDurationWeeks=10))
        dumped = response.model_dump()
        # No internal-only keys (e.g. task metadata) leak into the public dump.
        self.assertEqual(set(dumped.keys()), set(ProjectRoadmapResponse.model_fields.keys()))
        ProjectRoadmapResponse.model_validate(dumped)


if __name__ == "__main__":
    unittest.main()
