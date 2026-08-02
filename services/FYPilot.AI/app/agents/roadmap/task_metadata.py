"""
Internal structured task metadata: what the LLM is asked to propose per
task (InternalTaskProposal), and what Python validates/clamps it into
before it can influence scheduling (ValidatedTaskMetadata).

This is never exposed on the public ProjectRoadmapResponse -- RoadmapWeek
and RoadmapTask keep their existing public shape unchanged. It survives the
Writer -> Reviewer -> Rewrite -> deterministic-scheduling pipeline via a
request-scoped registry (see roadmap_scheduler.py's task-metadata bridge):
populated once from the Writer's validated proposals, keyed by task TITLE,
and consulted by the scheduler whenever a task's title is unchanged from
what was validated -- if a Rewrite pass changes a task's wording, that
task simply falls back to the existing deterministic text-based
classification (never a stale/mismatched metadata entry).

Python remains authoritative throughout: every number here has already
been validated and clamped against task_taxonomy's defensible per-type
ranges (see validate_and_clamp_effort) -- nothing here is a raw, unchecked
LLM value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.agents.roadmap import task_taxonomy

_VALID_COMPLEXITY_LABELS = {"simple", "small", "medium", "complex", "testing"}


class InternalTaskProposal(BaseModel):
    """
    What the LLM proposes for one task -- validated before use, never
    trusted directly. `localId`/`dependencyIds` are scoped to the SAME
    phase only (e.g. "T1", "T2"); a cross-phase or unknown reference is
    dropped during validation, not resolved.
    """

    localId: str = ""
    title: str
    description: str = ""
    taskType: str = ""
    effortMinHours: float | None = None
    effortLikelyHours: float | None = None
    effortMaxHours: float | None = None
    complexity: str = ""
    mandatory: bool | None = None
    dependencyIds: list[str] = Field(default_factory=list)
    parallelizable: bool | None = None
    requiredSkills: list[str] = Field(default_factory=list)
    acceptanceCriteria: str = ""
    scopeSize: str = ""


@dataclass(frozen=True)
class ValidatedTaskMetadata:
    """
    Python-validated/clamped metadata for one task, keyed by its title in
    the request-scoped registry. Every field here has already passed
    through task_taxonomy's validation -- see validate_task_proposal.
    """

    title: str
    taskType: str
    effortMinHours: int
    effortLikelyHours: int
    effortMaxHours: int
    complexity: str
    mandatory: bool | None
    parallelizable: bool | None
    requiredSkills: tuple[str, ...]
    acceptanceCriteria: str
    scopeSize: str
    dependency_titles: tuple[str, ...]


def validate_task_proposal(
    proposal: InternalTaskProposal,
    local_id_to_title: dict[str, str],
) -> ValidatedTaskMetadata:
    """
    Validates one LLM-proposed task against task_taxonomy's known
    categories and defensible effort ranges, and resolves its
    dependencyIds into sibling TASK TITLES using `local_id_to_title` (the
    owning phase's own localId -> title map). An id with no match in this
    phase (unknown, self-referential, or pointing at a different phase) is
    silently dropped -- never resolved to an unrelated task, never left as
    a dangling reference downstream.
    """
    title = proposal.title.strip()

    proposed_type = proposal.taskType.strip()
    type_key = proposed_type if proposed_type in task_taxonomy.TASK_TYPES else task_taxonomy.classify_task_type(title)

    effort_min, effort_likely, effort_max = task_taxonomy.validate_and_clamp_effort(
        type_key, proposal.effortMinHours, proposal.effortLikelyHours, proposal.effortMaxHours,
    )

    complexity = proposal.complexity.strip().lower()
    if complexity not in _VALID_COMPLEXITY_LABELS:
        complexity = task_taxonomy.TASK_TYPES[type_key].legacy_complexity

    dependency_titles = tuple(dict.fromkeys(
        local_id_to_title[dependency_id]
        for dependency_id in proposal.dependencyIds
        if dependency_id
        and dependency_id != proposal.localId
        and dependency_id in local_id_to_title
    ))

    required_skills = tuple(
        skill.strip() for skill in proposal.requiredSkills if skill and skill.strip()
    ) or tuple(task_taxonomy.extract_required_skills(title))

    return ValidatedTaskMetadata(
        title=title,
        taskType=type_key,
        effortMinHours=effort_min,
        effortLikelyHours=effort_likely,
        effortMaxHours=effort_max,
        complexity=complexity,
        mandatory=proposal.mandatory,
        parallelizable=proposal.parallelizable,
        requiredSkills=required_skills,
        acceptanceCriteria=proposal.acceptanceCriteria.strip()[:300],
        scopeSize=proposal.scopeSize.strip()[:120],
        dependency_titles=dependency_titles,
    )


def build_registry(phases: list[Any]) -> dict[str, ValidatedTaskMetadata]:
    """
    Validates every task proposal across all phases (each `phase` is
    anything with a `.tasks: list[InternalTaskProposal]` attribute -- see
    project_roadmap_agent._PhasePlan) and returns a flat title ->
    ValidatedTaskMetadata registry. Dependency ids are resolved per-phase
    (each phase gets its own localId -> title map), so a dependency can
    never accidentally resolve against a same-named task in a different
    phase. If two tasks end up sharing the exact same title, the later one
    wins -- roadmap_scheduler's own cross-phase deduplication already
    collapses near-duplicate titles before classification runs, so a
    collision here would itself indicate the pipeline already treats them
    as one task.
    """
    registry: dict[str, ValidatedTaskMetadata] = {}

    for phase in phases:
        local_id_to_title = {
            task.localId: task.title.strip()
            for task in phase.tasks
            if task.localId and task.title.strip()
        }

        for task in phase.tasks:
            title = task.title.strip()
            if not title:
                continue
            registry[title] = validate_task_proposal(task, local_id_to_title)

    return registry
