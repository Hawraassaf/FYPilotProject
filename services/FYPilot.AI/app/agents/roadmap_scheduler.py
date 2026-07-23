"""
Deterministic Project Roadmap scheduler.

Pure functions only -- no LLM calls, and the only "randomness" is a stable
hash of task text, so the exact same input always produces the exact same
output. This module is the single source of truth for every scheduling
number in a roadmap (phase durations, task hours, team assignment, workload
totals, overload resolution, weekly capacity), used from two call sites
that must stay behaviorally identical:

- RoadmapCandidateSchema (app/review/registry.py), applied to every
  candidate that passes through ReviewPipeline -- the Writer's first
  candidate AND every Rewrite pass -- so an LLM rewrite can never
  independently change a locked scheduling value without also changing the
  task text that deterministically drives it.
- ProjectRoadmapAgent.build_safe_fallback(), which bypasses ReviewPipeline
  entirely (the router calls it directly when nothing usable came back from
  the pipeline), so the safe fallback roadmap needs the exact same
  treatment applied directly.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

# ─────────────────────────────────────────────────────────────────────────
# Bounds and vocabulary
# ─────────────────────────────────────────────────────────────────────────

MIN_TOTAL_WEEKS = 4
MAX_TOTAL_WEEKS = 30

# estimatedWorkingDays = ceil(estimatedHours / EFFECTIVE_HOURS_PER_DAY) --
# one documented deterministic assumption, applied everywhere hours are
# estimated so the two figures can never contradict each other (e.g. 24h
# but "1 day").
EFFECTIVE_HOURS_PER_DAY = 6

# Task-hour percentage split when a complex task lists two collaborating
# members. Primary + secondary always sum to exactly 100, and the
# secondary's allocatedHours is the REMAINDER of the primary's rounded
# share (not independently rounded), so allocatedHours always sum back to
# the task's own estimatedHours exactly -- this is what makes
# sum(workloadByMember.assignedHours) == totalPlannedHours hold exactly,
# not just approximately.
PRIMARY_ALLOCATION_PERCENTAGE = 70
SECONDARY_ALLOCATION_PERCENTAGE = 30

_PADDING_PREFIXES = ("review and refine:", "test and verify:")

_STOPWORDS = {
    "a", "an", "the", "for", "and", "of", "to", "in", "on", "with", "this",
    "that", "system", "application", "app", "module", "feature",
    "functionality", "using", "via", "into", "from", "as",
}

_ACTION_VERBS = {
    "implement", "implementing", "develop", "developing", "create",
    "creating", "build", "building", "design", "designing", "add", "adding",
    "perform", "performing", "complete", "completing", "work", "test",
    "testing", "review", "refine", "verify", "establish", "set", "setup",
    "prepare", "preparing", "write", "writing", "construct", "constructing",
    "define", "defining",
}

# Checked in order -- first keyword match wins. Longer/more specific
# keyword sets are listed first so e.g. "OAuth integration" resolves to the
# "complex" bucket rather than the more generic "integration" one.
_COMPLEXITY_BUCKETS: list[tuple[tuple[str, ...], int, int, str]] = [
    (
        (
            "oauth", "provider fallback", "groq", "gemini", "ollama",
            "ai integration", "machine learning", "ml model",
            "recommendation engine", "nlp", "payment integration",
            "calendar api", "freebusy", "external api integration",
        ),
        24, 60, "complex",
    ),
    (
        ("integration", "migrate", "migration", "pipeline", "fallback chain", "provider chain"),
        20, 48, "complex",
    ),
    (
        ("implement", "build the", "develop the", "core workflow", "main feature", "primary workflow"),
        16, 32, "medium",
    ),
    (
        ("test", "testing", "stabiliz", "bug fix", "debug", "qa "),
        8, 24, "testing",
    ),
    (
        ("ui", "page", "form", "screen", "layout", "view", "endpoint", "api route", "crud"),
        6, 16, "small",
    ),
    (
        ("config", "setup", "install", "initialize", "environment", "connect", "wire up", "register"),
        2, 6, "simple",
    ),
]
_DEFAULT_HOURS = (8, 20)
_DEFAULT_COMPLEXITY = "medium"

_SKILL_VOCABULARY = (
    "PostgreSQL", "ASP.NET Core", "Razor Pages", "FastAPI", "Python",
    "JavaScript", "Bootstrap", "Ollama", "Groq", "Gemini", "OAuth",
    "Google Calendar", "REST API", "Entity Framework", "SQL", "JSON",
    "Machine Learning", "NLP",
)

# Priority classification -- checked in this order: a task/phase explicitly
# marked mandatory/required/essential is NEVER classified as optional
# (checked first, gates the optional bucket); critical is checked before
# optional so e.g. "core backend" can't be shadowed by an unrelated
# optional-sounding word; medium and optional are the softer buckets.
_MANDATORY_OVERRIDE_KEYWORDS = ("mandatory", "must have", "essential", "required")

_CRITICAL_KEYWORDS = (
    "database schema", "database design", "core backend", "backend logic",
    "core workflow", "core feature", "authentication", "login system",
    "authorization", "core ai", "ai integration", "central business logic",
    "required api", "core api", "primary business logic", "security",
)

_HIGH_KEYWORDS = (
    "essential ui", "integration test", "deployment configuration",
    "deployment prep", "required documentation", "core ui",
    "deployment",
)

_MEDIUM_KEYWORDS = (
    "usability improvement", "additional report", "secondary validation",
    "minor validation", "extra report", "usability",
)

_OPTIONAL_KEYWORDS = (
    "extra dashboard", "additional dashboard", "advanced visual polish",
    "visual polish", "non-required analytic", "non-essential analytic",
    "stretch integration", "stretch feature", "enhancement",
    "advanced analytics", "nice to have", "bonus feature", "optional",
    "polish",
)


# ─────────────────────────────────────────────────────────────────────────
# Total duration / phase duration
# ─────────────────────────────────────────────────────────────────────────


def normalize_total_weeks(requested: Any) -> int:
    """
    Reuse the caller's real requested duration; only guard against
    non-numeric or pathological values. Unlike the old implementation, this
    never collapses a legitimate long or short project into a fixed [6, 12]
    band.
    """
    try:
        value = int(requested)
    except Exception:
        value = 10

    return max(MIN_TOTAL_WEEKS, min(value, MAX_TOTAL_WEEKS))


def allocate_phase_durations(weights: list[float], total_weeks: int) -> list[int]:
    """
    Scale proposed phase weights into positive integer week-counts summing
    EXACTLY to total_weeks (largest-remainder method), preserving relative
    weight so harder/bigger phases still get more weeks.

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
# Task text analysis: complexity, hours, priority, duplicates
# ─────────────────────────────────────────────────────────────────────────


def _stable_fraction(text: str) -> float:
    """Deterministic pseudo-random value in [0, 1) derived from text -- same
    text always yields the same fraction, unlike Python's randomized
    string hash()."""
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def classify_task_complexity(title: str) -> str:
    lowered = title.lower()

    for keywords, _lo, _hi, label in _COMPLEXITY_BUCKETS:
        if any(keyword in lowered for keyword in keywords):
            return label

    return _DEFAULT_COMPLEXITY


def estimate_task_hours(title: str) -> tuple[int, int]:
    """Deterministic (hours, working_days) estimate from task text alone --
    same title always yields the same numbers, so re-running this after a
    Rewrite that left the wording unchanged reproduces identical hours.
    working_days is always ceil(hours / EFFECTIVE_HOURS_PER_DAY) -- the one
    documented assumption -- so the two figures can never contradict each
    other."""
    lowered = title.lower()
    lo, hi = _DEFAULT_HOURS

    for keywords, bucket_lo, bucket_hi, _label in _COMPLEXITY_BUCKETS:
        if any(keyword in lowered for keyword in keywords):
            lo, hi = bucket_lo, bucket_hi
            break

    hours = lo + round(_stable_fraction(title) * (hi - lo))
    working_days = compute_working_days(hours)

    return hours, working_days


def compute_working_days(hours: int) -> int:
    """The single deterministic estimatedHours -> estimatedWorkingDays rule,
    reused everywhere so a Rewrite (or overload resolution reducing a
    task's hours) can never leave the two figures inconsistent."""
    return max(1, math.ceil(hours / EFFECTIVE_HOURS_PER_DAY))


def classify_task_priority(title: str, phase_name: str = "") -> str:
    """
    Deterministic critical/high/medium/optional classification from task
    title + phase context (never a single keyword in isolation). A task or
    phase explicitly described as mandatory/required/essential is NEVER
    classified as optional, even if it also matches an optional-sounding
    keyword -- checked before the optional bucket, per the requirement that
    explicitly-mandatory work must never be auto-deferred.
    """
    combined = f"{title} {phase_name}".lower()
    is_explicitly_mandatory = any(keyword in combined for keyword in _MANDATORY_OVERRIDE_KEYWORDS)

    if any(keyword in combined for keyword in _CRITICAL_KEYWORDS):
        return "critical"

    if not is_explicitly_mandatory and any(keyword in combined for keyword in _OPTIONAL_KEYWORDS):
        return "optional"

    if any(keyword in combined for keyword in _HIGH_KEYWORDS):
        return "high"

    if any(keyword in combined for keyword in _MEDIUM_KEYWORDS):
        return "medium"

    return "high" if is_explicitly_mandatory else "medium"


def extract_required_skills(title: str) -> list[str]:
    lowered = title.lower()
    return [skill for skill in _SKILL_VOCABULARY if skill.lower() in lowered]


def is_padding_task(title: str) -> bool:
    lowered = title.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _PADDING_PREFIXES)


def _core_tokens(title: str) -> set[str]:
    lowered = re.sub(r"[^a-z0-9\s]", " ", title.lower())
    tokens = [token for token in lowered.split() if token]

    core = []
    for token in tokens:
        stemmed = token[:-1] if token.endswith("s") and len(token) > 3 else token
        if stemmed in _STOPWORDS or stemmed in _ACTION_VERBS:
            continue
        core.append(stemmed)

    return set(core)


def are_duplicate_tasks(a: str, b: str) -> bool:
    """
    True if two task descriptions describe the same underlying deliverable.
    Strips action verbs ("implement"/"develop"/"create"/...) and filler
    words, then compares the remaining core tokens: exact match, subset
    (one is fully contained in the other), or >=50% Jaccard overlap.
    """
    core_a, core_b = _core_tokens(a), _core_tokens(b)

    if not core_a or not core_b:
        return False

    if core_a == core_b:
        return True

    smaller, larger = (core_a, core_b) if len(core_a) <= len(core_b) else (core_b, core_a)

    if smaller.issubset(larger):
        return True

    union = core_a | core_b
    intersection = core_a & core_b

    return len(intersection) / len(union) >= 0.5


def deduplicate_tasks(titles: list[str]) -> list[str]:
    """Keep first-occurrence order; when a later task duplicates an earlier
    one, keep whichever phrasing is more specific (longer)."""
    kept: list[str] = []

    for title in titles:
        clean = title.strip()

        if not clean:
            continue

        merged = False

        for index, existing in enumerate(kept):
            if are_duplicate_tasks(clean, existing):
                if len(clean) > len(existing):
                    kept[index] = clean
                merged = True
                break

        if not merged:
            kept.append(clean)

    return kept


# ─────────────────────────────────────────────────────────────────────────
# Member hour allocation (exact reconciliation -- no double counting)
# ─────────────────────────────────────────────────────────────────────────


def allocate_task_hours(
    hours: int,
    complexity: str,
    member_hours: list[int],
    team_size: int,
) -> list[dict]:
    """
    Split one task's estimatedHours across 1 or 2 members. This is the ONLY
    place task hours are assigned to a member -- a task's effort is never
    counted twice: for two members, primary gets
    PRIMARY_ALLOCATION_PERCENTAGE% and secondary gets the REMAINDER (not an
    independently-rounded 30%), so the two allocatedHours always sum back
    to exactly `hours`, which is what makes
    sum(workloadByMember.assignedHours) == totalPlannedHours hold exactly
    rather than approximately.
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
# Phase grouping + full schedule construction
# ─────────────────────────────────────────────────────────────────────────


def _group_weeks_into_phases(weeks: list[dict]) -> list[dict]:
    """Group consecutive weeks sharing the same phaseTitle into one phase
    group. Always produces at most len(weeks) groups."""
    groups: list[dict] = []

    for week in weeks:
        title = str(week.get("phaseTitle") or "Phase").strip() or "Phase"
        week_number = int(week.get("weekNumber") or (len(groups) + 1))

        if groups and groups[-1]["name"] == title:
            groups[-1]["endWeek"] = week_number
            groups[-1]["raw_tasks"].extend(week.get("tasks") or [])
            groups[-1]["deliverables"].extend(week.get("deliverables") or [])
        else:
            groups.append({
                "name": title,
                "startWeek": week_number,
                "endWeek": week_number,
                "raw_tasks": list(week.get("tasks") or []),
                "deliverables": list(week.get("deliverables") or []),
                "objective": str(week.get("mainGoal") or ""),
            })

    return groups


def _distribute_weeks(task_count: int, start_week: int, end_week: int) -> list[int]:
    """Assign each of `task_count` tasks (in order) a single specific week
    within [start_week, end_week], spread as evenly as possible -- gives
    weekly-capacity checking a real per-task week instead of every task
    nominally spanning the whole phase."""
    span = max(1, end_week - start_week + 1)

    if task_count == 0:
        return []

    return [
        start_week + min(index * span // task_count, span - 1)
        for index in range(task_count)
    ]


def build_phases_and_summary(
    weeks: list[dict],
    total_weeks: int,
    team_size: int,
    hours_per_week_per_member: int,
) -> tuple[list[dict], dict, list[dict]]:
    """
    Build the structured `phases` list, `planningSummary` dict, and
    `deferredTasks` list entirely from `weeks` (+
    totalWeeks/teamSize/hoursPerWeekPerMember). Deterministic and
    idempotent: calling this again on the same weeks always reproduces the
    same phases/tasks/hours/assignments/workload/deferrals, regardless of
    what a Rewrite pass may have carried in those fields on the candidate.

    Returns (phases, planning_summary, deferred_tasks).
    """
    team_size = max(1, min(int(team_size or 1), 12))
    hours_per_week_per_member = max(1, int(hours_per_week_per_member or 10))
    total_weeks = int(total_weeks or len(weeks) or 1)

    groups = _group_weeks_into_phases(weeks)

    for group in groups:
        genuine = [
            task for task in group["raw_tasks"]
            if task and not is_padding_task(task)
        ]
        group["task_titles"] = deduplicate_tasks(genuine)

    seen_titles: list[str] = []
    for group in groups:
        kept_titles = []
        for title in group["task_titles"]:
            if any(are_duplicate_tasks(title, seen) for seen in seen_titles):
                continue
            seen_titles.append(title)
            kept_titles.append(title)
        group["task_titles"] = kept_titles

    member_hours = [0] * team_size
    member_task_counts = [0] * team_size
    all_tasks: list[dict] = []
    phases_out: list[dict] = []
    previous_phase_last_task_id: str | None = None

    for phase_index, group in enumerate(groups, start=1):
        phase_id = f"P{phase_index}"
        phase_tasks_out: list[dict] = []
        previous_task_id_in_phase: str | None = None

        task_weeks = _distribute_weeks(
            len(group["task_titles"]), group["startWeek"], group["endWeek"]
        )

        for task_index, (title, task_week) in enumerate(
            zip(group["task_titles"], task_weeks), start=1
        ):
            task_id = f"{phase_id}-T{task_index}"
            hours, days = estimate_task_hours(title)
            complexity = classify_task_complexity(title)
            priority = classify_task_priority(title, group["name"])

            allocations = allocate_task_hours(hours, complexity, member_hours, team_size)
            for allocation in allocations:
                index = int(allocation["memberId"].replace("Member", "").strip()) - 1
                member_hours[index] += allocation["allocatedHours"]
                member_task_counts[index] += 1

            dependencies: list[str] = []
            if previous_task_id_in_phase:
                dependencies.append(previous_task_id_in_phase)
            elif previous_phase_last_task_id:
                dependencies.append(previous_phase_last_task_id)

            task_dict = {
                "taskId": task_id,
                "title": title,
                "estimatedHours": hours,
                "estimatedWorkingDays": days,
                "startWeek": task_week,
                "endWeek": task_week,
                "dependencies": dependencies,
                "requiredSkills": extract_required_skills(title),
                "assignedMembers": [allocation["memberId"] for allocation in allocations],
                "memberAllocations": allocations,
                "complexity": complexity,
                "priority": priority,
            }
            phase_tasks_out.append(task_dict)
            all_tasks.append(task_dict)
            previous_task_id_in_phase = task_id

        if phase_tasks_out:
            previous_phase_last_task_id = phase_tasks_out[-1]["taskId"]

        deduped_deliverables = list(dict.fromkeys(
            d.strip() for d in group["deliverables"] if d and d.strip()
        ))[:4]

        phases_out.append({
            "phaseId": phase_id,
            "name": group["name"],
            "objective": group["objective"],
            "durationWeeks": group["endWeek"] - group["startWeek"] + 1,
            "startWeek": group["startWeek"],
            "endWeek": group["endWeek"],
            "deliverables": deduped_deliverables,
            "dependencies": [f"P{phase_index - 1}"] if phase_index > 1 else [],
            "tasks": phase_tasks_out,
        })

    phases_out, deferred_tasks, capacity_info = resolve_overload(
        phases_out, total_weeks, team_size, hours_per_week_per_member,
    )

    workload_by_member, weekly_capacity = _summarize_workload_and_weeks(
        phases_out, total_weeks, team_size, hours_per_week_per_member,
    )

    total_planned_hours = capacity_info["adjustedPlannedHours"]
    capacity_hours = capacity_info["capacityHours"]
    utilization = (
        round((total_planned_hours / capacity_hours) * 100, 1)
        if capacity_hours > 0 else 0.0
    )

    warnings: list[str] = []
    if capacity_info["scheduleFeasibility"] == "over_capacity":
        warnings.append(
            f"Essential project scope exceeds available capacity by "
            f"{capacity_info['overloadHours']}h even after deferring "
            f"optional work -- add approximately "
            f"{capacity_info['recommendedAdditionalWeeks']} more week(s) or "
            "reduce the mandatory scope."
        )
    elif capacity_info["scheduleFeasibility"] == "feasible_after_scope_reduction":
        warnings.append(
            f"{len(deferred_tasks)} optional task(s) totalling "
            f"{capacity_info['deferredHours']}h were deferred to future "
            "enhancements to fit the available capacity."
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
        "totalPlannedHours": total_planned_hours,
        "utilizationPercentage": utilization,
        "numberOfPhases": len(phases_out),
        "numberOfTasks": sum(len(phase["tasks"]) for phase in phases_out),
        "workloadByMember": workload_by_member,
        "warnings": warnings,
        "schedulingAssumptions": [
            "Each task is scheduled within its phase's week span, on one "
            "specific week -- task-level granularity is weekly, not daily.",
            f"Working days assume {EFFECTIVE_HOURS_PER_DAY} productive "
            "hours per day (estimatedWorkingDays = ceil(estimatedHours / "
            f"{EFFECTIVE_HOURS_PER_DAY})).",
            "A complex task may list a second collaborating member, "
            f"receiving {PRIMARY_ALLOCATION_PERCENTAGE}%/"
            f"{SECONDARY_ALLOCATION_PERCENTAGE}% of that task's hours "
            "(not additional hours) -- every other task has one primary "
            "owner with 100%.",
            "Task dependencies are sequential within a phase, and each "
            "phase's first task depends on the previous phase's last task.",
            "Optional/medium-priority tasks may be deferred to future "
            "enhancements when planned hours exceed team capacity; "
            "critical/high-priority tasks are never auto-deferred.",
        ],
        "scheduleFeasibility": capacity_info["scheduleFeasibility"],
        "originalPlannedHours": capacity_info["originalPlannedHours"],
        "adjustedPlannedHours": capacity_info["adjustedPlannedHours"],
        "capacityHours": capacity_hours,
        "deferredHours": capacity_info["deferredHours"],
        "overloadHours": capacity_info["overloadHours"],
        "recommendedAdditionalWeeks": capacity_info["recommendedAdditionalWeeks"],
        "weeklyCapacity": weekly_capacity,
    }

    return phases_out, planning_summary, deferred_tasks


# ─────────────────────────────────────────────────────────────────────────
# Overload resolution (deterministic, no LLM call)
# ─────────────────────────────────────────────────────────────────────────


def resolve_overload(
    phases: list[dict],
    total_weeks: int,
    team_size: int,
    hours_per_week_per_member: int,
) -> tuple[list[dict], list[dict], dict]:
    """
    Deterministic overload-resolution pass -- never just displays a
    warning. Step 1: compute overload. Step 2/3: defer optional, then
    medium-priority tasks (largest hours first, so fewer tasks need
    deferring) into a separate list, never deleting them and never
    touching critical/high priority work. Step 4: rebuild phase/task
    integrity (IDs, dependencies, allocations) from what remains. Step 6:
    if essential work alone still exceeds capacity, honestly mark
    scheduleFeasibility="over_capacity" with a concrete
    recommendedAdditionalWeeks instead of pretending it fits.
    """
    capacity_hours = total_weeks * hours_per_week_per_member * team_size

    all_tasks = [(phase, task) for phase in phases for task in phase["tasks"]]
    original_planned_hours = sum(task["estimatedHours"] for _phase, task in all_tasks)

    overload_hours = original_planned_hours - capacity_hours
    deferred_tasks: list[dict] = []

    if overload_hours > 0:
        deferral_order = sorted(
            (item for item in all_tasks if item[1]["priority"] in ("optional", "medium")),
            key=lambda item: (
                0 if item[1]["priority"] == "optional" else 1,
                -item[1]["estimatedHours"],
            ),
        )

        remaining_overload = overload_hours
        for phase, task in deferral_order:
            if remaining_overload <= 0:
                break

            phase["tasks"].remove(task)
            deferred_tasks.append({
                "title": task["title"],
                "description": "",
                "estimatedHours": task["estimatedHours"],
                "reasonDeferred": (
                    f"Deferred ({task['priority']} priority) to keep the "
                    f"plan within the team's available capacity "
                    f"({capacity_hours}h)."
                ),
                "originalPhase": phase["name"],
                "priority": task["priority"],
            })
            remaining_overload -= task["estimatedHours"]

    phases = _rebuild_phase_integrity(phases, team_size, total_weeks)

    adjusted_planned_hours = sum(
        task["estimatedHours"] for phase in phases for task in phase["tasks"]
    )
    deferred_hours = sum(task["estimatedHours"] for task in deferred_tasks)
    remaining_overload_hours = max(0, adjusted_planned_hours - capacity_hours)

    if remaining_overload_hours > 0:
        schedule_feasibility = "over_capacity"
        recommended_additional_weeks = math.ceil(
            remaining_overload_hours / max(1, hours_per_week_per_member * team_size)
        )
    elif deferred_tasks:
        schedule_feasibility = "feasible_after_scope_reduction"
        recommended_additional_weeks = 0
    else:
        schedule_feasibility = "feasible"
        recommended_additional_weeks = 0

    capacity_info = {
        "scheduleFeasibility": schedule_feasibility,
        "originalPlannedHours": original_planned_hours,
        "adjustedPlannedHours": adjusted_planned_hours,
        "capacityHours": capacity_hours,
        "deferredHours": deferred_hours,
        "overloadHours": remaining_overload_hours,
        "recommendedAdditionalWeeks": recommended_additional_weeks,
    }

    return phases, deferred_tasks, capacity_info


def _rebuild_phase_integrity(
    phases: list[dict],
    team_size: int,
    total_weeks: int,
) -> list[dict]:
    """
    After deferral, rebuild IDs/dependencies/allocations/week spans from
    scratch over whatever tasks remain -- phases left with zero tasks are
    dropped entirely (never left as an empty, invalid phase). Dropping a
    phase would otherwise leave its week span unaccounted for, so surviving
    phases' durations are RE-ALLOCATED (same weighted largest-remainder
    method as the initial build, weighted by each survivor's own original
    duration) to still span the full total_weeks -- "total project duration
    must remain valid" after deferral, not just "phase count is smaller".
    phaseIds/taskIds are renumbered contiguously and every dependency is
    rebuilt fresh, so no task can depend on one that was deferred and no
    dangling/circular reference can survive a deferral pass. Member hours
    are also fully reallocated over the surviving work, since the
    pre-deferral balance no longer reflects the real remaining effort.
    """
    surviving = [phase for phase in phases if phase["tasks"]]

    if not surviving:
        return []

    weights = [max(1, phase["durationWeeks"]) for phase in surviving]
    durations = allocate_phase_durations(weights, total_weeks)

    member_hours = [0] * team_size
    previous_phase_last_task_id: str | None = None
    rebuilt: list[dict] = []
    week_cursor = 1

    for phase_index, (phase, duration) in enumerate(zip(surviving, durations), start=1):
        phase_id = f"P{phase_index}"
        start_week = week_cursor
        end_week = week_cursor + duration - 1
        week_cursor = end_week + 1

        task_weeks = _distribute_weeks(len(phase["tasks"]), start_week, end_week)

        previous_task_id_in_phase: str | None = None
        rebuilt_tasks = []

        for task_index, (task, task_week) in enumerate(
            zip(phase["tasks"], task_weeks), start=1
        ):
            task_id = f"{phase_id}-T{task_index}"

            dependencies: list[str] = []
            if previous_task_id_in_phase:
                dependencies.append(previous_task_id_in_phase)
            elif previous_phase_last_task_id:
                dependencies.append(previous_phase_last_task_id)

            allocations = allocate_task_hours(
                task["estimatedHours"], task["complexity"], member_hours, team_size,
            )
            for allocation in allocations:
                index = int(allocation["memberId"].replace("Member", "").strip()) - 1
                member_hours[index] += allocation["allocatedHours"]

            new_task = dict(task)
            new_task["taskId"] = task_id
            new_task["dependencies"] = dependencies
            new_task["memberAllocations"] = allocations
            new_task["assignedMembers"] = [a["memberId"] for a in allocations]
            new_task["startWeek"] = task_week
            new_task["endWeek"] = task_week

            rebuilt_tasks.append(new_task)
            previous_task_id_in_phase = task_id

        if rebuilt_tasks:
            previous_phase_last_task_id = rebuilt_tasks[-1]["taskId"]

        new_phase = dict(phase)
        new_phase["phaseId"] = phase_id
        new_phase["dependencies"] = [f"P{phase_index - 1}"] if phase_index > 1 else []
        new_phase["durationWeeks"] = duration
        new_phase["startWeek"] = start_week
        new_phase["endWeek"] = end_week
        new_phase["tasks"] = rebuilt_tasks
        rebuilt.append(new_phase)

    return rebuilt


# ─────────────────────────────────────────────────────────────────────────
# Weekly capacity
# ─────────────────────────────────────────────────────────────────────────


def _summarize_workload_and_weeks(
    phases: list[dict],
    total_weeks: int,
    team_size: int,
    hours_per_week_per_member: int,
) -> tuple[list[dict], list[dict]]:
    all_tasks = [task for phase in phases for task in phase["tasks"]]

    member_hours = [0] * team_size
    member_task_counts = [0] * team_size
    for task in all_tasks:
        for allocation in task["memberAllocations"]:
            index = int(allocation["memberId"].replace("Member", "").strip()) - 1
            if 0 <= index < team_size:
                member_hours[index] += allocation["allocatedHours"]
                member_task_counts[index] += 1

    member_capacity_hours = total_weeks * hours_per_week_per_member
    workload_by_member = []
    for i in range(team_size):
        member_utilization = (
            round((member_hours[i] / member_capacity_hours) * 100, 1)
            if member_capacity_hours > 0 else 0.0
        )
        workload_by_member.append({
            "member": f"Member {i + 1}",
            "assignedTaskCount": member_task_counts[i],
            "assignedHours": member_hours[i],
            "utilizationPercentage": member_utilization,
        })

    weekly_hours = [0] * (total_weeks + 1)  # 1-indexed, index 0 unused
    for task in all_tasks:
        week = max(1, min(task["startWeek"], total_weeks))
        weekly_hours[week] += task["estimatedHours"]

    team_weekly_capacity = hours_per_week_per_member * team_size
    weekly_capacity = [
        {
            "week": week,
            "plannedHours": weekly_hours[week],
            "capacityHours": team_weekly_capacity,
            "utilizationPercentage": (
                round((weekly_hours[week] / team_weekly_capacity) * 100, 1)
                if team_weekly_capacity > 0 else 0.0
            ),
        }
        for week in range(1, total_weeks + 1)
    ]

    return workload_by_member, weekly_capacity


def has_dependency_cycle(tasks: list[dict]) -> bool:
    """
    DFS-based cycle detection over the taskId -> dependencies graph.
    build_phases_and_summary only ever creates forward (earlier -> later)
    dependencies, so it cannot produce a cycle by construction -- this is a
    defensive check exercised directly with adversarial input in tests, and
    used as a real guard in RoadmapCandidateSchema in case a future change
    to the scheduler ever breaks that guarantee.
    """
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
