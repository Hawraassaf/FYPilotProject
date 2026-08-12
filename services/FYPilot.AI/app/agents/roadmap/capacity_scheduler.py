"""
Capacity-aware deterministic list scheduler.

Replaces the old approach of (a) letting the LLM's proposed phase "weeks"
directly become the final phase duration, and (b) placing every task on
exactly one nominal week via even split (`_distribute_weeks`). Both of those
made it possible for a week to silently carry more hours than the team's
real capacity while still being reported "feasible", and for a phase's
duration to be whatever the LLM guessed rather than what its tasks actually
need.

This module instead:
  1. Classifies every task's type/stage/effort/priority from its title text
     (task_taxonomy.py) -- never from the LLM's raw numbers, so a Rewrite
     that only changes wording can never independently change a number.
  2. Builds a real dependency DAG: tasks in the same (phase, stage-tier) are
     independent of each other (parallelizable); a tier depends only on the
     immediately preceding tier in the same phase, or on the previous
     phase's final tier if it's the first tier in this phase.
  3. When mandatory + optional work together exceed total capacity, defers
     optional/medium-priority tasks (largest hours first) -- never a
     critical/high one -- before scheduling, same policy as before.
  4. Runs a greedy list-scheduling pass across the WHOLE timeline: each
     task starts at the earliest week its dependencies allow, then consumes
     as much of each week's *remaining* team/member capacity as it needs,
     spilling into as many following weeks as its effort requires. A week's
     capacity ledger is shared across every task placed in it, so
     independent same-tier tasks naturally land in the same week when
     capacity allows (true parallelism) instead of being forced one-per-week.
  5. Reconstructs each phase's startWeek/endWeek from where its OWN tasks
     actually landed -- not from a pre-allocated weight split.

If, despite deferral, the greedy pass still can't fit everything within
total_weeks (a known limitation of any non-backtracking list scheduler),
the leftover hours are clamped onto the last valid week so every
startWeek/endWeek stays structurally valid, and the resulting per-week
overcommitment is reported honestly via scheduleFeasibility="over_capacity"
-- never silently hidden, never presented as "feasible".
"""

from __future__ import annotations

import math
from typing import Any

from app.agents.roadmap import task_taxonomy

_MANDATORY_PRIORITIES = {"critical", "high"}
_DEFERRABLE_PRIORITIES = {"optional", "medium"}


# ─────────────────────────────────────────────────────────────────────────
# Step 1: classify raw (phase, title) pairs into scheduling-ready tasks
# ─────────────────────────────────────────────────────────────────────────


def build_task_universe(
    phase_groups: list[dict[str, Any]],
    *,
    difficulty_level: str,
    is_safety_sensitive: bool,
    is_security_sensitive: bool,
    metadata_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    phase_groups: [{"phase_index": int, "name": str, "task_titles": [str]}]
    Returns a flat list of task dicts, ordered by (phase_index, stage tier,
    original position within phase) -- this ordering is itself a valid
    topological order for the dependency edges assigned in
    assign_dependencies(), since a dependency can only ever point at a task
    with a strictly smaller (phase_index, stage) key.

    When `metadata_registry` (title -> task_metadata.ValidatedTaskMetadata)
    has an entry for a task's exact title, that ALREADY-VALIDATED type/
    effort/priority/skills data is used instead of re-deriving everything
    from the title text -- this is how a Writer-proposed task's structured
    metadata survives into scheduling. A title with no registry entry (no
    registry at all, or a Rewrite pass changed its wording) falls back to
    the existing deterministic text classification, unchanged.
    """
    metadata_registry = metadata_registry or {}
    raw: list[dict[str, Any]] = []

    for group in phase_groups:
        for position, title in enumerate(group["task_titles"]):
            validated = metadata_registry.get(title)

            if validated is not None:
                type_key = validated.taskType
                hours, days = task_taxonomy.estimate_task_hours(
                    title,
                    group["name"],
                    difficulty_level=difficulty_level,
                    is_safety_sensitive=is_safety_sensitive,
                    is_security_sensitive=is_security_sensitive,
                    effort_override=(
                        validated.effortMinHours, validated.effortLikelyHours, validated.effortMaxHours,
                    ),
                    scope_hint=validated.scopeSize,
                    type_key_override=type_key,
                )
                priority = task_taxonomy.classify_task_priority(
                    title, group["name"], mandatory_hint=validated.mandatory,
                )
                complexity = validated.complexity
                required_skills = list(validated.requiredSkills) or task_taxonomy.extract_required_skills(title)
            else:
                type_key = task_taxonomy.classify_task_type(title, group["name"])
                hours, days = task_taxonomy.estimate_task_hours(
                    title,
                    group["name"],
                    difficulty_level=difficulty_level,
                    is_safety_sensitive=is_safety_sensitive,
                    is_security_sensitive=is_security_sensitive,
                )
                priority = task_taxonomy.classify_task_priority(title, group["name"])
                complexity = task_taxonomy.TASK_TYPES[type_key].legacy_complexity
                required_skills = task_taxonomy.extract_required_skills(title)

            raw.append({
                "phase_index": group["phase_index"],
                "phase_name": group["name"],
                "position": position,
                "title": title,
                "type_key": type_key,
                "stage": task_taxonomy.task_stage(type_key),
                "complexity": complexity,
                "hours": hours,
                "days": days,
                "priority": priority,
                "mandatory": priority in _MANDATORY_PRIORITIES,
                "requiredSkills": required_skills,
                "has_validated_metadata": validated is not None,
                "validated_dependency_titles": (
                    list(validated.dependency_titles) if validated is not None else None
                ),
            })

    raw.sort(key=lambda task: (task["phase_index"], task["stage"], task["position"]))
    return raw


# ─────────────────────────────────────────────────────────────────────────
# Step 2: capacity-based deferral (coarse, hour-sum level)
# ─────────────────────────────────────────────────────────────────────────


def defer_overflow(
    tasks: list[dict[str, Any]],
    capacity_hours: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Defers optional-then-medium priority tasks (largest hours first) until
    the remaining total fits capacity_hours, or no deferrable task remains.
    Critical/high tasks are never deferred here (they may still cause an
    honest over_capacity result later, in schedule_tasks). Returns
    (surviving_tasks, deferred_task_records).
    """
    total_hours = sum(task["hours"] for task in tasks)
    overload = total_hours - capacity_hours

    if overload <= 0:
        return tasks, []

    deferral_order = sorted(
        (task for task in tasks if task["priority"] in _DEFERRABLE_PRIORITIES),
        key=lambda task: (0 if task["priority"] == "optional" else 1, -task["hours"]),
    )

    deferred_ids = set()
    remaining_overload = overload

    for task in deferral_order:
        if remaining_overload <= 0:
            break

        deferred_ids.add(id(task))
        remaining_overload -= task["hours"]

    deferred_records = [
        {
            "title": task["title"],
            "description": "",
            "estimatedHours": task["hours"],
            "reasonDeferred": (
                f"Deferred ({task['priority']} priority) to keep the plan "
                f"within the team's available capacity ({capacity_hours}h)."
            ),
            "originalPhase": task["phase_name"],
            "priority": task["priority"],
        }
        for task in tasks
        if id(task) in deferred_ids
    ]

    surviving = [task for task in tasks if id(task) not in deferred_ids]
    return surviving, deferred_records


# ─────────────────────────────────────────────────────────────────────────
# Step 3: dependency DAG (stage-tiered, intra-tier parallelism)
# ─────────────────────────────────────────────────────────────────────────


def assign_dependencies(tasks: list[dict[str, Any]]) -> None:
    """
    Mutates each task in place, adding 'dependency_indices': list[int] --
    indices into `tasks`, guaranteed strictly smaller than the task's own
    index (so `tasks`' own order is always a valid topological order and no
    cycle can exist by construction).

    Assigns ONLY intra-phase edges -- cross-phase sequencing (phase N never
    starts before phase N-1 is fully done) is enforced separately, as a
    hard per-phase floor inside schedule_tasks(), not as a dependency edge
    here. That separation matters: a dependency edge only constrains the
    ONE task that has it, but a task with validated metadata and NO
    dependencies (e.g. an independent, `parallelizable: true` first task of
    a new phase) still must not be schedulable during the previous phase --
    a uniform phase-level floor guarantees that for every task, regardless
    of whether it happens to carry a dependency edge. It also keeps every
    week's tasks cleanly attributable to exactly one phase (see
    project_roadmap_agent._build_public_weeks's docstring for why that
    matters for the public weeks contract).

    Stage ordering (tasks sharing a (phase, stage) tier get no dependency
    between them, so genuinely independent work like "create the ASP.NET
    project" / "configure PostgreSQL" / "initialize FastAPI" can run in
    parallel; a tier depends on the immediately preceding tier in the same
    phase) is used as the FALLBACK -- it is the only source for a task with
    no validated metadata (no registry at all, or a Rewrite pass changed
    its wording).

    A task WITH validated metadata (see task_metadata.py /
    build_task_universe's `has_validated_metadata`) uses its own validated
    `dependencyIds`, resolved to sibling task titles in the SAME phase,
    instead -- e.g. "model evaluation depends on model training" or
    "end-to-end testing depends on integration" reflect the LLM's own
    (Python-validated) intent rather than a generic stage guess. A
    validated dependency that resolves to a title not found in the same
    phase, or that would point FORWARD/at itself, is silently dropped
    (never a dangling or cyclic reference) -- if that leaves the task with
    zero validated dependencies, it is treated as confirmed-independent
    (e.g. `parallelizable: true`) rather than falling back to stage
    ordering, since the registry's presence is itself an explicit signal.
    """
    by_phase: dict[int, list[int]] = {}
    for index, task in enumerate(tasks):
        by_phase.setdefault(task["phase_index"], []).append(index)

    stage_tier_fallback: dict[int, list[int]] = {}

    for phase_index in sorted(by_phase.keys()):
        indices = by_phase[phase_index]

        by_stage: dict[int, list[int]] = {}
        for index in indices:
            by_stage.setdefault(tasks[index]["stage"], []).append(index)

        preceding_tier: list[int] = []

        for stage in sorted(by_stage.keys()):
            tier_indices = by_stage[stage]
            for index in tier_indices:
                stage_tier_fallback[index] = list(preceding_tier)
            preceding_tier = tier_indices

    title_to_index_by_phase: dict[int, dict[str, int]] = {}
    for index, task in enumerate(tasks):
        title_to_index_by_phase.setdefault(task["phase_index"], {}).setdefault(task["title"], index)

    for index, task in enumerate(tasks):
        validated_titles = task.get("validated_dependency_titles")

        if task.get("has_validated_metadata") and validated_titles is not None:
            phase_titles = title_to_index_by_phase.get(task["phase_index"], {})
            task["dependency_indices"] = [
                phase_titles[dependency_title]
                for dependency_title in validated_titles
                if dependency_title in phase_titles and phase_titles[dependency_title] < index
            ]
        else:
            task["dependency_indices"] = stage_tier_fallback.get(index, [])


# ─────────────────────────────────────────────────────────────────────────
# Step 4: greedy, capacity-aware, multi-week placement
# ─────────────────────────────────────────────────────────────────────────


def schedule_tasks(
    tasks: list[dict[str, Any]],
    total_weeks: int,
    team_size: int,
    hours_per_week_per_member: int,
) -> dict[int, int]:
    """
    Mutates each task in place with startWeek/endWeek/member_hours
    (dict[member_index, hours], summing exactly to task hours) and
    overflow_hours (0 unless total_weeks genuinely could not fit it even
    after deferral). Tasks MUST already be in a valid topological order
    (see build_task_universe/assign_dependencies), and MUST already be
    grouped contiguously by phase_index (build_task_universe's own sort
    guarantees this).

    Enforces phase-level sequencing directly here (not as a dependency
    edge, see assign_dependencies' docstring): no task in phase N may start
    before phase N-1's LAST task has fully ended, with a full week of
    separation -- this is what guarantees two different phases' tasks can
    never share the same week, so every week's tasks stay attributable to
    exactly one phase when re-derived from the public `weeks` list.

    Returns the per-week planned-hours ledger (nominal_capacity - what's
    left), which is the actual distributed load -- including hours from
    tasks that span multiple weeks -- used to build weeklyCapacity.
    """
    nominal_week_capacity = team_size * hours_per_week_per_member
    week_capacity = {week: nominal_week_capacity for week in range(1, total_weeks + 1)}
    member_capacity = {
        week: [hours_per_week_per_member] * team_size for week in range(1, total_weeks + 1)
    }

    current_phase_index: int | None = None
    current_phase_floor = 1
    previous_phase_max_end_week = 0

    for task in tasks:
        if task["phase_index"] != current_phase_index:
            if current_phase_index is not None:
                previous_phase_max_end_week = phase_max_end_week
            current_phase_index = task["phase_index"]
            current_phase_floor = previous_phase_max_end_week + 1 if previous_phase_max_end_week else 1
            phase_max_end_week = 0

        dependency_end_weeks = [
            tasks[dep_index]["endWeek"]
            for dep_index in task["dependency_indices"]
            if tasks[dep_index].get("endWeek")
        ]
        start_floor = max([current_phase_floor] + dependency_end_weeks)

        remaining = task["hours"]
        week = start_floor
        first_week: int | None = None
        last_week: int | None = None
        member_hours: dict[int, int] = {}

        while remaining > 0 and week <= total_weeks:
            available_this_week = week_capacity[week]

            if available_this_week <= 0:
                week += 1
                continue

            target = min(remaining, available_this_week)
            member_order = sorted(range(team_size), key=lambda m: -member_capacity[week][m])
            taken = 0

            for member_index in member_order:
                if taken >= target:
                    break

                available_member = member_capacity[week][member_index]
                if available_member <= 0:
                    continue

                chunk = min(target - taken, available_member)
                member_capacity[week][member_index] -= chunk
                member_hours[member_index] = member_hours.get(member_index, 0) + chunk
                taken += chunk

            if taken <= 0:
                week += 1
                continue

            week_capacity[week] -= taken
            remaining -= taken

            if first_week is None:
                first_week = week
            last_week = week

            if remaining > 0:
                week += 1

        overflow = remaining

        if overflow > 0:
            # Could not fit within total_weeks even after deferral -- clamp
            # onto the last valid week so the task's own week range stays
            # structurally valid (endWeek <= total_weeks always wins, even
            # if start_floor itself already exceeds total_weeks -- a
            # dependency/phase-floor requirement that literally cannot fit
            # within the declared duration is itself part of what
            # over_capacity is reporting); the overcommitment is reported
            # via the returned ledger (that week's planned hours will
            # exceed its nominal capacity) and surfaced honestly as
            # scheduleFeasibility="over_capacity" by the caller.
            clamp_week = min(total_weeks, max(start_floor, last_week or start_floor, 1))
            member_capacity.setdefault(clamp_week, [0] * team_size)
            primary_member = (
                min(range(team_size), key=lambda m: member_capacity[clamp_week][m])
                if team_size > 0 else 0
            )
            member_capacity[clamp_week][primary_member] -= overflow
            member_hours[primary_member] = member_hours.get(primary_member, 0) + overflow
            week_capacity[clamp_week] = week_capacity.get(clamp_week, nominal_week_capacity) - overflow

            if first_week is None:
                first_week = clamp_week
            last_week = clamp_week

        task["startWeek"] = first_week if first_week is not None else start_floor
        task["endWeek"] = last_week if last_week is not None else task["startWeek"]
        task["member_hours"] = member_hours
        task["overflow_hours"] = overflow

        phase_max_end_week = max(phase_max_end_week, task["endWeek"])

    planned_hours_by_week = {
        week: nominal_week_capacity - week_capacity[week] for week in range(1, total_weeks + 1)
    }
    return planned_hours_by_week


# ─────────────────────────────────────────────────────────────────────────
# Step 5: member hour allocation percentages (exact reconciliation)
# ─────────────────────────────────────────────────────────────────────────


def member_allocations(task: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Converts a scheduled task's member_hours {index: hours} into the public
    memberAllocations shape. allocationPercentage always sums to exactly
    100 (the last member, by hours descending, takes the remainder rather
    than an independently-rounded share) and allocatedHours always sums to
    exactly the task's own hours, since member_hours already holds exact
    integer hours from the scheduler -- never independently re-derived.
    """
    entries = sorted(task["member_hours"].items(), key=lambda item: -item[1])
    total_hours = sum(hours for _member, hours in entries) or 1

    allocations: list[dict[str, Any]] = []
    percentage_used = 0

    for position, (member_index, hours) in enumerate(entries):
        is_last = position == len(entries) - 1
        percentage = (100 - percentage_used) if is_last else round(hours / total_hours * 100)
        percentage_used += percentage

        allocations.append({
            "memberId": f"Member {member_index + 1}",
            "allocationPercentage": percentage,
            "allocatedHours": hours,
        })

    return allocations


# ─────────────────────────────────────────────────────────────────────────
# Per-phase local capacity diagnostic (confirmed live defect: a phase can
# report an honest WHOLE-PROJECT utilization near 100% while one of its own
# phases is individually impossible -- see this function's docstring).
# ─────────────────────────────────────────────────────────────────────────


def diagnose_phase_capacity(
    phases_out: list[dict[str, Any]],
    team_size: int,
    hours_per_week_per_member: int,
) -> list[dict[str, Any]]:
    """
    Confirmed live defect this closes: a 12-task, 103-hour phase was
    reported as "Week 16, Duration: 1 week" for a 1-person/20h-per-week
    team -- 515% of that week's real capacity -- while the WHOLE-PROJECT
    schedule (compute_feasibility, above) still read as only marginally
    over capacity (326h planned / 320h capacity). That whole-project ledger
    is accurate for the AGGREGATE, but says nothing about whether any ONE
    phase's own planned effort could ever fit inside the specific week span
    that phase actually ended up with -- which is exactly what happened
    here: earlier phases left this phase's schedule_tasks() floor pinned to
    the project's very last week, so every task past that point clamped
    onto the same single week (see schedule_tasks' overflow-clamping
    docstring) regardless of how much total slack existed earlier in the
    timeline. No existing check (compute_feasibility is whole-project only;
    nothing here was phase-local) could have caught that.

    For each already-scheduled phase, compares its own planned task hours
    against durationWeeks * team_size * hours_per_week_per_member -- the
    capacity that phase's OWN scheduled span provides, taken in isolation.
    Purely diagnostic: never mutates phases_out, never changes scheduling.
    Returns one dict per OVERLOADED phase only (phases that fit their own
    span are omitted, not listed as "ok").

    requiredMinWeeks is how many weeks this phase's own workload would need
    if it ran ALONE, sequentially, at the team's nominal weekly capacity --
    ceil(plannedHours / (team_size * hours_per_week_per_member)) -- e.g.
    ceil(103 / 20) = 6. This is a floor, not a schedule: it assumes no
    dependency chain inside the phase forces even more weeks than that.
    """
    nominal_week_capacity = max(1, team_size * hours_per_week_per_member)
    issues: list[dict[str, Any]] = []

    for phase in phases_out:
        planned_hours = sum(task["estimatedHours"] for task in phase["tasks"])
        duration_weeks = max(1, phase["durationWeeks"])
        available_capacity_hours = duration_weeks * nominal_week_capacity

        if planned_hours <= available_capacity_hours:
            continue

        issues.append({
            "phaseId": phase["phaseId"],
            "phaseName": phase["name"],
            "durationWeeks": duration_weeks,
            "plannedHours": planned_hours,
            "availableCapacityHours": available_capacity_hours,
            "utilizationPercentage": round((planned_hours / available_capacity_hours) * 100, 1),
            "overloadHours": planned_hours - available_capacity_hours,
            "requiredMinWeeks": math.ceil(planned_hours / nominal_week_capacity),
        })

    return issues


# ─────────────────────────────────────────────────────────────────────────
# Feasibility (derived from the ACTUAL per-week ledger, not a hour-sum guess)
# ─────────────────────────────────────────────────────────────────────────


def compute_feasibility(
    planned_hours_by_week: dict[int, int],
    nominal_week_capacity: int,
    hours_per_week_per_member: int,
    team_size: int,
    deferred_count: int,
) -> dict[str, Any]:
    overload_hours = sum(
        max(0, planned - nominal_week_capacity) for planned in planned_hours_by_week.values()
    )

    if overload_hours > 0:
        feasibility = "over_capacity"
        recommended_additional_weeks = math.ceil(
            overload_hours / max(1, hours_per_week_per_member * team_size)
        )
    elif deferred_count > 0:
        feasibility = "feasible_after_scope_reduction"
        recommended_additional_weeks = 0
    else:
        feasibility = "feasible"
        recommended_additional_weeks = 0

    return {
        "scheduleFeasibility": feasibility,
        "overloadHours": overload_hours,
        "recommendedAdditionalWeeks": recommended_additional_weeks,
    }
