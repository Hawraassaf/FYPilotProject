"""
ProjectRoadmapAgent — LLM-based customized project roadmap generation for FYPilot.

Design (v3 -- hybrid AI + deterministic planning engine):
- A deterministic ProjectProfile (app/agents/roadmap/project_profile.py) is
  built from the request FIRST -- project type, risk flags, required
  lifecycle coverage, and a duration/domain-sensitive phase-count range.
  This replaces a single fixed "design 4-7 phases" instruction that used to
  apply to every project regardless of its actual duration or domain.
- The LLM still designs the PHASES (names, goals, concrete tasks,
  deliverables, skills to learn, risk, checkpoint) for this specific idea,
  guided by the profile. Python owns everything else: lifecycle coverage
  validation, task classification, PERT effort estimation, dependency
  ordering, capacity-aware weekly scheduling, and phase duration (which is
  reconstructed from where tasks actually land, never taken directly from
  the LLM's guessed phase-week weight).
- The public request/response contract is UNCHANGED: .NET still sends
  ProjectRoadmapRequest and receives week-based ProjectRoadmapResponse.
- If every AI provider fails, a profile-aware deterministic fallback
  roadmap is built from a lifecycle-driven phase catalog and run through
  the exact same scheduler as an AI-generated plan.
"""

import json
import logging
import os
import re
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents import roadmap_scheduler
from app.agents.roadmap import deliverable_coverage, fallback_reason, project_profile, task_metadata, task_taxonomy
from app.agents.roadmap.lexicon import contains_any
from app.agents.roadmap.project_profile import ProjectProfile, ProjectProfileInput
from app.agents.roadmap.task_metadata import InternalTaskProposal
from app.services import json_reliability
from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-roadmap-agent")


# =============================================================================
# Public contract (UNCHANGED — .NET depends on these shapes)
# =============================================================================


class ProjectRoadmapRequest(BaseModel):
    ideaTitle: str
    problemStatement: str
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = "medium"
    expectedDurationWeeks: int = 10
    domain: str = ""
    finalDeliverables: str = ""

    teamSize: int = 1
    availableHoursPerWeek: int = 10
    studentSkills: list[str] = Field(default_factory=list)
    skillRatings: dict[str, int] = Field(default_factory=dict)


class RoadmapWeek(BaseModel):
    weekNumber: int
    phaseTitle: str
    mainGoal: str
    tasks: list[str]
    deliverables: list[str]
    teamResponsibilities: list[str]
    skillsToLearn: list[str]
    riskWarning: str
    checkpoint: str


class RoadmapMemberAllocation(BaseModel):
    memberId: str
    allocationPercentage: int
    allocatedHours: int


class RoadmapTask(BaseModel):
    taskId: str
    title: str
    estimatedHours: int
    estimatedWorkingDays: int
    startWeek: int
    endWeek: int
    dependencies: list[str] = Field(default_factory=list)
    requiredSkills: list[str] = Field(default_factory=list)
    # Kept as plain member labels for backward compatibility with anything
    # that already reads assignedMembers as strings; memberAllocations adds
    # the exact, non-double-counted hour split per member.
    assignedMembers: list[str] = Field(default_factory=list)
    memberAllocations: list[RoadmapMemberAllocation] = Field(default_factory=list)
    complexity: str = "medium"
    priority: str = "medium"


class RoadmapPhaseSummary(BaseModel):
    phaseId: str
    name: str
    objective: str = ""
    durationWeeks: int
    startWeek: int
    endWeek: int
    deliverables: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    tasks: list[RoadmapTask] = Field(default_factory=list)


class RoadmapMemberWorkload(BaseModel):
    member: str
    assignedTaskCount: int
    assignedHours: int
    utilizationPercentage: float


class RoadmapWeeklyCapacity(BaseModel):
    week: int
    plannedHours: int
    capacityHours: int
    utilizationPercentage: float


class RoadmapDeferredTask(BaseModel):
    title: str
    description: str = ""
    estimatedHours: int
    reasonDeferred: str
    originalPhase: str
    priority: str = "optional"


class RoadmapPhaseCapacityIssue(BaseModel):
    """One phase whose OWN planned effort exceeds the capacity its OWN
    scheduled span provides -- see capacity_scheduler.diagnose_phase_
    capacity's docstring for the confirmed live defect this reports (a
    103h phase clamped onto a single 20h-capacity week, invisible to the
    whole-project scheduleFeasibility/utilizationPercentage figures alone
    since those can look only marginally over capacity in aggregate)."""

    phaseId: str
    phaseName: str
    durationWeeks: int
    plannedHours: int
    availableCapacityHours: int
    utilizationPercentage: float
    overloadHours: int
    requiredMinWeeks: int


class RoadmapPlanningSummary(BaseModel):
    totalWeeks: int
    teamSize: int
    hoursPerWeekPerMember: int
    totalCapacityHours: int
    totalPlannedHours: int
    utilizationPercentage: float
    numberOfPhases: int
    numberOfTasks: int
    workloadByMember: list[RoadmapMemberWorkload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    schedulingAssumptions: list[str] = Field(default_factory=list)

    # Overload-resolution fields (Roadmap stabilization batch). All
    # defaulted so pre-stabilization candidates/tests without them still
    # validate.
    scheduleFeasibility: str = "feasible"
    originalPlannedHours: int = 0
    adjustedPlannedHours: int = 0
    capacityHours: int = 0
    deferredHours: int = 0
    overloadHours: int = 0
    recommendedAdditionalWeeks: int = 0
    weeklyCapacity: list[RoadmapWeeklyCapacity] = Field(default_factory=list)
    # Phase-LOCAL capacity check (Roadmap phase-capacity-validation batch).
    # Defaulted so pre-existing candidates/tests without it still validate
    # -- see RoadmapPhaseCapacityIssue's docstring.
    phaseCapacityIssues: list[RoadmapPhaseCapacityIssue] = Field(default_factory=list)


class ProjectRoadmapResponse(BaseModel):
    roadmapTitle: str
    totalWeeks: int
    difficultyLevel: str
    teamStrategy: str
    weeks: list[RoadmapWeek]
    finalAdvice: str

    # Additive fields (Roadmap dynamic-scheduling improvement). Existing
    # clients/tests that construct this model without them still work --
    # every new field has a safe default. teamSize/hoursPerWeekPerMember
    # simply echo the request so RoadmapCandidateSchema's validator can
    # deterministically recompute phases/planningSummary from the
    # candidate alone, without needing the original request.
    teamSize: int = 1
    hoursPerWeekPerMember: int = 10
    phases: list[RoadmapPhaseSummary] = Field(default_factory=list)
    planningSummary: RoadmapPlanningSummary | None = None
    # Optional/medium-priority tasks deferred by the overload-resolution
    # pass -- never silently deleted, always listed here even though
    # they're removed from `phases`.
    deferredTasks: list[RoadmapDeferredTask] = Field(default_factory=list)


# =============================================================================
# Internal phase-plan models (what the LLM actually generates)
# =============================================================================


class _PhasePlan(BaseModel):
    name: str
    weeks: int = 1  # advisory weight only -- see module docstring
    goal: str = ""
    tasks: list[InternalTaskProposal] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    skillsToLearn: list[str] = Field(default_factory=list)
    riskWarning: str = ""
    checkpoint: str = ""


class _RoadmapPlan(BaseModel):
    roadmapTitle: str = ""
    teamStrategy: str = ""
    finalAdvice: str = ""
    phases: list[_PhasePlan] = Field(default_factory=list)


# Compact description of the phase-plan JSON shape, given to a provider's
# OWN one-shot JSON-syntax repair request (see llm_provider.py's
# _repair_json_with_provider on each provider) -- deliberately terse (a
# repair call only needs to know the shape to restore, not the full
# generation prompt's rules/examples) so that request stays small.
# Concrete example phrasing for the lifecycle areas that were empirically
# observed (live, across DeepInfra and Groq) to be skipped most often even
# when listed in _LIFECYCLE_LABELS -- these are the areas a model tends to
# assume are "implicit" in another phase rather than writing a dedicated one
# for, so the prompt spells out what a compliant phase looks like.
_LIFECYCLE_HINTS: dict[str, str] = {
    "architecture_design": "'System Architecture and Database Design' -- decide the component layout and design the database schema, before core implementation starts",
    "data_governance_privacy": "'Data Governance and Privacy Compliance' -- define consent, storage, retention, and anonymization rules for the data this project collects",
    "safety_validation": "'Supervisor and Safety Validation' -- have the supervisor or a domain expert review outputs for safety before they are relied on",
}

_ROADMAP_PHASE_PLAN_SCHEMA_DESCRIPTION = """{
  "roadmapTitle": string, "teamStrategy": string, "finalAdvice": string,
  "phases": [{
    "name": string, "weeks": number, "goal": string,
    "tasks": [{
      "localId": string, "title": string, "description": string, "taskType": string,
      "effortMinHours": number, "effortLikelyHours": number, "effortMaxHours": number,
      "complexity": string, "mandatory": boolean, "dependencyIds": [string],
      "parallelizable": boolean, "requiredSkills": [string],
      "acceptanceCriteria": string, "scopeSize": string
    }],
    "deliverables": [string], "skillsToLearn": [string],
    "riskWarning": string, "checkpoint": string
  }]
}"""


# Human-readable phrasing for each lifecycle category key, used only to
# build prompt guidance -- the category KEYS themselves are the ones
# project_profile.lifecycle_coverage() checks against real phase text.
_LIFECYCLE_LABELS: dict[str, str] = {
    "requirements_scope": "requirements and scope definition",
    "architecture_design": "architecture / database design",
    "core_implementation": "core feature implementation",
    "application_implementation": "core web application implementation (screens, auth, persistence)",
    "integration": "backend-frontend / service integration",
    "testing_validation": "testing and validation",
    "documentation": "documentation",
    "final_delivery": "final deployment, submission, or presentation",
    "data_sourcing": "dataset sourcing",
    "data_preprocessing": "data cleaning / preprocessing / annotation",
    "baseline_model": "a baseline model",
    "model_evaluation": "model evaluation and error analysis",
    "data_governance_privacy": "data governance / privacy handling",
    "safety_validation": "safety or supervisor/domain-expert validation",
    "threat_modeling": "threat modeling",
    "security_testing": "security testing",
    "hardware_integration": "hardware/firmware integration",
    "calibration_reliability": "calibration and reliability testing",
}

# A gap here (the fundamentals every FYP roadmap needs) always blocks
# acceptance of the LLM's phase plan; a gap in a domain-specific category is
# tolerated in moderation (keyword coverage is inherently imperfect) but not
# if more than half of them are missing -- see _lifecycle_gap_is_blocking.
_CORE_LIFECYCLE_CATEGORIES = {
    "requirements_scope", "core_implementation", "application_implementation",
    "testing_validation", "final_delivery",
}


def _lifecycle_gap_is_blocking(missing_mandatory: list[str]) -> bool:
    if any(category in _CORE_LIFECYCLE_CATEGORIES for category in missing_mandatory):
        return True

    domain_missing = [c for c in missing_mandatory if c not in _CORE_LIFECYCLE_CATEGORIES]
    return len(domain_missing) > 1


# =============================================================================
# Agent
# =============================================================================


class ProjectRoadmapAgent:
    """
    AI-based Project Roadmap Generator.

    The LLM customizes phase content (names, goals, concrete tasks,
    deliverables, skills, risk, checkpoint) for this specific idea, guided
    by a deterministic ProjectProfile. Python owns lifecycle-coverage
    validation, task classification, effort estimation, dependency
    ordering, capacity-aware scheduling, and phase duration -- see
    app/agents/roadmap_scheduler.py and app/agents/roadmap/*.py. Uses the
    "high" DeepInfra tier -- the phase plan is the structural backbone
    every week entry, deliverable, and team-responsibility derivation
    downstream is built from, so it warrants the same accuracy tier as SE
    Documentation.
    """

    # See generate()'s content-gate retry loop docstring: raised from 1 to 2
    # (3 total attempts) 2026-08-16 after live testing showed a single retry
    # wasn't enough headroom against deliverable_coverage's literal-wording
    # sensitivity for an otherwise-correct, complete plan.
    _MAX_CONTENT_GATE_RETRIES = 2

    def __init__(self):
        # Own tier (not "high") so its Ollama fallback leg can use a
        # roadmap-specific timeout (ROADMAP_OLLAMA_TIMEOUT_SECONDS, default
        # 180s) without changing the shared "high" tier's timing for SE
        # Documentation/Idea Generator -- see llm_provider.py's
        # _DEEPINFRA_TIER_DEFAULTS["roadmap"] / _OLLAMA_TIER_TIMING.
        self.provider_chain = ProviderChain(tier="roadmap")

        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider: str | None = None
        self.last_model_used: str | None = None
        # One of "original_ai_candidate" / "original_ai_candidate_repaired"
        # / "provider_repaired_ai_candidate" / None (fallback, or not yet
        # attempted) -- classifies which stage actually produced the
        # accepted phase plan, for the roadmap.generated log line.
        self.last_generation_source: str | None = None

        # Correlation id for structured logging across one HTTP request --
        # set by the router (routers/roadmap.py) right after construction,
        # before generate_candidate()/generate() runs. None means "not set
        # by a router" (e.g. direct unit-test construction), in which case
        # log lines below just omit it.
        self.request_id: str | None = None

        # Typed diagnostics (see app/agents/roadmap/fallback_reason.py) --
        # populated whenever generate() falls back to the deterministic
        # roadmap, so the router can report a precise reason instead of the
        # single generic "provider_unavailable" pipeline status. Survives a
        # subsequent build_safe_fallback() call (which does not reset these)
        # so the router can read them AFTER calling build_safe_fallback().
        self.last_fallback_reason_code: str | None = None
        self.last_usable_phase_count: int = 0
        self.last_lifecycle_coverage_passed: bool = True
        self.last_missing_lifecycle_categories: list[str] = []
        self.last_blocked_term_tasks_dropped: int = 0
        # Populated whenever diagnose_roadmap_deliverable_coverage finds a
        # phase promising a technical artifact (e.g. "baseline urgency
        # classifier") that no task in that phase plausibly produces --
        # see app/agents/roadmap/deliverable_coverage.py.
        self.last_deliverable_coverage_passed: bool = True
        self.last_deliverable_coverage_issues: list[str] = []

        self.blocked_terms = [
            "react",
            "node.js",
            "nodejs",
            "vue",
            "angular",
            "flutter",
            "dart",
            "kafka",
            "azure",
            "aws",
            "gcp",
            "kubernetes",
            "blockchain",
            "web3",
            "solidity",
            "smart contract",
            "flask",
            "balsamiq",
            "figma",
        ]

    # =========================================================================
    # Main entry point
    # =========================================================================

    def _build_profile(self, request: ProjectRoadmapRequest, total_weeks: int) -> ProjectProfile:
        return project_profile.build_profile(ProjectProfileInput(
            idea_title=request.ideaTitle,
            problem_statement=request.problemStatement,
            required_technologies=request.requiredTechnologies,
            required_skills=request.requiredSkills,
            missing_skills=request.missingSkills,
            domain=request.domain,
            final_deliverables=request.finalDeliverables,
            difficulty_level=request.difficultyLevel,
            total_weeks=total_weeks,
            team_size=request.teamSize,
            hours_per_week=request.availableHoursPerWeek,
            student_skills=request.studentSkills,
            skill_ratings=request.skillRatings,
        ))

    def generate(
        self, request: ProjectRoadmapRequest, *, deadline: float | None = None,
    ) -> ProjectRoadmapResponse:
        """
        ``deadline``, when supplied, is an absolute time.monotonic()
        timestamp -- the Writer's own reserved slice of the ReviewPipeline's
        total budget (routers/roadmap.py's writer_deadline), strictly
        earlier than the deadline passed to ReviewPipeline.run() itself, so
        the Writer can never consume the time reserved for Reviewer/Rewrite.
        Forwarded to the provider cascade (see _try_generate_phase_plan),
        which clamps each provider's own configured timeout to whatever
        Writer budget actually remains and skips starting a fallback
        provider once too little remains -- see
        ProviderChain.generate_json's cap_timeout_to_deadline. None (the
        default) preserves the exact prior unbounded behavior for every
        caller that doesn't pass one (e.g. build_safe_fallback's own
        internal use of this class's helpers never needs a deadline, since
        it makes no LLM calls at all).
        """
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None
        self.last_generation_source = None
        # request_id is deliberately NOT reset here -- it is set once by the
        # router before generate()/generate_candidate() runs, for the whole
        # request's lifetime (see __init__'s docstring on the field).
        self.last_fallback_reason_code = None
        self.last_usable_phase_count = 0
        self.last_lifecycle_coverage_passed = True
        self.last_missing_lifecycle_categories = []
        self.last_blocked_term_tasks_dropped = 0
        # Defensive reset: a stale registry from an earlier request/attempt
        # in this same thread/task context must never be consulted for this
        # one -- see roadmap_scheduler.py's task-metadata bridge docstring.
        roadmap_scheduler.clear_task_metadata_registry()

        total_weeks = self._normalize_weeks(request.expectedDurationWeeks)
        profile = self._build_profile(request, total_weeks)

        logger.info(
            "roadmap.profile request_id=%s idea=%r types=%s risks=%s phases=[%d,%d] weekly_capacity=%d skill_gap=%s",
            self.request_id, request.ideaTitle[:60], profile.project_types, sorted(profile.risk_flags),
            profile.phase_count_min, profile.phase_count_max,
            profile.weekly_team_capacity, profile.skill_gap_level,
        )

        plan = self._try_generate_phase_plan(request, total_weeks, profile, deadline=deadline)

        # Up to _MAX_CONTENT_GATE_RETRIES fresh, independent generation
        # attempts (never a targeted "fix only these phases" rewrite) when
        # an attempt was discarded purely for failing a deterministic
        # CONTENT gate (lifecycle/deliverable coverage: the LLM answered
        # successfully, but the plan itself had a structural gap, e.g. a
        # phase promising a deliverable no task produces). Raised from a
        # single retry to _MAX_CONTENT_GATE_RETRIES after live testing
        # 2026-08-16 showed a genuinely complete, otherwise-correct 14-phase
        # medical/AI plan discarded to the generic fallback template twice
        # in a row on two DIFFERENT single-deliverable wording mismatches
        # each time -- one retry wasn't enough headroom for a gate this
        # sensitive to exact phrasing (see deliverable_coverage.py's
        # docstring on why it's literal-word-stem matching, not semantic
        # understanding). Each attempt is independently and freshly
        # generated, so a mismatch in attempt N has no bearing on whether
        # attempt N+1 repeats it. Deliberately NOT retried for
        # transport/timeout/schema-invalid failures (fallback_reason values
        # other than the two checked below) -- those indicate the provider
        # itself struggled, so an immediate retry would likely just repeat
        # the same failure while spending more of the shared deadline
        # budget for no benefit; PROVIDER_TIMEOUT/WRITER_DEADLINE_EXCEEDED
        # in particular are exactly the failures a retry could make worse.
        for _ in range(self._MAX_CONTENT_GATE_RETRIES):
            if not (
                (plan is None or not plan.phases)
                and self.last_fallback_reason_code in (
                    fallback_reason.LIFECYCLE_COVERAGE_FAILED,
                    fallback_reason.DELIVERABLE_COVERAGE_FAILED,
                )
                and (
                    deadline is None
                    or (deadline - time.monotonic()) >= ProviderChain._MIN_SECONDS_PER_PROVIDER_ATTEMPT
                )
            ):
                break

            logger.info(
                "roadmap.content_gate_retry request_id=%s previous_reason=%s",
                self.request_id, self.last_fallback_reason_code,
            )
            retry_plan = self._try_generate_phase_plan(request, total_weeks, profile, deadline=deadline)
            if retry_plan is not None and retry_plan.phases:
                plan = retry_plan
                break

        if plan is not None and plan.phases:
            try:
                response = self._expand_plan_to_weeks(request, plan, total_weeks, profile)
            except Exception:
                logger.exception(
                    "roadmap.expand_plan_failed request_id=%s -- falling back to deterministic roadmap.",
                    self.request_id,
                )
                self.last_error = "Failed to expand the AI phase plan into a schedule. Used fallback roadmap."
                self.last_llm_used = False
                self.last_fallback_reason_code = fallback_reason.UNKNOWN
                roadmap_scheduler.clear_task_metadata_registry()
            else:
                self.last_llm_used = True
                logger.info(
                    "roadmap.generated request_id=%s source=%s phases=%d weeks=%d totalPlannedHours=%d feasibility=%s",
                    self.request_id, self.last_generation_source or "original_ai_candidate",
                    len(response.phases), response.totalWeeks,
                    response.planningSummary.totalPlannedHours if response.planningSummary else -1,
                    response.planningSummary.scheduleFeasibility if response.planningSummary else "unknown",
                )
                return response

        if self.last_error is None:
            self.last_error = (
                "No AI provider returned a valid roadmap phase plan. "
                "Used fallback roadmap."
            )

        if self.last_fallback_reason_code is None:
            self.last_fallback_reason_code = fallback_reason.UNKNOWN

        raw = self._fallback_raw_roadmap(request, total_weeks, profile)
        response = self._complete_and_validate(request, raw, total_weeks)
        logger.info(
            "roadmap.generated request_id=%s source=fallback phases=%d weeks=%d totalPlannedHours=%d "
            "feasibility=%s fallback_reason_code=%s reason=%s",
            self.request_id, len(response.phases), response.totalWeeks,
            response.planningSummary.totalPlannedHours if response.planningSummary else -1,
            response.planningSummary.scheduleFeasibility if response.planningSummary else "unknown",
            self.last_fallback_reason_code, self.last_error,
        )
        return response

    # =========================================================================
    # Review pipeline integration (app/review/pipeline.py)
    # =========================================================================

    def build_safe_fallback(self, request: ProjectRoadmapRequest) -> ProjectRoadmapResponse:
        """
        Public entry point for the deterministic fallback roadmap -- the same
        template-based path generate() already falls back to internally when
        every provider fails, exposed publicly so routers never reach into a
        private method (matches FypMentorAgent.build_safe_fallback).
        """
        roadmap_scheduler.clear_task_metadata_registry()
        total_weeks = self._normalize_weeks(request.expectedDurationWeeks)
        profile = self._build_profile(request, total_weeks)
        raw = self._fallback_raw_roadmap(request, total_weeks, profile)
        return self._complete_and_validate(request, raw, total_weeks)

    def generate_candidate(
        self, request: ProjectRoadmapRequest, *, deadline: float | None = None,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses generate() end to
        end (LLM phase design -> deterministic capacity-aware scheduling)
        rather than duplicating it, then wraps the result as an LLMResult so
        it can flow through guarded_call like any other LLM stage.

        ``deadline`` (an absolute time.monotonic() timestamp) is forwarded
        unchanged to generate() -- see its docstring. guarded_call
        (app/llm_firewall/guard.py) calls this as a zero-arg
        ``request.call_fn()``, so the router supplies this via a closure
        (``lambda: agent.generate_candidate(request, deadline=writer_deadline)``,
        matching SE Documentation's identical pattern in routers/
        se_documentation.py) rather than a global or mutable-agent-state
        deadline -- safe under concurrent requests since each request gets
        its own ProjectRoadmapAgent instance (see routers/roadmap.py).

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- when
        generate() itself had to fall back internally (self.last_llm_used is
        False), whether that's because every provider failed OR because the
        Writer deadline was exceeded (self.last_fallback_reason_code
        distinguishes the two -- see fallback_reason.WRITER_DEADLINE_EXCEEDED).
        In either case there is no real candidate to review; the router uses
        build_safe_fallback() directly instead.
        """
        result = self.generate(request, deadline=deadline)

        if not self.last_llm_used:
            return None

        return LLMResult(
            ok=True,
            provider=self.last_provider or "unknown",
            model=self.last_model_used,
            text="",
            data=result.model_dump(),
        )

    # =========================================================================
    # LLM phase-plan generation
    # =========================================================================

    def _estimate_phase_plan_max_tokens(self, profile: ProjectProfile) -> int:
        """
        Sized to the structured per-task schema (title/description/
        taskType/3 effort numbers/complexity/mandatory/dependencyIds/
        parallelizable/requiredSkills/acceptanceCriteria/scopeSize -- much
        larger than a flat task-title-string schema) times the profile's
        own upper phase-count bound, so a project needing more phases gets
        a proportionally bigger budget instead of a flat default. A flat
        2200-token default was observed live truncating a provider's
        response for a 13-phase advanced AI/medical plan before this
        existed, which a fixed-budget repair request then couldn't fix
        either (see DeepInfraProvider._repair_json_with_provider).

        The *1.5 factor reuses _repair_max_tokens' own empirically-proven
        safety margin (see its docstring in llm_provider.py) rather than a
        fresh guess: live-observed 2026-08-16, a 14-phase medical/AI plan
        truncated at the un-multiplied budget (14100 tokens, stop_reason=
        max_tokens) on every single attempt, forcing a truncate-then-repair
        round trip (an extra ~90-100s provider call) EVERY time -- while
        that same plan's repair call, requesting 14100*1.5=21150 tokens,
        consistently finished cleanly (stop_reason=end_turn, well under its
        budget). Applying that already-validated 1.5x directly to the
        FIRST request lets most plans finish in one call instead of two.
        Cap raised from 16000 to 28000 to accommodate the larger scaled
        values without clipping them back down to the old, too-small cap.
        """
        return max(3000, min(28000, int((1500 + profile.phase_count_max * 900) * 1.5)))

    def _classify_generation_failure(
        self,
        deadline: float | None,
        error_category: str | None,
        error_text: str | None,
        *,
        default: str = fallback_reason.UNKNOWN,
    ) -> str:
        """
        WRITER_DEADLINE_EXCEEDED takes priority over every other
        classification whenever the Writer's own reserved deadline has
        effectively run out by the time a failure is observed -- not only
        when it has literally passed, but also when the remaining time is
        below ProviderChain's own _MIN_SECONDS_PER_PROVIDER_ATTEMPT floor
        (the SAME threshold ProviderChain._run_cascade uses to decide
        whether it's even worth starting one more attempt): a cascade that
        skipped every provider for that reason returns a generic "All
        providers failed" result well BEFORE the deadline timestamp itself
        is reached, so comparing only against the exact deadline would
        misclassify that (very common) case as an ordinary provider
        failure. "We ran out of time" is the more specific and actionable
        reason even when the provider's own error also looks like an
        ordinary timeout/transport failure (which it often will, since
        cap_timeout_to_deadline clamps each attempt's own timeout to
        whatever Writer budget remains). See app/agents/roadmap/
        fallback_reason.py's WRITER_DEADLINE_EXCEEDED.

        ``default`` is used instead of fallback_reason.UNKNOWN when
        error_category/error_text don't classify to anything more specific
        -- callers with their own reasonable prior (e.g. a bare transport
        exception with no category defaulted to PROVIDER_UNAVAILABLE before
        this deadline-awareness existed) pass it to preserve that exact
        behavior.
        """
        if (
            deadline is not None
            and (deadline - time.monotonic()) < ProviderChain._MIN_SECONDS_PER_PROVIDER_ATTEMPT
        ):
            return fallback_reason.WRITER_DEADLINE_EXCEEDED

        classified = fallback_reason.classify_provider_error(error_category, error_text)
        return classified if classified != fallback_reason.UNKNOWN else default

    def _try_generate_phase_plan(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
        profile: ProjectProfile,
        *,
        deadline: float | None = None,
    ) -> Optional[_RoadmapPlan]:
        prompt = self._build_phase_prompt(request, total_weeks, profile)

        # deadline alone only skips starting another fallback provider once
        # too little time remains (ProviderChain's existing, shared
        # _MIN_SECONDS_PER_PROVIDER_ATTEMPT check); cap_timeout_to_deadline
        # additionally shortens EACH provider's own configured request
        # timeout (up to 120s DeepInfra / 180s Ollama) to whatever Writer
        # budget is actually left. cap_timeout_to_deadline is only included
        # at all when a real deadline was supplied -- this keeps the call
        # shape byte-for-byte identical to before this change for every
        # caller that never passes one (e.g. test doubles built against the
        # pre-deadline generate_json signature), rather than relying on a
        # provider chain implementation to merely ignore an always-False flag.
        extra_kwargs: dict[str, Any] = {}
        if deadline is not None:
            extra_kwargs["cap_timeout_to_deadline"] = True

        try:
            result = self.provider_chain.generate_json(
                prompt, use_search=False,
                schema_description=_ROADMAP_PHASE_PLAN_SCHEMA_DESCRIPTION,
                max_tokens=self._estimate_phase_plan_max_tokens(profile),
                deadline=deadline,
                **extra_kwargs,
            )
        except Exception as ex:
            self.last_error = f"Roadmap generation failed: {ex}"
            self.last_fallback_reason_code = self._classify_generation_failure(
                deadline,
                json_reliability.TIMEOUT if "timeout" in str(ex).lower() else None,
                str(ex),
                default=fallback_reason.PROVIDER_UNAVAILABLE,
            )
            logger.exception("Roadmap generation failed. request_id=%s", self.request_id)
            return None

        self.last_provider = (
            result.provider if result.provider != "none" else None
        )
        self.last_model_used = result.model

        diagnostics = result.parse_diagnostics or {}
        # provider=="none" means the whole cascade was exhausted (every
        # provider failed or was skipped for lack of remaining deadline) --
        # ProviderChain._run_cascade's final LLMResult in that case carries
        # error_category=None (it was never set by any single provider
        # attempt), so checking error_category ALONE would print the
        # misleading "transport=success" even though literally no provider
        # ever returned output. Treat "no provider identified at all" as a
        # transport failure too.
        is_transport_failure = (
            result.provider == "none"
            or result.error_category in (
                json_reliability.TRANSPORT_FAILURE, json_reliability.TIMEOUT, json_reliability.PROVIDER_HTTP_ERROR,
            )
        )

        def log_provider_output(*, schema_valid: bool, semantic_valid: bool) -> None:
            # Matches the production log shape requested for this
            # stabilization pass -- distinguishes "provider responded but
            # output JSON was malformed" (transport=success,
            # initial_json_valid=false) from "provider failed to respond"
            # (transport=failure) from schema/semantic rejection of an
            # otherwise well-formed candidate.
            logger.info(
                "roadmap.provider_output request_id=%s provider=%s model=%s transport=%s "
                "initial_json_valid=%s repair_method=%s repair_success=%s "
                "schema_valid=%s semantic_valid=%s",
                self.request_id, self.last_provider, self.last_model_used,
                "failure" if is_transport_failure else "success",
                diagnostics.get("initialJsonValid"), diagnostics.get("repairMethod"),
                diagnostics.get("repairSuccess"), schema_valid, semantic_valid,
            )

        if not result.ok or not isinstance(result.data, dict):
            log_provider_output(schema_valid=False, semantic_valid=False)
            self.last_error = result.error or "No provider returned valid roadmap JSON."
            self.last_fallback_reason_code = self._classify_generation_failure(
                deadline, result.error_category, result.error,
            )
            return None

        self.last_raw_llm_response = json.dumps(
            result.data,
            ensure_ascii=False,
        )[:1500]

        try:
            plan = _RoadmapPlan.model_validate(result.data)
        except Exception as ex:
            log_provider_output(schema_valid=False, semantic_valid=False)
            self.last_error = f"Roadmap plan failed validation: {str(ex)}"
            self.last_fallback_reason_code = fallback_reason.SCHEMA_INVALID
            return None

        plan.phases = self._sanitize_phases(plan.phases, profile)
        self.last_usable_phase_count = len(plan.phases)

        if len(plan.phases) < 3:
            log_provider_output(schema_valid=True, semantic_valid=False)
            self.last_error = (
                "Roadmap plan contained fewer than 3 usable phases. "
                "Used fallback roadmap."
            )
            self.last_fallback_reason_code = fallback_reason.INSUFFICIENT_USABLE_PHASES
            return None

        phase_texts = [
            " ".join([phase.name, phase.goal, *(task.title for task in phase.tasks)])
            for phase in plan.phases
        ]
        _covered, missing_mandatory = project_profile.lifecycle_coverage(profile, phase_texts)
        self.last_missing_lifecycle_categories = list(missing_mandatory)

        # TEMPORARY diagnostic bypass (ROADMAP_LIFECYCLE_GATE_DISABLED=1) --
        # lets a partial AI candidate through even with missing mandatory
        # categories, so the raw model output can be inspected instead of
        # always seeing the generic deterministic fallback. Unset/remove
        # this before relying on the output as fully reviewed: nothing else
        # checks lifecycle coverage once this is on.
        _gate_disabled = os.getenv("ROADMAP_LIFECYCLE_GATE_DISABLED", "").strip().lower() in ("1", "true", "yes")
        if _gate_disabled and missing_mandatory:
            readable = ", ".join(_LIFECYCLE_LABELS.get(c, c) for c in missing_mandatory)
            logger.warning(
                "roadmap.lifecycle_gate_DISABLED_bypassing_rejection request_id=%s missing=%s",
                self.request_id, missing_mandatory,
            )
            self.last_lifecycle_coverage_passed = False
            self.last_error = (
                f"[LIFECYCLE GATE DISABLED FOR DEBUGGING] Plan is missing: {readable}. "
                f"Not auto-rejected -- verify these areas manually before using this plan."
            )
        elif missing_mandatory and _lifecycle_gap_is_blocking(missing_mandatory):
            readable = ", ".join(_LIFECYCLE_LABELS.get(c, c) for c in missing_mandatory)
            log_provider_output(schema_valid=True, semantic_valid=False)
            self.last_error = (
                f"Roadmap plan is missing mandatory lifecycle coverage for this "
                f"project type: {readable}. Used fallback roadmap."
            )
            self.last_fallback_reason_code = fallback_reason.LIFECYCLE_COVERAGE_FAILED
            self.last_lifecycle_coverage_passed = False
            logger.info(
                "roadmap.lifecycle_gap_rejected request_id=%s missing=%s", self.request_id, missing_mandatory,
            )
            return None

        if missing_mandatory:
            self.last_lifecycle_coverage_passed = False
            logger.info(
                "roadmap.lifecycle_gap_tolerated request_id=%s missing=%s", self.request_id, missing_mandatory,
            )

        # A phase must not promise a deliverable (e.g. "baseline urgency
        # classifier") that none of its own tasks plausibly produce -- see
        # deliverable_coverage.py's module docstring for the confirmed live
        # defect this closes. Checked after lifecycle coverage (a coarser,
        # whole-roadmap signal) and before the plan is accepted, using the
        # same debug-bypass knob as the lifecycle gate so both deterministic
        # gates can be inspected together.
        deliverable_issues = deliverable_coverage.diagnose_roadmap_deliverable_coverage(plan.phases)
        self.last_deliverable_coverage_issues = [
            f"{issue.phase_name}: '{issue.deliverable}'" for issue in deliverable_issues
        ]

        if _gate_disabled and deliverable_issues:
            logger.warning(
                "roadmap.deliverable_coverage_gate_DISABLED_bypassing_rejection request_id=%s issues=%s",
                self.request_id, self.last_deliverable_coverage_issues,
            )
            self.last_deliverable_coverage_passed = False
        elif deliverable_issues:
            log_provider_output(schema_valid=True, semantic_valid=False)
            self.last_error = (
                "Roadmap plan promised a deliverable that no task actually produces: "
                f"{'; '.join(self.last_deliverable_coverage_issues)}. Used fallback roadmap."
            )
            self.last_fallback_reason_code = fallback_reason.DELIVERABLE_COVERAGE_FAILED
            self.last_deliverable_coverage_passed = False
            logger.info(
                "roadmap.deliverable_coverage_rejected request_id=%s issues=%s",
                self.request_id, self.last_deliverable_coverage_issues,
            )
            return None
        else:
            self.last_deliverable_coverage_passed = True

        log_provider_output(schema_valid=True, semantic_valid=True)
        logger.info(
            "roadmap.gate_result request_id=%s usable_phase_count=%d lifecycle_coverage_passed=%s "
            "missing_lifecycle_categories=%s blocked_term_tasks_dropped=%d",
            self.request_id, self.last_usable_phase_count, self.last_lifecycle_coverage_passed,
            self.last_missing_lifecycle_categories, self.last_blocked_term_tasks_dropped,
        )

        repair_method = diagnostics.get("repairMethod")
        if repair_method == "provider_repair":
            self.last_generation_source = "provider_repaired_ai_candidate"
        elif repair_method == "local_json_repair":
            self.last_generation_source = "original_ai_candidate_repaired"
        else:
            self.last_generation_source = "original_ai_candidate"

        return plan

    def _sanitize_phases(
        self,
        phases: list[_PhasePlan],
        profile: ProjectProfile,
    ) -> list[_PhasePlan]:
        """
        Drop empty/vague phases, strip vague/blocked tasks. Never pads a
        phase back up with invented "Review and refine:"/"Test and verify:"
        filler -- a phase with too few genuine tasks is dropped rather than
        padded, since its real scheduled duration will now be derived from
        actual task effort, not from an artificially inflated task count.
        """
        cleaned: list[_PhasePlan] = []

        for phase in phases[: profile.phase_count_max + 4]:
            name = phase.name.strip()

            if not name:
                continue

            tasks = []
            for task in phase.tasks:
                if not task.title.strip() or task_taxonomy.is_vague_task(task.title):
                    continue
                if self._contains_blocked_term(task.title):
                    self.last_blocked_term_tasks_dropped += 1
                    continue
                tasks.append(task)

            if len(tasks) < 2:
                continue

            phase.name = name[:120]
            phase.weeks = max(1, min(int(phase.weeks or 1), 6))
            phase.goal = phase.goal.strip()[:400]
            phase.tasks = self._normalize_task_ids(tasks[:12])
            phase.deliverables = [
                item.strip() for item in phase.deliverables if item.strip()
            ][:4]
            phase.skillsToLearn = [
                item.strip() for item in phase.skillsToLearn if item.strip()
            ][:4]
            phase.riskWarning = phase.riskWarning.strip()[:250]
            phase.checkpoint = phase.checkpoint.strip()[:250]

            cleaned.append(phase)

        return cleaned[: max(3, min(len(cleaned), profile.phase_count_max + 2))]

    def _normalize_task_ids(
        self, tasks: list[InternalTaskProposal], id_prefix: str = "T",
    ) -> list[InternalTaskProposal]:
        """
        Reassigns every task in this phase a fresh, deterministic
        "{id_prefix}1".."{id_prefix}N" localId (in list order) and rewrites
        dependencyIds to match, using whatever localIds the LLM originally
        supplied to resolve them first. A dependencyId that never matched
        any task in THIS phase (missing, duplicated, self-referential, or
        pointing at a different phase) is silently dropped rather than left
        dangling -- the scheduler only ever trusts a dependency it can
        resolve to a real sibling task.

        Safe to call repeatedly (e.g. once per phase after sanitizing,
        again after merging two phases together) -- `id_prefix` lets two
        already-normalized phases (which independently reused "T1", "T2",
        ...) be combined without their ids colliding: normalize each side
        with a DIFFERENT prefix before concatenating, see
        _merge_phases_to_fit.
        """
        old_id_to_index = {
            task.localId: index for index, task in enumerate(tasks) if task.localId
        }

        renumbered: list[InternalTaskProposal] = []
        for index, task in enumerate(tasks):
            new_id = f"{id_prefix}{index + 1}"
            resolved_dependency_indices = sorted({
                old_id_to_index[old_id]
                for old_id in task.dependencyIds
                if old_id in old_id_to_index and old_id_to_index[old_id] != index
            })
            task.dependencyIds = [f"{id_prefix}{i + 1}" for i in resolved_dependency_indices]
            task.localId = new_id
            renumbered.append(task)

        return renumbered

    def _contains_blocked_term(self, text: str) -> bool:
        return contains_any(text, self.blocked_terms)

    def _build_phase_prompt(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
        profile: ProjectProfile,
    ) -> str:
        skills_text = (
            ", ".join(request.studentSkills)
            if request.studentSkills
            else "No skills provided"
        )

        ratings_text = (
            ", ".join(
                f"{skill}: {rating}/5"
                for skill, rating in request.skillRatings.items()
            )
            if request.skillRatings
            else "No ratings provided"
        )

        missing = self._split_items(request.missingSkills)
        missing_text = ", ".join(missing) if missing else "None"

        weekly_capacity = profile.weekly_team_capacity

        if weekly_capacity <= 12:
            capacity_note = (
                "Capacity is LOW. Keep phases lean, prefer fewer parallel tasks, "
                "and give implementation-heavy phases more weeks."
            )
        elif weekly_capacity <= 30:
            capacity_note = (
                "Capacity is MODERATE. Balance learning and implementation."
            )
        else:
            capacity_note = (
                "Capacity is HIGH. Implementation phases can be compressed and "
                "parallel workstreams are possible."
            )

        lifecycle_lines = "\n".join(
            f"  - {_LIFECYCLE_LABELS.get(category, category)}"
            + (f" (e.g. a phase like: {_LIFECYCLE_HINTS[category]})" if category in _LIFECYCLE_HINTS else "")
            for category in profile.mandatory_lifecycle
        )

        task_type_options = ", ".join(task_taxonomy.TASK_TYPES.keys())

        return f"""
You are ProjectRoadmapAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Design the PHASE PLAN for implementing this specific final year project. You design phases; the platform will run a deterministic capacity-aware scheduler on top of your tasks to produce the final week-by-week timeline, so treat "weeks" below as a rough emphasis weight, not a guaranteed final duration.

Project idea:
- Title: {request.ideaTitle}
- Problem statement: {request.problemStatement}
- Domain: {request.domain}
- Detected project type(s): {', '.join(profile.project_types)}
- Required technologies: {request.requiredTechnologies}
- Required skills: {request.requiredSkills}
- Missing skills (must appear in skillsToLearn, see rules below): {missing_text}
- Difficulty level: {request.difficultyLevel}
- Final deliverables: {request.finalDeliverables}

Student/team:
- Team size: {request.teamSize}
- Available hours per week per member: {request.availableHoursPerWeek}
- Total weekly capacity: about {weekly_capacity} hours
- Existing skills: {skills_text}
- Skill ratings: {ratings_text}
- {capacity_note}

Timeline:
- Total project duration: {total_weeks} weeks.
- You MUST design AT LEAST {profile.phase_count_min} phases (up to {profile.phase_count_max}) -- this is not a suggestion. A plan with fewer than {profile.phase_count_min} phases will be automatically rejected before a human ever sees it. Do not default to a generic 4-7 regardless of what is asked. Each phase's "weeks" field should be a rough weight (1-6).
- EVERY one of the following lifecycle areas MUST have its own clearly identifiable phase, with phase name/goal/tasks that unambiguously cover it. At most TWO areas may share a single phase, and only when they are genuinely one unit of work -- never compress three or more distinct areas into one "umbrella" phase, and never silently drop an area:
{lifecycle_lines}
  A plan that omits more than one of these areas will be automatically rejected and replaced with a generic, non-project-specific fallback roadmap -- so when in doubt, add another phase rather than skip or bury an area.
- Give MORE weeks/effort to the phases that are hardest FOR THIS SPECIFIC PROJECT AND TEAM: complex core features, AI/data work, and areas where skills are missing. Give FEWER weeks to easy or routine phases.
- Sequence work by TRUE technical prerequisites, not by lifecycle category label. Application-foundation work that has no real dependency on a trained/finalized AI model or on full end-to-end service integration -- project setup, authentication, core UI screens/pages, navigation, database persistence -- should be scheduled as soon as ITS OWN prerequisites (architecture and database design) are done, in an early-to-mid phase. Do not defer it to the final phase just because "the model" is still being built elsewhere -- they are independent workstreams. Reserve later phases for work that genuinely cannot start earlier (e.g. integrating a trained model artifact into a live service, end-to-end tests that need both the application and the service running, final validation/deployment/documentation).
- Never let one terminal phase absorb most of the project's remaining work. If a phase's own tasks would clearly need more effort than its "weeks" weight can hold at this team's weekly capacity, either give that phase visibly more weeks or move genuinely independent tasks into an earlier phase that still has spare capacity -- do not silently pile everything left into the last phase.

Phase design rules:
- The phase structure must fit THIS project. Phase names must mention the project's actual features, data, or components, not generic labels like "Core Feature Development".
- Every task must be concrete, testable, and connected to a real deliverable of THIS project -- one clear unit of work.
  BAD (too vague to estimate or test): "Implement main features", "Learn NLP", "Work on the backend", "Continue development".
  GOOD: "Implement the medicine reminder scheduling form and store reminders in PostgreSQL"; "Build an Arabic text-normalization prototype covering diacritics, letter variants, and tokenization, validated on 100 sample records".
- Do NOT invent filler tasks (e.g. "review and refine X", "test and verify X") just to pad the task count -- write only genuine, distinct units of work. A phase with fewer, better tasks is preferred over one padded with restatements.
- Missing skills ({missing_text}) must be listed in skillsToLearn. Only add a task for one when it produces a measurable artifact (e.g. a working prototype validated against sample data) -- never a bare "Learn X" task.
- Each phase needs enough distinct tasks to be independently schedulable -- roughly 3-5 concrete tasks per phase is typical; more for phases covering more lifecycle ground.
- 2 to 4 deliverables per phase, 0 to 4 skillsToLearn per phase.
- riskWarning: one short sentence about the biggest risk of that phase for this team.
- checkpoint: one short sentence describing what the supervisor should verify at the end of the phase.
- Technology constraints: use ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL, Bootstrap, and JavaScript for the deployed application. Ollama may be used where relevant, but it is typically a DEVELOPMENT-time or data-generation tool (e.g. generating synthetic training data) rather than a live runtime component -- do not describe Ollama as part of the deployed/runtime architecture (e.g. in an architecture diagram or integration task for the LIVE system) unless this specific project's own required technologies genuinely call for it as a runtime service. Do not suggest React, Node.js, Flutter, AWS, Azure, Kubernetes, blockchain, Flask, Balsamiq, or Figma.

Task fields -- the platform validates and clamps every number below against defensible per-type ranges before using it, so propose your honest best estimate rather than guessing conservatively:
- localId: short id unique WITHIN this phase only, e.g. "T1", "T2".
- title: the concrete task itself (see rules above).
- description: 1 short sentence of extra detail (optional).
- taskType: the SINGLE best match from this exact list -- {task_type_options}.
- effortMinHours / effortLikelyHours / effortMaxHours: your honest 3-point estimate in hours (min <= likely <= max) for THIS specific task, considering its measurable SCOPE where relevant (e.g. a task covering 10,000 records or 8 endpoints needs more hours than one covering 200 records or a single endpoint -- state the scope explicitly in scopeSize).
- complexity: one of "simple", "small", "medium", "complex", "testing".
- mandatory: true if this task is essential to the project working at all; false if it is genuinely optional/nice-to-have polish.
- dependencyIds: localIds of OTHER tasks in THIS SAME phase that must complete first (e.g. model evaluation depends on the model-training task's id; leave empty if nothing in this phase must finish first). Do not reference a task in a different phase -- cross-phase order is handled automatically.
- parallelizable: true if this task has no real dependency on other tasks in this phase and can run alongside them.
- requiredSkills: short list of concrete skills/technologies this task needs.
- acceptanceCriteria: 1 short sentence describing when this task is done.
- scopeSize: a short, explicit statement of this task's measurable scope when one exists (e.g. "200 annotated records", "8 API endpoints", "4 user roles", "3 model architectures compared") -- leave empty if the task genuinely has no such quantity.
- Return only valid JSON. No markdown, no explanations, no code fences.

Return exactly this JSON structure:

{{
  "roadmapTitle": "string",
  "teamStrategy": "1-2 sentences on how this specific team should divide and pace the work",
  "finalAdvice": "1-2 sentences of project-specific advice",
  "phases": [
    {{
      "name": "string",
      "weeks": 2,
      "goal": "string",
      "tasks": [
        {{
          "localId": "T1",
          "title": "string",
          "description": "string",
          "taskType": "string",
          "effortMinHours": 4,
          "effortLikelyHours": 8,
          "effortMaxHours": 14,
          "complexity": "medium",
          "mandatory": true,
          "dependencyIds": [],
          "parallelizable": true,
          "requiredSkills": ["string"],
          "acceptanceCriteria": "string",
          "scopeSize": "string"
        }}
      ],
      "deliverables": ["string"],
      "skillsToLearn": ["string"],
      "riskWarning": "string",
      "checkpoint": "string"
    }}
  ]
}}
"""

    # =========================================================================
    # Deterministic expansion: phase plan -> capacity-aware schedule -> weeks
    # =========================================================================

    def _expand_plan_to_weeks(
        self,
        request: ProjectRoadmapRequest,
        plan: _RoadmapPlan,
        total_weeks: int,
        profile: ProjectProfile,
    ) -> ProjectRoadmapResponse:
        phases = self._merge_phases_to_fit(plan.phases, total_weeks, profile)
        phase_plan_by_name = {phase.name: phase for phase in phases}

        # Validate every proposed task against task_taxonomy's defensible
        # ranges/known categories and resolve its dependencyIds into
        # sibling task TITLES (task_metadata.py) -- this registry, keyed by
        # title, is what lets a task's structured effort/dependency/
        # priority metadata survive into deterministic scheduling despite
        # `weeks[i].tasks` only ever carrying plain title strings publicly.
        # Set on the request-scoped bridge right before scheduling runs, so
        # it's still visible to RoadmapCandidateSchema's own validation
        # pass moments later (see roadmap_scheduler.py's registry bridge).
        metadata_registry = task_metadata.build_registry(phases)
        roadmap_scheduler.set_task_metadata_registry(metadata_registry)

        # One seed "week" per phase, carrying that phase's full task TITLE
        # list -- only used so roadmap_scheduler can re-group them back
        # into a phase and run the real capacity-aware scheduler on them
        # (which looks up each title in metadata_registry above). The LLM's
        # phase.weeks weight is intentionally NOT used to pre-allocate week
        # spans here; see module docstring.
        seed_weeks = [
            {
                "weekNumber": index + 1,
                "phaseTitle": phase.name,
                "mainGoal": phase.goal,
                "tasks": [task.title for task in phase.tasks],
                "deliverables": phase.deliverables,
                "skillsToLearn": phase.skillsToLearn,
            }
            for index, phase in enumerate(phases)
        ]

        phases_summary, planning_summary, deferred_tasks = roadmap_scheduler.build_phases_and_summary(
            weeks=seed_weeks,
            total_weeks=total_weeks,
            team_size=request.teamSize,
            hours_per_week_per_member=request.availableHoursPerWeek,
            difficulty_level=request.difficultyLevel,
        )

        if not phases_summary:
            raise ValueError("Capacity-aware scheduling produced zero usable phases.")

        missing_skills = self._split_items(request.missingSkills)
        weeks = self._build_public_weeks(
            request, phase_plan_by_name, phases_summary, total_weeks, missing_skills,
        )

        return ProjectRoadmapResponse(
            roadmapTitle=self._clean_text(
                plan.roadmapTitle,
                f"Implementation Roadmap for {request.ideaTitle}",
            ),
            totalWeeks=total_weeks,
            difficultyLevel=request.difficultyLevel,
            teamStrategy=self._clean_text(
                plan.teamStrategy,
                self._default_team_strategy(request),
            ),
            weeks=weeks,
            finalAdvice=self._clean_text(
                plan.finalAdvice,
                (
                    "Start with the MVP, review progress weekly, and avoid adding "
                    "advanced features before the core workflow is stable."
                ),
            ),
            teamSize=request.teamSize,
            hoursPerWeekPerMember=request.availableHoursPerWeek,
            phases=[RoadmapPhaseSummary.model_validate(p) for p in phases_summary],
            planningSummary=RoadmapPlanningSummary.model_validate(planning_summary),
            deferredTasks=[RoadmapDeferredTask.model_validate(d) for d in deferred_tasks],
        )

    def _merge_phases_to_fit(
        self,
        phases: list[_PhasePlan],
        total_weeks: int,
        profile: ProjectProfile,
    ) -> list[_PhasePlan]:
        """
        Merge the lowest-weighted adjacent phases (concatenating their
        tasks/deliverables/skillsToLearn) down to at most
        profile.phase_count_max (and never more than total_weeks, since a
        phase's own scheduled span cannot exceed the project's duration).
        """
        cap = max(3, min(profile.phase_count_max, total_weeks))
        merged = list(phases)

        while len(merged) > cap and len(merged) > 1:
            best_index = 0
            best_combined: int | None = None

            for i in range(len(merged) - 1):
                combined = (merged[i].weeks or 1) + (merged[i + 1].weeks or 1)
                if best_combined is None or combined < best_combined:
                    best_combined = combined
                    best_index = i

            first, second = merged[best_index], merged[best_index + 1]
            first.name = f"{first.name} & {second.name}"[:120]
            first.weeks = max(1, (first.weeks or 1) + (second.weeks or 1) - 1)
            first.goal = first.goal or second.goal
            # Both sides may reuse the same localIds ("T1", "T2", ...) --
            # normalize each side under a DIFFERENT prefix first so
            # concatenating them can never collide or scramble
            # dependencyIds resolution downstream.
            first.tasks = (
                self._normalize_task_ids(first.tasks, id_prefix="A")
                + self._normalize_task_ids(second.tasks, id_prefix="B")
            )[:16]
            first.deliverables = list(
                dict.fromkeys(first.deliverables + second.deliverables)
            )[:4]
            first.skillsToLearn = list(
                dict.fromkeys(first.skillsToLearn + second.skillsToLearn)
            )[:4]
            first.riskWarning = first.riskWarning or second.riskWarning
            first.checkpoint = second.checkpoint or first.checkpoint

            merged.pop(best_index + 1)

        return merged

    def _build_public_weeks(
        self,
        request: ProjectRoadmapRequest,
        phase_plan_by_name: dict[str, _PhasePlan],
        phases_summary: list[dict[str, Any]],
        total_weeks: int,
        missing_skills: list[str],
    ) -> list[RoadmapWeek]:
        """
        Builds the public per-week view FROM the scheduler's actual task
        placement -- a week shows exactly the tasks genuinely scheduled
        into it (which may be zero on a legitimate buffer week, never
        invented filler). Deliverables/checkpoint are surfaced on a phase's
        real final scheduled week, not on a pre-guessed one.

        A week's OWNING phase (used for phaseTitle/goal/deliverables/
        checkpoint) is decided by which phase has the MOST of its own
        tasks active that week, not by which phase's [startWeek, endWeek]
        RANGE happens to include it -- two adjacent phases' ranges can
        legitimately share a boundary week (phase B's first task starts
        the same week phase A's last task ends, if capacity allows), and
        picking "whichever phase's range was seen last" would mislabel
        that week's phaseTitle -- which would then make the SCHEMA
        re-validation pass (which re-derives phases purely from `weeks`,
        see roadmap_scheduler._group_weeks_into_phases) merge phase A's
        tasks into phase B's group, silently losing a phase on a Rewrite
        pass even though nothing about the schedule actually changed.
        """
        active_tasks_by_week: dict[int, list[str]] = {week: [] for week in range(1, total_weeks + 1)}
        active_phases_by_week: dict[int, list[dict[str, Any]]] = {week: [] for week in range(1, total_weeks + 1)}

        for phase in phases_summary:
            for task in phase["tasks"]:
                for week_number in range(task["startWeek"], task["endWeek"] + 1):
                    if 1 <= week_number <= total_weeks:
                        active_tasks_by_week.setdefault(week_number, []).append(task["title"])
                        active_phases_by_week.setdefault(week_number, []).append(phase)

        owning_phase_by_week: dict[int, dict[str, Any]] = {}
        for week_number, phases_active_this_week in active_phases_by_week.items():
            if not phases_active_this_week:
                continue

            counts: dict[int, int] = {}
            first_seen_order: dict[int, int] = {}
            for order, phase in enumerate(phases_active_this_week):
                key = id(phase)
                counts[key] = counts.get(key, 0) + 1
                first_seen_order.setdefault(key, order)

            best_key = max(counts, key=lambda key: (counts[key], -first_seen_order[key]))
            owning_phase_by_week[week_number] = next(
                phase for phase in phases_active_this_week if id(phase) == best_key
            )

        weeks: list[RoadmapWeek] = []

        for week_number in range(1, total_weeks + 1):
            phase = owning_phase_by_week.get(week_number) or (
                phases_summary[-1] if phases_summary else None
            )
            original_plan = phase_plan_by_name.get(phase["name"]) if phase else None
            week_tasks = active_tasks_by_week.get(week_number, [])
            is_last_week_of_phase = bool(phase) and week_number == phase["endWeek"]
            is_buffer_week = week_number not in owning_phase_by_week

            weeks.append(RoadmapWeek(
                weekNumber=week_number,
                phaseTitle=phase["name"] if phase else "Buffer & Supervisor Feedback",
                mainGoal=self._week_goal(phase, original_plan, is_buffer_week),
                tasks=week_tasks,
                deliverables=self._week_deliverables(phase, is_last_week_of_phase, is_buffer_week),
                teamResponsibilities=self._derive_responsibilities(
                    request, week_tasks, phase["name"] if phase else "buffer time",
                ),
                skillsToLearn=self._week_skills(original_plan, missing_skills, week_number),
                riskWarning=(
                    (original_plan.riskWarning if original_plan else "")
                    or ("" if is_buffer_week else "Avoid expanding the scope beyond this phase's goal.")
                ),
                checkpoint=self._week_checkpoint(phase, original_plan, is_last_week_of_phase, is_buffer_week),
            ))

        return weeks

    def _week_goal(
        self,
        phase: dict[str, Any] | None,
        original_plan: _PhasePlan | None,
        is_buffer_week: bool,
    ) -> str:
        if is_buffer_week or phase is None:
            return (
                "Buffer week: no additional scheduled work this week -- use it "
                "for supervisor feedback, rework, or getting ahead on the next phase."
            )

        return (original_plan.goal if original_plan and original_plan.goal else "") or (
            phase.get("objective") or f"Advance the {phase['name']} phase."
        )

    def _week_deliverables(
        self,
        phase: dict[str, Any] | None,
        is_last_week_of_phase: bool,
        is_buffer_week: bool,
    ) -> list[str]:
        if is_buffer_week or phase is None:
            return []

        deliverables = phase.get("deliverables") or []

        if is_last_week_of_phase and deliverables:
            return deliverables[:3]

        if deliverables:
            # roadmap_scheduler.PROGRESS_UPDATE_PREFIX (not a second literal
            # here) -- RoadmapCandidateSchema revalidates every candidate,
            # including this already-expanded public response, by rebuilding
            # `phases` from THESE `weeks[]` (roadmap_scheduler._group_weeks_
            # into_phases). Its own deliverable dedup only recognizes THIS
            # exact prefix to collapse "Progress update: X" back onto plain
            # "X" -- a drifted literal here would silently reintroduce the
            # duplicate-deliverable defect that fix closes.
            return [f"{roadmap_scheduler.PROGRESS_UPDATE_PREFIX}{deliverables[0]}"]

        return [f"Progress update on {phase['name']}"]

    def _week_checkpoint(
        self,
        phase: dict[str, Any] | None,
        original_plan: _PhasePlan | None,
        is_last_week_of_phase: bool,
        is_buffer_week: bool,
    ) -> str:
        if is_buffer_week or phase is None:
            return ""

        if is_last_week_of_phase:
            return (
                (original_plan.checkpoint if original_plan else "")
                or f"Supervisor reviews the completed {phase['name']} phase."
            )

        return f"Internal team review of {phase['name']} progress."

    def _week_skills(
        self,
        original_plan: _PhasePlan | None,
        missing_skills: list[str],
        week_number: int,
    ) -> list[str]:
        skills = list(original_plan.skillsToLearn) if original_plan else []

        # Front-load missing skills in the first two weeks of the roadmap
        # -- as skills to learn, never as a standalone vague task.
        if week_number <= 2:
            for skill in missing_skills:
                if skill not in skills:
                    skills.insert(0, skill)

        return skills[:4] if skills else []

    def _derive_responsibilities(
        self,
        request: ProjectRoadmapRequest,
        week_tasks: list[str],
        phase_name: str,
    ) -> list[str]:
        """
        Responsibilities are DERIVED from the week's actual tasks — one per
        member, guaranteed count, specific to this week, never a canned
        sentence repeated across weeks.
        """
        team_count = max(1, min(request.teamSize, 5))

        if team_count == 1:
            first_task = (
                self._lower_first(week_tasks[0])
                if week_tasks
                else f"progress the {phase_name} work"
            )
            return [f"Student: lead this week's work, starting with {first_task}"]

        responsibilities: list[str] = []

        for member_index in range(team_count):
            if week_tasks:
                task = week_tasks[member_index % len(week_tasks)]
                text = self._lower_first(task)

                if member_index >= len(week_tasks):
                    responsibilities.append(
                        f"Member {member_index + 1}: support and verify {text}"
                    )
                else:
                    responsibilities.append(
                        f"Member {member_index + 1}: own {text}"
                    )
            else:
                responsibilities.append(
                    f"Member {member_index + 1}: progress the {phase_name} work."
                )

        return responsibilities

    def _lower_first(self, text: str) -> str:
        clean = text.strip().rstrip(".")

        if not clean:
            return clean

        return clean[0].lower() + clean[1:]

    # =========================================================================
    # Fallback path (used only when every AI provider fails) -- profile-aware,
    # lifecycle-driven, and run through the exact same deterministic
    # scheduler as an AI-generated candidate.
    # =========================================================================

    def _complete_and_validate(
        self,
        request: ProjectRoadmapRequest,
        raw: dict[str, Any],
        total_weeks: int,
    ) -> ProjectRoadmapResponse:
        raw_weeks = raw.get("weeks")
        fallback_weeks = raw["weeks"]

        completed_weeks: list[RoadmapWeek] = []

        for index in range(total_weeks):
            raw_week = {}

            if isinstance(raw_weeks, list) and index < len(raw_weeks):
                if isinstance(raw_weeks[index], dict):
                    raw_week = raw_weeks[index]

            if not raw_week:
                raw_week = fallback_weeks[index]

            completed_weeks.append(
                self._complete_week(request, raw_week, index + 1, fallback_weeks[index])
            )

        phases_summary, planning_summary, deferred_tasks = roadmap_scheduler.build_phases_and_summary(
            weeks=[week.model_dump() for week in completed_weeks],
            total_weeks=total_weeks,
            team_size=request.teamSize,
            hours_per_week_per_member=request.availableHoursPerWeek,
            difficulty_level=request.difficultyLevel,
        )

        return ProjectRoadmapResponse(
            roadmapTitle=self._clean_text(
                raw.get("roadmapTitle"),
                f"Implementation Roadmap for {request.ideaTitle}",
            ),
            totalWeeks=total_weeks,
            difficultyLevel=self._clean_text(
                raw.get("difficultyLevel"),
                request.difficultyLevel,
            ),
            teamStrategy=self._clean_text(
                raw.get("teamStrategy"),
                self._default_team_strategy(request),
            ),
            weeks=completed_weeks,
            finalAdvice=self._clean_text(
                raw.get("finalAdvice"),
                (
                    "Start with the MVP, review progress weekly, and avoid adding "
                    "advanced features before the core workflow is stable."
                ),
            ),
            teamSize=request.teamSize,
            hoursPerWeekPerMember=request.availableHoursPerWeek,
            phases=[RoadmapPhaseSummary.model_validate(p) for p in phases_summary],
            planningSummary=RoadmapPlanningSummary.model_validate(planning_summary),
            deferredTasks=[RoadmapDeferredTask.model_validate(d) for d in deferred_tasks],
        )

    def _complete_week(
        self,
        request: ProjectRoadmapRequest,
        raw_week: dict[str, Any],
        week_number: int,
        fallback: dict[str, Any],
    ) -> RoadmapWeek:
        team_count = max(1, min(request.teamSize, 5))

        return RoadmapWeek(
            weekNumber=week_number,
            phaseTitle=self._clean_text(
                raw_week.get("phaseTitle"),
                fallback["phaseTitle"],
            ),
            mainGoal=self._clean_text(
                raw_week.get("mainGoal"),
                fallback["mainGoal"],
            ),
            tasks=self._list_of_strings(
                raw_week.get("tasks"),
                fallback["tasks"],
                min_items=0,
                max_items=8,
            ),
            deliverables=self._list_of_strings(
                raw_week.get("deliverables"),
                fallback["deliverables"],
                min_items=0,
                max_items=3,
            ),
            teamResponsibilities=self._list_of_strings(
                raw_week.get("teamResponsibilities"),
                fallback["teamResponsibilities"],
                min_items=team_count,
                max_items=team_count,
            ),
            skillsToLearn=self._list_of_strings(
                raw_week.get("skillsToLearn"),
                fallback["skillsToLearn"],
                min_items=0,
                max_items=4,
            ),
            riskWarning=self._clean_text(
                raw_week.get("riskWarning"),
                fallback["riskWarning"],
            ),
            checkpoint=self._clean_text(
                raw_week.get("checkpoint"),
                fallback["checkpoint"],
            ),
        )

    def _fallback_raw_roadmap(
        self,
        request: ProjectRoadmapRequest,
        total_weeks: int,
        profile: ProjectProfile,
    ) -> dict[str, Any]:
        return {
            "roadmapTitle": f"Implementation Roadmap for {request.ideaTitle}",
            "totalWeeks": total_weeks,
            "difficultyLevel": request.difficultyLevel,
            "teamStrategy": self._default_team_strategy(request),
            "weeks": [
                self._fallback_week(request, week_number, total_weeks, profile)
                for week_number in range(1, total_weeks + 1)
            ],
            "finalAdvice": (
                "Focus on the MVP first, then add AI or advanced features only "
                "after the core system works."
            ),
        }

    def _fallback_week(
        self,
        request: ProjectRoadmapRequest,
        week_number: int,
        total_weeks: int,
        profile: ProjectProfile,
    ) -> dict[str, Any]:
        phase = self._phase_for_week(total_weeks, week_number, profile)

        return {
            "weekNumber": week_number,
            "phaseTitle": phase,
            "mainGoal": f"Complete the {phase.lower()} phase for {request.ideaTitle}.",
            "tasks": self._default_tasks_for_phase(phase, request),
            "deliverables": self._default_deliverables_for_phase(phase),
            "teamResponsibilities": self._default_responsibilities(request, phase),
            "skillsToLearn": self._default_skills_to_learn(request, week_number),
            "riskWarning": "Avoid expanding the scope beyond the MVP.",
            "checkpoint": f"Supervisor reviews the {phase.lower()} progress.",
        }

    # -- Fallback phase catalog -------------------------------------------
    #
    # Each entry: (name, relative effort weight, essential-for-this-profile).
    # "Essential" phases are always kept when trimming to fit; the rest are
    # dropped first on a short project. Task/deliverable content for every
    # name below lives in _default_tasks_for_phase / _default_deliverables_for_phase.

    _WEB_BASE_CATALOG: list[tuple[str, float, bool]] = [
        ("Requirements and Scope Definition", 1.0, True),
        ("UI/UX Design and User Flow", 1.0, False),
        ("Database Design and Architecture", 1.0, True),
        ("Authentication and Core Setup", 1.0, False),
        ("Core Feature Development", 3.0, True),
        ("AI/API Service Integration", 2.0, False),
        ("Dashboard and Reports", 1.5, False),
        ("Testing and Bug Fixing", 2.0, True),
        ("Documentation and User Guide", 1.0, True),
        ("Final Demo and Presentation Preparation", 1.0, False),
        ("Supervisor Feedback Improvements", 1.0, False),
        ("Final Deployment and Submission", 1.0, True),
    ]

    _AI_DATA_CATALOG: list[tuple[str, float, bool]] = [
        ("Dataset Sourcing and Governance", 1.5, True),
        ("Data Preprocessing and Annotation", 1.5, True),
        ("Baseline Model Development", 2.0, True),
        ("Model Training and Tuning", 2.0, True),
        ("Model Evaluation and Error Analysis", 1.5, True),
    ]

    _SAFETY_CATALOG: list[tuple[str, float, bool]] = [
        ("Safety Validation and Supervisor Review", 1.0, True),
    ]

    _SECURITY_CATALOG: list[tuple[str, float, bool]] = [
        ("Threat Modeling and Security Requirements", 1.0, True),
        ("Security and Penetration Testing", 1.0, True),
    ]

    _IOT_CATALOG: list[tuple[str, float, bool]] = [
        ("Hardware and Firmware Integration", 1.5, True),
        ("Calibration and Reliability Testing", 1.5, True),
    ]

    # Phase-granularity review: combine two closely-related, typically
    # one-week catalog phases into one academically-clearer phase, so a
    # profile that pulls in several domain-specific catalog blocks (AI +
    # medical + security + ...) doesn't fragment into many thin phases.
    # Lifecycle coverage is preserved -- the merged phase's own task
    # content (see _default_tasks_for_phase) still covers both original
    # areas, just as one phase instead of two. (first_name, second_name,
    # merged_name); applied only when BOTH halves are present.
    _GRANULARITY_MERGE_GROUPS: tuple[tuple[str, str, str], ...] = (
        ("Baseline Model Development", "Model Training and Tuning", "Model Development and Tuning"),
        ("Model Evaluation and Error Analysis", "Safety Validation and Supervisor Review",
         "Model Evaluation and Safety Validation"),
        ("Documentation and User Guide", "Final Deployment and Submission",
         "Documentation and Final Deployment"),
        ("Threat Modeling and Security Requirements", "Security and Penetration Testing",
         "Security Threat Modeling and Testing"),
        ("Hardware and Firmware Integration", "Calibration and Reliability Testing",
         "Hardware Integration and Reliability Testing"),
    )

    # Normally target ~7-10 major phases for a full-length (16-week-ish)
    # project -- exceptions (a very long or lifecycle-heavy project) are
    # still allowed via profile.phase_count_max, but never above this.
    _FALLBACK_MAX_PHASES = 10

    def _apply_granularity_merges(
        self, catalog: list[tuple[str, float, bool]],
    ) -> list[tuple[str, float, bool]]:
        by_name = {name: (weight, essential) for name, weight, essential in catalog}
        order = [name for name, _weight, _essential in catalog]
        merged_away: set[str] = set()
        replacements: dict[str, tuple[str, float, bool]] = {}

        for first_name, second_name, merged_name in self._GRANULARITY_MERGE_GROUPS:
            if first_name not in by_name or second_name not in by_name:
                continue

            first_weight, first_essential = by_name[first_name]
            second_weight, second_essential = by_name[second_name]

            replacements[first_name] = (
                merged_name, first_weight + second_weight, first_essential or second_essential,
            )
            merged_away.add(second_name)

        result: list[tuple[str, float, bool]] = []
        for name in order:
            if name in merged_away:
                continue
            if name in replacements:
                result.append(replacements[name])
            else:
                weight, essential = by_name[name]
                result.append((name, weight, essential))

        return result

    def _fallback_phase_catalog(self, profile: ProjectProfile) -> list[tuple[str, float, bool]]:
        catalog = list(self._WEB_BASE_CATALOG)

        is_ai_data = any(
            project_type in profile.project_types
            for project_type in ("ai_ml", "nlp", "computer_vision", "data_science")
        )

        if is_ai_data:
            catalog = [entry for entry in catalog if entry[0] != "AI/API Service Integration"]
            insert_at = next(
                (i for i, entry in enumerate(catalog) if entry[0] == "Core Feature Development"),
                len(catalog),
            )
            for offset, entry in enumerate(self._AI_DATA_CATALOG):
                catalog.insert(insert_at + offset, entry)

        testing_index = next(
            (i for i, entry in enumerate(catalog) if entry[0] == "Testing and Bug Fixing"),
            len(catalog),
        )

        if profile.is_safety_sensitive:
            for offset, entry in enumerate(self._SAFETY_CATALOG):
                catalog.insert(testing_index + offset, entry)
            testing_index += len(self._SAFETY_CATALOG)

        if profile.is_security_sensitive:
            for offset, entry in enumerate(self._SECURITY_CATALOG):
                catalog.insert(testing_index + offset, entry)
            testing_index += len(self._SECURITY_CATALOG)

        if "iot" in profile.project_types:
            core_index = next(
                (i for i, entry in enumerate(catalog) if entry[0] == "Core Feature Development"),
                len(catalog),
            )
            for offset, entry in enumerate(self._IOT_CATALOG):
                catalog.insert(core_index + offset, entry)

        return self._apply_granularity_merges(catalog)

    def _dynamic_phase_ranges(
        self, total_weeks: int, profile: ProjectProfile,
    ) -> list[tuple[str, int, int]]:
        """
        Select as many catalog phases as fit total_weeks/profile guidance
        (essential ones always kept, see _fallback_phase_catalog), restore
        their natural dependency order, then allocate dynamic, weighted
        week ranges via roadmap_scheduler.allocate_phase_durations. This is
        only the SEED span used to build fallback weeks -- the final
        phase/task schedule reported to .NET is still reconstructed from
        actual scheduled task effort by build_phases_and_summary, exactly
        like the AI-generated path.
        """
        catalog = self._fallback_phase_catalog(profile)
        essential = [entry for entry in catalog if entry[2]]
        optional = [entry for entry in catalog if not entry[2]]

        # Essential phases are never dropped even if that exceeds
        # _FALLBACK_MAX_PHASES (lifecycle completeness wins), but the
        # OPTIONAL fill-in never pushes a normal project past it -- see
        # _apply_granularity_merges for the primary way phase count is
        # kept in the ~7-10 range for a full-length project.
        target_count = max(
            len(essential), min(profile.phase_count_max, total_weeks, self._FALLBACK_MAX_PHASES),
        )
        selected = list(essential)

        for entry in optional:
            if len(selected) >= target_count:
                break
            selected.append(entry)

        # Restore original catalog order (essential-first insertion above
        # would otherwise scramble the natural build sequence).
        order_index = {entry[0]: i for i, entry in enumerate(catalog)}
        selected.sort(key=lambda entry: order_index[entry[0]])

        names = [entry[0] for entry in selected]
        weights = [entry[1] for entry in selected]
        durations = roadmap_scheduler.allocate_phase_durations(weights, total_weeks)

        ranges: list[tuple[str, int, int]] = []
        week = 1
        for name, duration in zip(names, durations):
            ranges.append((name, week, week + duration - 1))
            week += duration

        return ranges

    def _phase_for_week(self, total_weeks: int, week_number: int, profile: ProjectProfile) -> str:
        for name, start_week, end_week in self._dynamic_phase_ranges(total_weeks, profile):
            if start_week <= week_number <= end_week:
                return name

        return "Requirements and Scope Definition"

    def _normalize_weeks(self, weeks: int) -> int:
        return roadmap_scheduler.normalize_total_weeks(weeks)

    def _default_tasks_for_phase(self, phase: str, request: ProjectRoadmapRequest) -> list[str]:
        title = request.ideaTitle or "the project"

        mapping = {
            "Requirements and Scope Definition": [
                f"Refine the final problem statement for {title}.",
                "Define target users and MVP feature list.",
                "Confirm project boundaries and out-of-scope items with the supervisor.",
                "Write functional requirements for each core feature.",
            ],
            "UI/UX Design and User Flow": [
                "Design the main page layouts and navigation flow.",
                "Create wireframes for the primary user workflow.",
                "Review the user experience and page flow with the team.",
            ],
            "Database Design and Architecture": [
                "Identify the main entities and their relationships.",
                "Create the database tables, keys, and relationships in PostgreSQL.",
                "Document how project data will be stored and queried.",
                "Review the database schema with the supervisor.",
            ],
            "Authentication and Core Setup": [
                "Set up the ASP.NET Core Razor Pages project structure.",
                "Implement login, roles, and access control.",
                "Connect the web application to the PostgreSQL database.",
            ],
            "Core Feature Development": [
                f"Implement the main {title} workflow end to end.",
                "Connect the primary forms to the database with validation.",
                "Build the core business logic for the main use case.",
                "Manually verify the core feature against the requirements.",
            ],
            "AI/API Service Integration": [
                "Create the Python FastAPI endpoint for the AI feature.",
                "Send the relevant project data from .NET to the AI service.",
                "Display the structured AI results in the web application.",
                "Handle a graceful fallback when the AI service is unavailable.",
            ],
            "Dashboard and Reports": [
                "Build summary cards for the main dashboard.",
                "Display roadmap/analysis results in a readable layout.",
                "Add progress indicators for ongoing work.",
            ],
            "Testing and Bug Fixing": [
                "Write and run functional tests for the main user flows.",
                "Fix validation and database issues found during testing.",
                "Test edge cases (empty input, invalid data, concurrent use).",
                "Improve error messages shown to the user.",
            ],
            "Documentation and User Guide": [
                "Write the technical documentation for the implemented system.",
                "Prepare annotated screenshots of each major screen.",
                "Write setup and run instructions for both the .NET and Python services.",
            ],
            "Final Demo and Presentation Preparation": [
                "Prepare a concrete demo scenario covering the core workflow.",
                "Practice explaining the AI/service integration to the panel.",
                "Test the full project end to end before the demo.",
            ],
            "Supervisor Feedback Improvements": [
                "Apply the supervisor's feedback from the last checkpoint.",
                "Polish the UI and reports based on that feedback.",
                "Verify the previously reported issues are actually fixed.",
            ],
            "Final Deployment and Submission": [
                "Prepare the final project package and configuration.",
                "Verify the database and AI service configuration for submission.",
                "Assemble the final submission files per the FYP checklist.",
                "Run a final end-to-end demo test before submission.",
            ],
            "Dataset Sourcing and Governance": [
                f"Identify and collect a dataset appropriate for {title}.",
                "Document dataset provenance, licensing, and consent constraints.",
                "Define a data governance policy (storage, access, retention).",
            ],
            "Data Preprocessing and Annotation": [
                "Clean and normalize the raw dataset (missing values, duplicates, formatting).",
                "Build the text/data preprocessing pipeline (tokenization, normalization).",
                "Annotate or label a representative sample of the data.",
                "Validate preprocessing output on a held-out sample of records.",
            ],
            "Baseline Model Development": [
                "Implement a simple baseline model as a performance reference.",
                "Train the baseline on the prepared dataset.",
                "Record the baseline's metrics for later comparison.",
            ],
            "Model Training and Tuning": [
                "Select and implement the target model architecture.",
                "Train the model and tune its key hyperparameters.",
                "Track training runs and their configurations for reproducibility.",
            ],
            "Model Evaluation and Error Analysis": [
                "Evaluate the trained model on a held-out test set with concrete metrics.",
                "Perform error analysis on a sample of misclassified/incorrect cases.",
                "Document the model's known limitations and failure patterns.",
            ],
            "Safety Validation and Supervisor Review": [
                "Define explicit safety boundaries and disclaimers for the AI output.",
                "Run the system against known failure-case / edge-case scenarios.",
                "Review the AI output and safety boundaries with the supervisor or domain expert.",
            ],
            "Threat Modeling and Security Requirements": [
                "Identify the system's attack surface and likely threat scenarios.",
                "Define security requirements (authentication, authorization, data protection).",
                "Document the threat model and mitigation plan.",
            ],
            "Security and Penetration Testing": [
                "Test authentication and access-control boundaries for common bypasses.",
                "Run a basic vulnerability/security review of the deployed endpoints.",
                "Fix and re-verify any security issues found.",
            ],
            "Hardware and Firmware Integration": [
                "Assemble and wire the required hardware/sensor components.",
                "Implement the firmware/communication logic for the device.",
                "Integrate the hardware layer with the backend service.",
            ],
            "Calibration and Reliability Testing": [
                "Calibrate sensor readings against known reference values.",
                "Run extended reliability/stress testing of the hardware-software link.",
                "Document calibration results and reliability findings.",
            ],
            # Phase-granularity merges (see _GRANULARITY_MERGE_GROUPS) --
            # curated, trimmed task lists for the combined phase rather
            # than a raw concatenation of both source phases' tasks.
            "Model Development and Tuning": [
                "Implement a simple baseline model as a performance reference.",
                "Select and implement the target model architecture.",
                "Train the model and tune its key hyperparameters against the baseline.",
                "Track training runs and their configurations for reproducibility.",
            ],
            "Model Evaluation and Safety Validation": [
                "Evaluate the trained model on a held-out test set with concrete metrics.",
                "Perform error analysis on a sample of misclassified/incorrect cases.",
                "Define explicit safety boundaries and disclaimers for the AI output.",
                "Review the model output and safety boundaries with the supervisor or domain expert.",
            ],
            "Documentation and Final Deployment": [
                "Write the technical documentation for the implemented system.",
                "Write setup and run instructions for both the .NET and Python services.",
                "Prepare the final project package and configuration.",
                "Run a final end-to-end demo test before submission.",
            ],
            "Security Threat Modeling and Testing": [
                "Identify the system's attack surface and likely threat scenarios.",
                "Define security requirements (authentication, authorization, data protection).",
                "Test authentication and access-control boundaries for common bypasses.",
                "Fix and re-verify any security issues found.",
            ],
            "Hardware Integration and Reliability Testing": [
                "Assemble and wire the required hardware/sensor components.",
                "Implement the firmware/communication logic for the device.",
                "Calibrate sensor readings against known reference values.",
                "Run extended reliability/stress testing of the hardware-software link.",
            ],
        }

        return mapping.get(
            phase,
            [
                f"Progress the {phase.lower()} work for {title}.",
                "Review progress with the team and record blockers.",
                "Prepare a concrete deliverable for this phase.",
            ],
        )

    def _default_deliverables_for_phase(self, phase: str) -> list[str]:
        mapping = {
            "Requirements and Scope Definition": ["Requirements document", "MVP feature list"],
            "UI/UX Design and User Flow": ["Wireframes", "User flow diagram"],
            "Database Design and Architecture": ["Database schema", "Entity relationship plan"],
            "Authentication and Core Setup": ["Authentication pages", "Initial project skeleton"],
            "Core Feature Development": ["Working core feature", "Database-connected forms"],
            "AI/API Service Integration": ["Connected AI endpoint", "Displayed AI results"],
            "Dashboard and Reports": ["Dashboard cards", "Report section"],
            "Testing and Bug Fixing": ["Tested workflows", "Bug fix list"],
            "Documentation and User Guide": ["Technical documentation", "User guide"],
            "Final Demo and Presentation Preparation": ["Demo script", "Presentation outline"],
            "Supervisor Feedback Improvements": ["Improved UI", "Updated features"],
            "Final Deployment and Submission": ["Final project package", "Submission checklist"],
            "Dataset Sourcing and Governance": ["Sourced dataset", "Data governance notes"],
            "Data Preprocessing and Annotation": ["Cleaned dataset", "Preprocessing pipeline"],
            "Baseline Model Development": ["Baseline model", "Baseline metrics"],
            "Model Training and Tuning": ["Trained model", "Training configuration log"],
            "Model Evaluation and Error Analysis": ["Evaluation report", "Error analysis notes"],
            "Safety Validation and Supervisor Review": ["Safety boundary documentation", "Supervisor sign-off notes"],
            "Threat Modeling and Security Requirements": ["Threat model document", "Security requirements list"],
            "Security and Penetration Testing": ["Security test report", "Fixed vulnerabilities list"],
            "Hardware and Firmware Integration": ["Assembled hardware unit", "Firmware build"],
            "Calibration and Reliability Testing": ["Calibration report", "Reliability test results"],
            "Model Development and Tuning": ["Trained model", "Training configuration log"],
            "Model Evaluation and Safety Validation": ["Evaluation report", "Safety boundary documentation"],
            "Documentation and Final Deployment": ["Technical documentation", "Final project package"],
            "Security Threat Modeling and Testing": ["Threat model document", "Security test report"],
            "Hardware Integration and Reliability Testing": ["Assembled hardware unit", "Reliability test results"],
        }

        return mapping.get(phase, [f"{phase} output", "Updated project progress"])

    def _default_responsibilities(
        self,
        request: ProjectRoadmapRequest,
        phase: str,
    ) -> list[str]:
        if request.teamSize <= 1:
            return [f"Student: complete the {phase.lower()} tasks and report progress."]

        phase_lower = phase.lower()

        if "requirements" in phase_lower or "scope" in phase_lower:
            responsibilities = [
                "Member 1: define requirements and MVP scope.",
                "Member 2: collect user needs and prepare documentation.",
                "Member 3: review feasibility and technical constraints.",
                "Member 4: validate project assumptions and constraints.",
                "Member 5: organize supervisor feedback notes.",
            ]
        elif "ui" in phase_lower or "user flow" in phase_lower:
            responsibilities = [
                "Member 1: review UI flow with backend requirements.",
                "Member 2: design layouts and page structure.",
                "Member 3: prepare usability notes and testing checklist.",
                "Member 4: review accessibility and navigation.",
                "Member 5: document UI decisions.",
            ]
        elif "database" in phase_lower:
            responsibilities = [
                "Member 1: design database entities and relationships.",
                "Member 2: review forms and required stored data.",
                "Member 3: prepare sample records and test cases.",
                "Member 4: check database constraints and indexes.",
                "Member 5: document schema decisions.",
            ]
        elif "authentication" in phase_lower:
            responsibilities = [
                "Member 1: implement authentication and roles.",
                "Member 2: test login, logout, and access flow.",
                "Member 3: document security behavior.",
                "Member 4: review authorization rules.",
                "Member 5: prepare test accounts.",
            ]
        elif "core feature" in phase_lower:
            responsibilities = [
                "Member 1: implement backend logic for the core workflow.",
                "Member 2: build the UI for the main workflow.",
                "Member 3: test database-connected features.",
                "Member 4: write validation scenarios.",
                "Member 5: document feature behavior.",
            ]
        elif "dataset" in phase_lower or "data preprocessing" in phase_lower or "annotation" in phase_lower:
            responsibilities = [
                "Member 1: source and document the dataset.",
                "Member 2: build the preprocessing/cleaning pipeline.",
                "Member 3: annotate/label the sample data.",
                "Member 4: validate preprocessing output quality.",
                "Member 5: document governance and licensing constraints.",
            ]
        elif (
            "baseline model" in phase_lower
            or "model training" in phase_lower
            or "model tuning" in phase_lower
            or "model development" in phase_lower
        ):
            responsibilities = [
                "Member 1: implement and train the model.",
                "Member 2: tune hyperparameters and track experiments.",
                "Member 3: prepare the evaluation harness.",
                "Member 4: document model architecture decisions.",
                "Member 5: prepare training data splits.",
            ]
        elif "model evaluation" in phase_lower or "error analysis" in phase_lower:
            responsibilities = [
                "Member 1: run evaluation metrics on the test set.",
                "Member 2: perform error analysis on failure cases.",
                "Member 3: document model limitations.",
                "Member 4: compare against the baseline.",
                "Member 5: prepare the evaluation report.",
            ]
        elif "safety" in phase_lower:
            responsibilities = [
                "Member 1: define safety boundaries and disclaimers.",
                "Member 2: run failure-case/edge-case testing.",
                "Member 3: coordinate supervisor/domain-expert review.",
                "Member 4: document safety findings.",
                "Member 5: verify fixes to any safety issue found.",
            ]
        elif "threat model" in phase_lower or "security" in phase_lower:
            responsibilities = [
                "Member 1: define the threat model and attack surface.",
                "Member 2: implement security controls.",
                "Member 3: run security/penetration tests.",
                "Member 4: document findings and mitigations.",
                "Member 5: verify fixes against the original threat model.",
            ]
        elif "hardware" in phase_lower or "firmware" in phase_lower or "calibration" in phase_lower:
            responsibilities = [
                "Member 1: assemble and wire hardware components.",
                "Member 2: implement firmware/communication logic.",
                "Member 3: calibrate sensors against reference values.",
                "Member 4: run reliability/stress testing.",
                "Member 5: document hardware integration results.",
            ]
        elif "ai" in phase_lower or "api" in phase_lower:
            responsibilities = [
                "Member 1: connect .NET to the Python FastAPI endpoint.",
                "Member 2: test API responses in the UI.",
                "Member 3: validate AI output and fallback behavior.",
                "Member 4: document API request and response fields.",
                "Member 5: test error handling when AI is unavailable.",
            ]
        elif "dashboard" in phase_lower or "reports" in phase_lower:
            responsibilities = [
                "Member 1: prepare dashboard data from the backend.",
                "Member 2: design dashboard cards and layout.",
                "Member 3: test displayed summaries and reports.",
                "Member 4: improve visual clarity.",
                "Member 5: document dashboard behavior.",
            ]
        elif "testing" in phase_lower:
            responsibilities = [
                "Member 1: test backend and database flows.",
                "Member 2: test UI and user experience.",
                "Member 3: document bugs and verify fixes.",
                "Member 4: test edge cases.",
                "Member 5: prepare final testing notes.",
            ]
        elif "documentation" in phase_lower:
            responsibilities = [
                "Member 1: write technical implementation details.",
                "Member 2: prepare screenshots and user guide.",
                "Member 3: review formatting and completeness.",
                "Member 4: write setup instructions.",
                "Member 5: prepare feature descriptions.",
            ]
        elif "presentation" in phase_lower or "demo" in phase_lower:
            responsibilities = [
                "Member 1: explain backend and database workflow.",
                "Member 2: explain UI and user flow.",
                "Member 3: explain AI/API integration.",
                "Member 4: prepare demo scenario.",
                "Member 5: practice expected questions.",
            ]
        elif "feedback" in phase_lower:
            responsibilities = [
                "Member 1: apply backend-related feedback.",
                "Member 2: polish UI based on feedback.",
                "Member 3: verify fixed issues.",
                "Member 4: update documentation.",
                "Member 5: prepare final review notes.",
            ]
        elif "deployment" in phase_lower or "submission" in phase_lower:
            responsibilities = [
                "Member 1: verify backend configuration.",
                "Member 2: check UI and navigation.",
                "Member 3: test AI service startup.",
                "Member 4: prepare submission files.",
                "Member 5: run final demo test.",
            ]
        else:
            responsibilities = [
                "Member 1: handle backend-related tasks.",
                "Member 2: handle UI and documentation tasks.",
                "Member 3: handle testing and support tasks.",
                "Member 4: review project quality.",
                "Member 5: prepare progress notes.",
            ]

        return responsibilities[: request.teamSize]

    def _default_skills_to_learn(
        self,
        request: ProjectRoadmapRequest,
        week_number: int,
    ) -> list[str]:
        missing = self._split_items(request.missingSkills)

        if week_number <= 3 and missing:
            return missing[:3]

        if week_number <= 3:
            return ["requirements analysis", "database planning"]

        if week_number <= 6:
            return ["ASP.NET Core implementation", "PostgreSQL integration"]

        return ["testing", "documentation", "presentation preparation"]

    def _default_team_strategy(self, request: ProjectRoadmapRequest) -> str:
        if request.teamSize <= 1:
            return (
                "Solo strategy: keep the MVP small, finish core features first, "
                "and avoid unnecessary advanced modules."
            )

        return (
            f"Team strategy: divide work across {request.teamSize} members, "
            "with clear ownership for backend, UI, AI/API integration, testing, "
            "and documentation."
        )

    def _clean_text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback

        if isinstance(value, list):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()

        if not text:
            return fallback

        if self._contains_blocked_term(text):
            return fallback

        return text

    def _list_of_strings(
        self,
        value: Any,
        fallback: list[str],
        min_items: int,
        max_items: int,
    ) -> list[str]:
        items: list[str] = []

        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]

        fallback_index = 0

        while len(items) < min_items and fallback:
            fallback_item = fallback[fallback_index % len(fallback)]

            if fallback_item not in items:
                items.append(fallback_item)

            fallback_index += 1

        return items[:max_items]

    def _split_items(self, text: str) -> list[str]:
        if not text:
            return []

        parts = re.split(r",|;|\n", text)

        return [part.strip() for part in parts if part.strip()]
