"""
Deterministic Project Roadmap scheduler.

Pure functions only -- no LLM calls, no hashing of task text. This module is
the single source of truth for every scheduling number in a roadmap (phase
durations, task hours, dependencies, team assignment, workload totals,
overload resolution, weekly capacity), used from two call sites that must
stay behaviorally identical:

- RoadmapCandidateSchema (app/review/registry.py), applied to every
  candidate that passes through ReviewPipeline -- the Writer's first
  candidate AND every Rewrite pass -- so an LLM rewrite can never
  independently change a locked scheduling value without also changing the
  task text that deterministically drives it.
- ProjectRoadmapAgent.build_safe_fallback(), which bypasses ReviewPipeline
  entirely (the router calls it directly when nothing usable came back from
  the pipeline), so the safe fallback roadmap needs the exact same
  treatment applied directly.

The actual classification/effort/dependency/scheduling logic lives in
app/agents/roadmap/{lexicon,project_profile,task_taxonomy,capacity_scheduler}.py
-- this module wires them together and re-exports the small set of
functions the rest of the codebase (and the existing test suite) calls
directly, so no caller outside this package needs to change.
"""

from __future__ import annotations

import contextvars
import re
from typing import Any

from app.agents.roadmap import capacity_scheduler, project_profile, task_taxonomy

# ─────────────────────────────────────────────────────────────────────────
# Request-scoped internal task-metadata bridge
# ─────────────────────────────────────────────────────────────────────────
#
# RoadmapCandidateSchema's @model_validator (app/review/registry.py) calls
# build_phases_and_summary() with no channel to pass extra context beyond
# its own pydantic fields, which must stay exactly the public contract --
# yet it needs access to the SAME validated task metadata the Writer just
# built, so a Rewrite pass that only reworded an unrelated field doesn't
# lose that metadata for every other, unchanged task. A contextvar bridges
# this: the agent sets it once, right after validating the Writer's phase
# plan (project_roadmap_agent._expand_plan_to_weeks), and it stays set for
# the rest of that request's synchronous call chain (initial schema
# validation, then any Rewrite's own schema validation) without requiring
# any caller in between to thread it through explicitly.
#
# Always cleared at the start of ProjectRoadmapAgent.generate() and
# .build_safe_fallback() (the only two entry points that can reach
# RoadmapCandidateSchema for a given request), so a value can never survive
# from an earlier request/attempt into one that never set its own.
_task_metadata_registry: "contextvars.ContextVar[dict[str, Any] | None]" = contextvars.ContextVar(
    "roadmap_task_metadata_registry", default=None,
)


def set_task_metadata_registry(registry: dict[str, Any]) -> None:
    _task_metadata_registry.set(registry)


def clear_task_metadata_registry() -> None:
    _task_metadata_registry.set(None)


def get_task_metadata_registry() -> dict[str, Any]:
    return _task_metadata_registry.get() or {}


# ─────────────────────────────────────────────────────────────────────────
# Bounds
# ─────────────────────────────────────────────────────────────────────────

MIN_TOTAL_WEEKS = 4
MAX_TOTAL_WEEKS = 30

# estimatedWorkingDays = ceil(estimatedHours / EFFECTIVE_HOURS_PER_DAY) --
# one documented deterministic assumption, applied everywhere hours are
# estimated so the two figures can never contradict each other.
EFFECTIVE_HOURS_PER_DAY = task_taxonomy.EFFECTIVE_HOURS_PER_DAY

# Task-hour percentage split kept for allocate_task_hours() below (a
# standalone legacy utility -- see its own docstring).
PRIMARY_ALLOCATION_PERCENTAGE = 70
SECONDARY_ALLOCATION_PERCENTAGE = 30


# ─────────────────────────────────────────────────────────────────────────
# Total duration / phase-weight duration allocation
# ─────────────────────────────────────────────────────────────────────────


def normalize_total_weeks(requested: Any) -> int:
    """Reuse the caller's real requested duration; only guard against
    non-numeric or pathological values."""
    try:
        value = int(requested)
    except Exception:
        value = 10

    return max(MIN_TOTAL_WEEKS, min(value, MAX_TOTAL_WEEKS))


def allocate_phase_durations(weights: list[float], total_weeks: int) -> list[int]:
    """
    Scale proposed phase weights into positive integer week-counts summing
    EXACTLY to total_weeks (largest-remainder method). Used only as the
    LLM phase plan's INITIAL nominal week span (a semantic weighting hint
    for where to start scheduling) -- the FINAL phase durations reported to
    .NET are always reconstructed from actually-scheduled task weeks by
    build_phases_and_summary() below, never taken directly from this.

    Precondition: len(weights) <= total_weeks (callers with more proposed
    phases than available weeks must merge phases down first -- see
    ProjectRoadmapAgent._merge_phases_to_fit).
    """
    count = len(weights)

    if count == 0:
        return []

    if count > total_weeks:
        count = total_weeks
        weights = weights[:count]

    cleaned = [max(0.1, float(value or 1)) for value in weights]
    total_weight = sum(cleaned)

    ideal = [value * total_weeks / total_weight for value in cleaned]
    floors = [max(1, int(value)) for value in ideal]
    remainder = total_weeks - sum(floors)

    if remainder > 0:
        order = sorted(
            range(count),
            key=lambda i: ideal[i] - floors[i],
            reverse=True,
        )
        for i in range(remainder):
            floors[order[i % count]] += 1
    elif remainder < 0:
        for _ in range(-remainder):
            largest = max(range(count), key=lambda i: floors[i])
            if floors[largest] > 1:
                floors[largest] -= 1

    return floors


# ─────────────────────────────────────────────────────────────────────────
# Task text analysis -- thin re-exports of task_taxonomy (kept here so
# every existing caller/test that does `roadmap_scheduler.X` keeps working
# unchanged).
# ─────────────────────────────────────────────────────────────────────────


def classify_task_complexity(title: str, phase_name: str = "") -> str:
    return task_taxonomy.classify_task_complexity(title, phase_name)


def estimate_task_hours(
    title: str,
    phase_name: str = "",
    *,
    difficulty_level: str = "medium",
    is_safety_sensitive: bool = False,
    is_security_sensitive: bool = False,
) -> tuple[int, int]:
    """Deterministic (hours, working_days) estimate driven by the task's
    classified type + a small set of documented multipliers -- see
    task_taxonomy.estimate_task_hours. No hashing of task text."""
    return task_taxonomy.estimate_task_hours(
        title,
        phase_name,
        difficulty_level=difficulty_level,
        is_safety_sensitive=is_safety_sensitive,
        is_security_sensitive=is_security_sensitive,
    )


def compute_working_days(hours: int) -> int:
    return task_taxonomy.compute_working_days(hours)


def classify_task_priority(title: str, phase_name: str = "") -> str:
    return task_taxonomy.classify_task_priority(title, phase_name)


def extract_required_skills(title: str) -> list[str]:
    return task_taxonomy.extract_required_skills(title)


def is_padding_task(title: str) -> bool:
    return task_taxonomy.is_padding_task(title)


def is_vague_task(title: str) -> bool:
    return task_taxonomy.is_vague_task(title)


def are_duplicate_tasks(a: str, b: str) -> bool:
    return task_taxonomy.are_duplicate_tasks(a, b)


def deduplicate_tasks(titles: list[str]) -> list[str]:
    return task_taxonomy.deduplicate_tasks(titles)


def min_tasks_for_duration(duration_weeks: int, phase_name: str = "") -> int:
    return task_taxonomy.min_tasks_for_duration(duration_weeks, phase_name)


# ─────────────────────────────────────────────────────────────────────────
# Legacy standalone member-hour split (kept for API compatibility -- no
# longer called from build_phases_and_summary, which now uses
# capacity_scheduler.member_allocations() driven by the actual per-week
# placement instead of this task-in-isolation 70/30 split).
# ─────────────────────────────────────────────────────────────────────────


def allocate_task_hours(
    hours: int,
    complexity: str,
    member_hours: list[int],
    team_size: int,
) -> list[dict]:
    """
    Split one task's estimatedHours across 1 or 2 members, given a running
    per-member hour total (least-loaded member picked first). For two
    members, primary gets PRIMARY_ALLOCATION_PERCENTAGE% and secondary gets
    the REMAINDER (not an independently-rounded 30%), so the two
    allocatedHours always sum back to exactly `hours`.
    """
    primary = min(range(team_size), key=lambda i: (member_hours[i], i))

    if complexity == "complex" and team_size > 1:
        secondary = min(
            (i for i in range(team_size) if i != primary),
            key=lambda i: (member_hours[i], i),
        )
        primary_hours = round(hours * PRIMARY_ALLOCATION_PERCENTAGE / 100)
        secondary_hours = hours - primary_hours

        return [
            {
                "memberId": f"Member {primary + 1}",
                "allocationPercentage": PRIMARY_ALLOCATION_PERCENTAGE,
                "allocatedHours": primary_hours,
            },
            {
                "memberId": f"Member {secondary + 1}",
                "allocationPercentage": SECONDARY_ALLOCATION_PERCENTAGE,
                "allocatedHours": secondary_hours,
            },
        ]

    return [{
        "memberId": f"Member {primary + 1}",
        "allocationPercentage": 100,
        "allocatedHours": hours,
    }]


# ─────────────────────────────────────────────────────────────────────────
# Phase grouping (weeks -> phase groups, by consecutive matching phaseTitle)
# ─────────────────────────────────────────────────────────────────────────


def _group_weeks_into_phases(weeks: list[dict]) -> list[dict]:
    """Group consecutive weeks sharing the same phaseTitle into one phase
    group, in appearance order. Always produces at most len(weeks) groups."""
    groups: list[dict] = []

    for week in weeks:
        title = str(week.get("phaseTitle") or "Phase").strip() or "Phase"
        week_number = int(week.get("weekNumber") or (len(groups) + 1))

        if groups and groups[-1]["name"] == title:
            groups[-1]["raw_tasks"].extend(week.get("tasks") or [])
            groups[-1]["deliverables"].extend(week.get("deliverables") or [])
        else:
            groups.append({
                "phase_index": len(groups),
                "name": title,
                "raw_tasks": list(week.get("tasks") or []),
                "deliverables": list(week.get("deliverables") or []),
                "objective": str(week.get("mainGoal") or ""),
            })

    return groups


# ─────────────────────────────────────────────────────────────────────────
# Full schedule construction -- the capacity-aware replacement for the old
# "even split across the phase's nominal week range" approach.
# ─────────────────────────────────────────────────────────────────────────


def build_phases_and_summary(
    weeks: list[dict],
    total_weeks: int,
    team_size: int,
    hours_per_week_per_member: int,
    *,
    difficulty_level: str = "medium",
) -> tuple[list[dict], dict, list[dict]]:
    """
    Build the structured `phases` list, `planningSummary` dict, and
    `deferredTasks` list entirely from `weeks` (+ totalWeeks/teamSize/
    hoursPerWeekPerMember/difficultyLevel). Deterministic and idempotent:
    calling this again on the same weeks always reproduces the same
    phases/tasks/hours/assignments/workload/deferrals/feasibility,
    regardless of what a Rewrite pass may have carried in those fields on
    the candidate.

    Pipeline: classify every task's type/effort/priority from its title
    text -> defer optional/medium work if total hours exceed capacity ->
    build a stage-tiered dependency DAG (same-tier tasks are parallel,
    never a forced full chain) -> run a capacity-aware, multi-week-capable
    greedy scheduler across the whole timeline -> reconstruct each phase's
    startWeek/endWeek from where its own tasks actually landed.

    Returns (phases, planning_summary, deferred_tasks).
    """
    team_size = max(1, min(int(team_size or 1), 12))
    hours_per_week_per_member = max(1, int(hours_per_week_per_member or 10))
    total_weeks = int(total_weeks or len(weeks) or 1)

    groups = _group_weeks_into_phases(weeks)

    for group in groups:
        genuine = [
            task for task in group["raw_tasks"]
            if task and not task_taxonomy.is_padding_task(task)
        ]
        group["task_titles"] = task_taxonomy.deduplicate_tasks(genuine)

    seen_titles: list[str] = []
    for group in groups:
        kept_titles = []
        for title in group["task_titles"]:
            if any(task_taxonomy.are_duplicate_tasks(title, seen) for seen in seen_titles):
                continue
            seen_titles.append(title)
            kept_titles.append(title)
        group["task_titles"] = kept_titles

    # Safety/security sensitivity is re-derived from the candidate's OWN
    # narrative text (never from the original request, which isn't part of
    # the response schema) -- so this reaches the same answer whether it
    # runs on the Writer's first candidate or any later Rewrite pass.
    combined_text = " ".join(
        " ".join([group["name"], group["objective"], *group["task_titles"]])
        for group in groups
    )
    is_safety_sensitive = project_profile.text_indicates_medical_safety(combined_text)
    is_security_sensitive = project_profile.text_indicates_security_sensitivity(combined_text)

    # Contingency reserve: for higher-risk profiles (advanced+AI, medical,
    # external-API dependency, hardware dependency, or a large skill gap --
    # see project_profile.contingency_reserve_fraction), Python proactively
    # defers more optional/medium work than strictly necessary so a real
    # buffer survives, rather than planning every available hour. Derived
    # the same way as is_safety_sensitive above -- from this candidate's
    # own text plus its own skillsToLearn -- so it's consistent whether this
    # runs on the Writer's first candidate or a later Rewrite pass.
    distinct_skills_to_learn = len({
        skill for week in weeks for skill in (week.get("skillsToLearn") or []) if skill
    })
    reserve_fraction = project_profile.contingency_reserve_fraction(
        combined_text,
        difficulty_level=difficulty_level,
        distinct_skills_to_learn=distinct_skills_to_learn,
    )

    metadata_registry = get_task_metadata_registry()

    all_tasks = capacity_scheduler.build_task_universe(
        groups,
        difficulty_level=difficulty_level,
        is_safety_sensitive=is_safety_sensitive,
        is_security_sensitive=is_security_sensitive,
        metadata_registry=metadata_registry,
    )

    capacity_hours = total_weeks * hours_per_week_per_member * team_size
    original_planned_hours = sum(task["hours"] for task in all_tasks)

    # Deferral targets the reserve-adjusted ceiling (never below it, never
    # above the true nominal capacity) -- this can defer MORE optional/
    # medium work than strictly required to fit true capacity, on purpose,
    # to leave contingency room; it never touches critical/high work either
    # way, and the true nominal capacity_hours is still what's reported
    # publicly and what over_capacity is judged against below.
    deferral_ceiling_hours = min(capacity_hours, round(capacity_hours * (1 - reserve_fraction)))
    surviving_tasks, deferred_tasks = capacity_scheduler.defer_overflow(all_tasks, deferral_ceiling_hours)

    capacity_scheduler.assign_dependencies(surviving_tasks)
    planned_hours_by_week = capacity_scheduler.schedule_tasks(
        surviving_tasks, total_weeks, team_size, hours_per_week_per_member,
    )

    # Assign phaseId/taskId in one linear pass -- surviving_tasks is sorted
    # by (phase_index, stage, position), so all of one phase's tasks are
    # contiguous and a simple running counter suffices.
    phase_id_by_index: dict[int, str] = {}
    task_counter_in_phase: dict[int, int] = {}
    phase_counter = 0

    for task in surviving_tasks:
        phase_index = task["phase_index"]

        if phase_index not in phase_id_by_index:
            phase_counter += 1
            phase_id_by_index[phase_index] = f"P{phase_counter}"
            task_counter_in_phase[phase_index] = 0

        task_counter_in_phase[phase_index] += 1
        task["taskId"] = f"{phase_id_by_index[phase_index]}-T{task_counter_in_phase[phase_index]}"

    tasks_by_phase: dict[int, list[dict]] = {}
    for task in surviving_tasks:
        tasks_by_phase.setdefault(task["phase_index"], []).append(task)

    member_hours_total = [0] * team_size
    member_task_counts = [0] * team_size
    phases_out: list[dict] = []

    for group in groups:
        phase_tasks = tasks_by_phase.get(group["phase_index"])

        if not phase_tasks:
            continue

        phase_id = phase_id_by_index[group["phase_index"]]
        task_dicts: list[dict] = []

        for task in phase_tasks:
            allocations = capacity_scheduler.member_allocations(task)

            for allocation in allocations:
                member_index = int(allocation["memberId"].replace("Member", "").strip()) - 1
                member_hours_total[member_index] += allocation["allocatedHours"]
                member_task_counts[member_index] += 1

            dependencies = [
                surviving_tasks[dependency_index]["taskId"]
                for dependency_index in task["dependency_indices"]
            ]

            task_dicts.append({
                "taskId": task["taskId"],
                "title": task["title"],
                "estimatedHours": task["hours"],
                "estimatedWorkingDays": task_taxonomy.compute_working_days(task["hours"]),
                "startWeek": task["startWeek"],
                "endWeek": task["endWeek"],
                "dependencies": dependencies,
                "requiredSkills": task["requiredSkills"],
                "assignedMembers": [allocation["memberId"] for allocation in allocations],
                "memberAllocations": allocations,
                "complexity": task["complexity"],
                "priority": task["priority"],
            })

        start_week = min(task["startWeek"] for task in phase_tasks)
        end_week = max(task["endWeek"] for task in phase_tasks)

        deduped_deliverables = list(dict.fromkeys(
            d.strip() for d in group["deliverables"] if d and d.strip()
        ))[:4]

        phases_out.append({
            "phaseId": phase_id,
            "name": group["name"],
            "objective": group["objective"],
            "durationWeeks": end_week - start_week + 1,
            "startWeek": start_week,
            "endWeek": end_week,
            "deliverables": deduped_deliverables,
            "dependencies": [phases_out[-1]["phaseId"]] if phases_out else [],
            "tasks": task_dicts,
        })

    nominal_week_capacity = team_size * hours_per_week_per_member
    feasibility = capacity_scheduler.compute_feasibility(
        planned_hours_by_week, nominal_week_capacity, hours_per_week_per_member,
        team_size, len(deferred_tasks),
    )

    adjusted_planned_hours = sum(task["hours"] for task in surviving_tasks)
    deferred_hours = sum(task["estimatedHours"] for task in deferred_tasks)

    member_capacity_hours = total_weeks * hours_per_week_per_member
    workload_by_member = [
        {
            "member": f"Member {i + 1}",
            "assignedTaskCount": member_task_counts[i],
            "assignedHours": member_hours_total[i],
            "utilizationPercentage": (
                round((member_hours_total[i] / member_capacity_hours) * 100, 1)
                if member_capacity_hours > 0 else 0.0
            ),
        }
        for i in range(team_size)
    ]

    weekly_capacity = [
        {
            "week": week,
            "plannedHours": planned_hours_by_week.get(week, 0),
            "capacityHours": nominal_week_capacity,
            "utilizationPercentage": (
                round((planned_hours_by_week.get(week, 0) / nominal_week_capacity) * 100, 1)
                if nominal_week_capacity > 0 else 0.0
            ),
        }
        for week in range(1, total_weeks + 1)
    ]

    utilization = (
        round((adjusted_planned_hours / capacity_hours) * 100, 1)
        if capacity_hours > 0 else 0.0
    )
    # INTERNAL risk categorization only -- see project_profile.
    # utilization_risk_band's docstring -- never assigned to the public
    # scheduleFeasibility field, which stays one of the existing
    # "feasible" / "feasible_after_scope_reduction" / "over_capacity"
    # values .NET already understands; risk beyond that is expressed only
    # through these warnings and the schedulingAssumptions note below.
    risk_band = project_profile.utilization_risk_band(utilization)

    warnings: list[str] = []
    if feasibility["scheduleFeasibility"] == "over_capacity":
        warnings.append(
            f"Essential project scope exceeds available capacity by "
            f"{feasibility['overloadHours']}h even after deferring "
            f"optional work -- add approximately "
            f"{feasibility['recommendedAdditionalWeeks']} more week(s) or "
            "reduce the mandatory scope."
        )
    else:
        if feasibility["scheduleFeasibility"] == "feasible_after_scope_reduction":
            warnings.append(
                f"{len(deferred_tasks)} optional task(s) totalling "
                f"{deferred_hours}h were deferred to future enhancements to "
                "fit the available capacity."
            )

        if risk_band == "very_tight":
            remaining_hours = max(0, capacity_hours - adjusted_planned_hours)
            warnings.append(
                f"VERY TIGHT SCHEDULE: planned work uses {utilization}% of "
                f"total capacity, leaving only {remaining_hours}h of "
                "contingency for supervisor feedback, rework, or slippage -- "
                "consider trimming scope or requesting more time."
            )
        elif risk_band == "feasible_with_risk":
            warnings.append(
                f"Planned work uses {utilization}% of total capacity, which "
                "leaves limited contingency buffer -- monitor progress "
                "closely against the schedule."
            )
        elif utilization < 30:
            warnings.append(
                f"Planned work only uses {utilization}% of available capacity "
                "-- the timeline may be longer than necessary for this scope."
            )

    planning_summary = {
        "totalWeeks": total_weeks,
        "teamSize": team_size,
        "hoursPerWeekPerMember": hours_per_week_per_member,
        "totalCapacityHours": capacity_hours,
        "totalPlannedHours": adjusted_planned_hours,
        "utilizationPercentage": utilization,
        "numberOfPhases": len(phases_out),
        "numberOfTasks": sum(len(phase["tasks"]) for phase in phases_out),
        "workloadByMember": workload_by_member,
        "warnings": warnings,
        "schedulingAssumptions": [
            "Each task's effort is estimated with the PERT formula "
            "(min + 4*likely + max) / 6 from its own classified task type "
            "(e.g. 'crud_api', 'model_training_tuning') -- never from a hash "
            "of its wording, so the same task type always yields the same "
            "base hours.",
            f"Working days assume {task_taxonomy.EFFECTIVE_HOURS_PER_DAY} "
            "productive hours per day (estimatedWorkingDays = "
            f"ceil(estimatedHours / {task_taxonomy.EFFECTIVE_HOURS_PER_DAY})).",
            "A small, capped multiplier adjusts the base PERT estimate for "
            "project difficulty, and for safety/security-sensitive "
            "validation or security work specifically -- documented in "
            "task_taxonomy.py, never applied silently.",
            "Tasks in the same phase and the same natural build stage "
            "(e.g. two setup tasks) have no dependency between them and "
            "may be scheduled in the same week in parallel; a task depends "
            "only on the immediately preceding stage in its own phase, or "
            "on the previous phase's final stage.",
            "A task's hours are distributed across as many consecutive "
            "weeks as its effort and the team's remaining weekly capacity "
            "require -- a task is never forced onto a single week that "
            "cannot hold its full effort.",
            "Optional/medium-priority tasks may be deferred to future "
            "enhancements when planned hours exceed team capacity; "
            "critical/high-priority tasks are never auto-deferred.",
            "Phase startWeek/endWeek are reconstructed from where that "
            "phase's own tasks were actually scheduled, not from a "
            "pre-allocated week weight -- unused capacity is left as "
            "honest buffer rather than filled with invented tasks.",
            (
                f"A {round(reserve_fraction * 100)}% contingency reserve was "
                "proactively targeted for this higher-risk project profile "
                "(advanced+AI, medical/safety-sensitive, an external API "
                "dependency, a hardware dependency, or a large skill gap) by "
                "deferring optional/medium work earlier -- never by cutting "
                "mandatory scope or inventing tasks."
                if reserve_fraction > 0 else
                "Planning bands: <=80% utilization is comfortably feasible, "
                "80-85% feasible, 85-90% feasible with reduced contingency, "
                "90-100% very tight (flagged above), over 100% over_capacity."
            ),
        ],
        "scheduleFeasibility": feasibility["scheduleFeasibility"],
        "originalPlannedHours": original_planned_hours,
        "adjustedPlannedHours": adjusted_planned_hours,
        "capacityHours": capacity_hours,
        "deferredHours": deferred_hours,
        "overloadHours": feasibility["overloadHours"],
        "recommendedAdditionalWeeks": feasibility["recommendedAdditionalWeeks"],
        "weeklyCapacity": weekly_capacity,
    }

    return phases_out, planning_summary, deferred_tasks


# ─────────────────────────────────────────────────────────────────────────
# Dependency-cycle detection (defensive; build_phases_and_summary's own
# stage-tiered construction cannot produce a cycle by construction, but
# this is exercised directly with adversarial input in tests and used as a
# real guard in RoadmapCandidateSchema in case a future scheduler change
# ever breaks that guarantee).
# ─────────────────────────────────────────────────────────────────────────


def has_dependency_cycle(tasks: list[dict]) -> bool:
    graph = {task["taskId"]: task.get("dependencies", []) for task in tasks}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {task_id: WHITE for task_id in graph}

    def visit(task_id: str) -> bool:
        color[task_id] = GRAY
        for dependency in graph.get(task_id, []):
            if dependency not in color:
                continue
            if color[dependency] == GRAY:
                return True
            if color[dependency] == WHITE and visit(dependency):
                return True
        color[task_id] = BLACK
        return False

    return any(color[task_id] == WHITE and visit(task_id) for task_id in graph)
