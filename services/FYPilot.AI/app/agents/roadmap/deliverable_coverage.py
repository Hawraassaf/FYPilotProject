"""
Deterministic deliverable -> producing-task consistency check for Roadmap
phases.

Confirmed live defect this closes: a phase named "Baseline Urgency and
Specialist Classification Models" declared BOTH a baseline urgency
classifier and a baseline specialist classifier as deliverables, but its
task list only ever trained a specialist-recommendation classifier -- no
task produced an urgency classifier at all, even though it was promised.
The same gap repeated in the phase's fine-tuned counterpart. Nothing in the
existing pipeline (project_profile.lifecycle_coverage, which only checks
whole-roadmap lifecycle CATEGORY coverage, never per-phase deliverable-to-
task correspondence) could have caught this.

Design: for each phase, every declared deliverable is reduced to its
DISTINGUISHING word STEMS (see _distinguishing_stems -- generic artifact
vocabulary like "model"/"classifier"/"baseline"/"report" is stripped,
since nearly every deliverable and task in a phase legitimately shares
those words). Crude 5-character-prefix stemming (see _stem) is used
throughout instead of exact-token matching so ordinary verb/noun form
differences ("evaluate" task vs. "evaluation" deliverable, "fine-tune"
task vs. "fine-tuned" deliverable) don't cause false negatives -- this is
deliberately NOT a real stemmer, just enough to fold the handful of
suffix variants this vocabulary actually produces.

The deliverable is "covered" only if at least one of the phase's own tasks
shares a distinguishing stem AND (when the deliverable's own generic
vocabulary implies a specific artifact CLASS -- model, database, service,
application, report, deployment) that task's own text also contains a
stem relevant to actually PRODUCING that class of artifact (see
_ARTIFACT_CLASS_STEMS). That second check is what stops a task merely
MENTIONING a topic word (e.g. a FastAPI endpoint task whose description
happens to say "returning urgency and specialist recommendation") from
being counted as the producer of a trained model deliverable it did not
actually train.

Never full NL understanding, never medical/project-specific hardcoding --
every keyword list here is generic technical-artifact vocabulary reusable
across any FYP domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.agents.roadmap import lexicon

MISSING_OUTPUT_PRODUCER = "missing_output_producer"

_STEM_LENGTH = 5


def _stem(word: str) -> str:
    return word[:_STEM_LENGTH] if len(word) > _STEM_LENGTH else word


# Generic artifact vocabulary that describes HOW something was made/its
# packaging, not WHAT makes it distinct from any other deliverable in the
# same phase -- stripped (by stem) before comparing a deliverable against
# task text, so "Baseline urgency classifier" and "Baseline specialist
# classifier" don't spuriously "cover" each other merely by both
# containing "baseline"/"classifier".
_GENERIC_DELIVERABLE_WORDS: tuple[str, ...] = (
    "model", "models", "classifier", "classifiers", "classification",
    "predictor", "baseline", "baselines", "fine", "tuned", "finetuned",
    "trained", "training", "advanced", "system", "systems", "application",
    "applications", "app", "apps", "module", "modules", "component",
    "components", "service", "services", "report", "reports", "artifact",
    "artifacts", "package", "packages", "result", "results", "output",
    "outputs", "final", "document", "documentation", "prototype",
    "pipeline", "data", "dataset", "progress", "update", "version", "level",
)
_GENERIC_DELIVERABLE_STEMS: frozenset[str] = frozenset(_stem(w) for w in _GENERIC_DELIVERABLE_WORDS)

# Deliverable vocabulary -> the artifact "class" it implies, checked in
# order (first match wins). Only used to narrow WHICH tasks may count as a
# producer (see _ARTIFACT_CLASS_STEMS) -- a deliverable that matches none
# of these still gets the plain distinguishing-stem overlap check with no
# further restriction.
_ARTIFACT_CLASS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("model", ("model", "classifier", "classification", "predictor")),
    ("database", ("database", "schema", "ddl", "migration")),
    ("deployment", ("deployment", "container", "docker image", "release build", "package for submission")),
    ("report", ("test report", "evaluation report", "evaluation results", "performance report")),
    ("service", ("api", "service", "endpoint", "microservice")),
    ("application", ("web application", "web app", "module", "screen", "page", "user interface", "dashboard")),
)

# Artifact classes where a phase realistically declares only ONE such
# deliverable, generically summarizing "the phase's output" ("a working
# booking web application", "the FastAPI triage service") rather than
# naming a specific artifact among several. For these, requiring the
# deliverable's own (often purely restated, non-task-shaped) wording to
# textually overlap a task is a false-positive trap -- a task named
# "Implement the Razor Pages registration and login screens" legitimately
# produces "a working booking web application" without ever using the
# words "working"/"booking"/"web" itself. Coverage for these classes only
# requires SOME task in the phase carrying the class's own producing stem
# (see _ARTIFACT_CLASS_STEMS), no topic-word overlap.
#
# "model" is deliberately excluded: multiple DISTINCT model deliverables
# routinely coexist in the same phase (urgency classifier + specialist
# classifier), and that is exactly the live defect this module exists to
# catch -- so model deliverables keep the stricter topic-word check.
_HOLISTIC_ARTIFACT_CLASSES: frozenset[str] = frozenset({"application", "service", "deployment", "report", "database"})

# Artifact class -> stems of task text that indicate a task could
# plausibly have PRODUCED that class of artifact (as opposed to merely
# mentioning it in passing). Matched by token-startswith, not
# lexicon.contains_any's exact word-boundary phrase match, specifically so
# ordinary verb-form variants ("fine-tune" vs "fine tuning", "evaluate" vs
# "evaluation") are recognized without depending on task_taxonomy's own
# (effort-estimation-focused, not deliverable-coverage-focused) keyword
# phrasing.
_ARTIFACT_CLASS_STEMS: dict[str, tuple[str, ...]] = {
    "model": ("train", "tun", "baselin", "classif", "predic", "evalua", "model"),
    "database": ("schema", "databa", "migrat", "table", "entit"),
    "deployment": ("deploy", "contai", "docker", "packag", "releas", "publis"),
    "report": ("evalua", "test", "report", "analy"),
    "service": ("api", "endpoi", "servic", "integr"),
    "application": ("implem", "develo", "build", "screen", "page", "ui", "interf", "control"),
}


@dataclass(frozen=True)
class DeliverableCoverageIssue:
    kind: str
    phase_name: str
    deliverable: str
    missing_topic_words: tuple[str, ...]
    artifact_class: str | None = None


def _distinguishing_stems(text: str) -> set[str]:
    words = lexicon.token_set(text)
    return {_stem(w) for w in words if len(w) >= 3 and _stem(w) not in _GENERIC_DELIVERABLE_STEMS}


def _artifact_class(deliverable: str) -> str | None:
    for artifact_class, keywords in _ARTIFACT_CLASS_KEYWORDS:
        if lexicon.contains_any(deliverable, keywords):
            return artifact_class
    return None


def _task_has_producing_stem(task_text: str, artifact_class: str | None) -> bool:
    if artifact_class is None:
        return True
    stems = _ARTIFACT_CLASS_STEMS.get(artifact_class)
    if not stems:
        return True
    tokens = lexicon.token_set(task_text)
    return any(token.startswith(stem) for token in tokens for stem in stems)


def diagnose_roadmap_deliverable_coverage(phases: Iterable[object]) -> list[DeliverableCoverageIssue]:
    """
    Returns every (phase, deliverable) pair where the deliverable's own
    distinguishing vocabulary appears in NONE of that phase's tasks (or
    appears only in tasks whose text carries no evidence of actually
    producing that class of artifact). A deliverable with no distinguishing
    words at all (e.g. just "Trained model") is skipped -- nothing specific
    enough is left to verify, and flagging it would be a false positive,
    not a real finding.
    """
    issues: list[DeliverableCoverageIssue] = []

    for phase in phases:
        phase_name = getattr(phase, "name", "")
        tasks = list(getattr(phase, "tasks", []) or [])
        task_infos = [
            (
                _distinguishing_stems(f"{getattr(task, 'title', '')} {getattr(task, 'description', '')}"),
                f"{getattr(task, 'title', '')} {getattr(task, 'description', '')}",
            )
            for task in tasks
        ]

        for deliverable in getattr(phase, "deliverables", []) or []:
            artifact_class = _artifact_class(deliverable)

            if artifact_class in _HOLISTIC_ARTIFACT_CLASSES:
                covered = any(
                    _task_has_producing_stem(task_text, artifact_class) for _task_stems, task_text in task_infos
                )
                topic_stems: set[str] = set()
            else:
                topic_stems = _distinguishing_stems(deliverable)
                if not topic_stems:
                    continue
                covered = any(
                    (topic_stems & task_stems) and _task_has_producing_stem(task_text, artifact_class)
                    for task_stems, task_text in task_infos
                )

            if not covered:
                issues.append(DeliverableCoverageIssue(
                    kind=MISSING_OUTPUT_PRODUCER,
                    phase_name=phase_name,
                    deliverable=deliverable,
                    missing_topic_words=tuple(sorted(topic_stems)),
                    artifact_class=artifact_class,
                ))

    return issues
