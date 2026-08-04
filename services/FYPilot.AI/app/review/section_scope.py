"""
Deterministic section scoping for semantic rewrites.

The LLM still returns a complete object because every agent's public schema is
unchanged. For SE Documentation, however, only the reviewer-confirmed section
and the smallest known set of dependent sections are allowed to change.
Everything else is restored from the pre-rewrite candidate before validation.

This prevents a targeted database/UI correction from silently rewriting good
requirements, use cases, architecture, or project identity.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from app.review.models import ReviewerIssue

Candidate = dict[str, Any]


# Canonical top-level SE Documentation fields. Only keys actually present in
# the candidate are used, so this stays compatible with DTO evolution.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "projecttitle": ("projectTitle",),
    "project title": ("projectTitle",),
    "projectoverview": ("projectOverview",),
    "project overview": ("projectOverview",),
    "problemstatement": ("problemStatement",),
    "problem statement": ("problemStatement",),
    "functionalrequirements": ("functionalRequirements",),
    "functional requirements": ("functionalRequirements",),
    "nonfunctionalrequirements": ("nonFunctionalRequirements",),
    "non functional requirements": ("nonFunctionalRequirements",),
    "usecases": ("useCases",),
    "use cases": ("useCases",),
    "edgecases": ("edgeCases",),
    "edge cases": ("edgeCases",),
    "systemmodules": ("systemModules",),
    "system modules": ("systemModules",),
    "modules": ("systemModules",),
    "databasedesign": ("databaseEntities", "databaseRelationships"),
    "database design": ("databaseEntities", "databaseRelationships"),
    "databaseentities": ("databaseEntities",),
    "database entities": ("databaseEntities",),
    "databaserelationships": ("databaseRelationships",),
    "database relationships": ("databaseRelationships",),
    "uiscreens": ("uiScreens",),
    "ui screens": ("uiScreens",),
    "frontend": ("uiScreens",),
    "apiintegrationpoints": ("apiIntegrationPoints",),
    "api integration points": ("apiIntegrationPoints",),
    "apis": ("apiIntegrationPoints",),
    "diagrams": ("diagramSpecifications", "diagramDescriptions", "diagrams"),
    "diagramspecifications": ("diagramSpecifications",),
    "diagram specifications": ("diagramSpecifications",),
    "architecture": ("architecture",),
    "security": ("securityAndPrivacy", "security",),
    "securityandprivacy": ("securityAndPrivacy",),
    "security and privacy": ("securityAndPrivacy",),
    "testingplan": ("testingPlan",),
    "testing plan": ("testingPlan",),
    "testing": ("testingPlan",),
    "traceabilitymatrix": ("traceabilityMatrix",),
    "traceability matrix": ("traceabilityMatrix",),
    "aitechnicalreport": ("aiTechnicalReport",),
    "ai technical report": ("aiTechnicalReport",),
    "risks": ("risksAndLimitations", "risks"),
    "risksandlimitations": ("risksAndLimitations",),
    "risks and limitations": ("risksAndLimitations",),
    "assumptions": ("assumptions",),
    "expectedoutcomes": ("expectedOutcomes",),
    "expected outcomes": ("expectedOutcomes",),
}


# These fields are always computed deterministically by the platform (see
# _SEDOC_EXTRA_RUBRIC's "Do NOT suggest changing documentationQualityScore or
# qualityAssessment" / "Do NOT critique mermaidERD, mermaidClassDiagram,
# activityDiagram, or sequenceDiagram" rules) and must never be directly
# LLM-rewritable -- not via an alias match, not via a literal top-level-key
# match (mermaidERD etc. are real candidate keys the "direct JSON path" branch
# below would otherwise match), and not via the "unmatched/global issue falls
# back to every top-level field" behavior. Removed here unconditionally,
# after every other rule has run, so no future alias/path-matching change can
# accidentally reopen this.
_NEVER_LLM_REWRITABLE_FIELDS: frozenset[str] = frozenset({
    "documentationQualityScore",
    "qualityAssessment",
    "documentationQualityAssessment",
    "mermaidERD",
    "mermaidClassDiagram",
    "activityDiagram",
    "sequenceDiagram",
})


# Conservative dependency closure. A changed requirement/entity/screen can
# invalidate references elsewhere, so those linked sections are included.
# Only fields present in the current candidate are ever added.
_DEPENDENCIES: dict[str, set[str]] = {
    "functionalRequirements": {
        "useCases",
        "edgeCases",
        "systemModules",
        "databaseEntities",
        "uiScreens",
        "apiIntegrationPoints",
        "testingPlan",
        "traceabilityMatrix",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "nonFunctionalRequirements": {
        "testingPlan",
        "traceabilityMatrix",
        "architecture",
        "securityAndPrivacy",
        "security",
    },
    "useCases": {
        "traceabilityMatrix",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "edgeCases": {"testingPlan", "traceabilityMatrix"},
    "systemModules": {
        "architecture",
        "apiIntegrationPoints",
        "traceabilityMatrix",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "databaseEntities": {
        "databaseRelationships",
        "traceabilityMatrix",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
        "architecture",
    },
    "databaseRelationships": {
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "uiScreens": {
        "traceabilityMatrix",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "apiIntegrationPoints": {
        "traceabilityMatrix",
        "architecture",
        "diagramSpecifications",
        "diagramDescriptions",
        "diagrams",
    },
    "testingPlan": {"traceabilityMatrix"},
    "aiTechnicalReport": {"architecture", "risksAndLimitations", "risks"},
}


_ROOT_PATH_RE = re.compile(r"^[\s`'\"]*([A-Za-z_][A-Za-z0-9_]*)")


def _normalise_text(value: str) -> str:
    value = re.sub(r"\[[^\]]*\]", "", value or "")
    value = value.replace("_", " ").replace("-", " ").replace(".", " ")
    return " ".join(value.lower().split())


def _candidate_key_lookup(candidate: Candidate) -> dict[str, str]:
    return {key.lower(): key for key in candidate.keys()}


def _match_existing_keys(candidate: Candidate, possible_keys: Iterable[str]) -> set[str]:
    lookup = _candidate_key_lookup(candidate)
    matched: set[str] = set()
    for key in possible_keys:
        actual = lookup.get(key.lower())
        if actual:
            matched.add(actual)
    return matched


def _roots_from_affected_field(candidate: Candidate, affected_field: str) -> set[str]:
    raw = (affected_field or "").strip()
    if not raw:
        return set()

    # Direct JSON/path-style root: databaseEntities[0].fields
    direct_match = _ROOT_PATH_RE.match(raw)
    if direct_match:
        direct = _match_existing_keys(candidate, [direct_match.group(1)])
        if direct:
            return direct

    normalised = _normalise_text(raw)
    compact = normalised.replace(" ", "")

    possible: set[str] = set()
    for alias, keys in _SECTION_ALIASES.items():
        alias_normalised = _normalise_text(alias)
        alias_compact = alias_normalised.replace(" ", "")
        if (
            normalised == alias_normalised
            or compact == alias_compact
            or alias_normalised in normalised
        ):
            possible.update(keys)

    return _match_existing_keys(candidate, possible)


def revision_scope_for(
    agent_name: str,
    candidate: Candidate,
    blocking_issues: list[ReviewerIssue],
) -> set[str]:
    """Return top-level fields the semantic rewrite may change.

    Non-SE agents retain their existing complete-object behavior. For SE
    Documentation, unknown/global affected fields intentionally fall back to
    all top-level fields rather than risking an incomplete repair.
    """
    all_fields = set(candidate.keys())
    if agent_name != "SEDocumentationAgent":
        return all_fields

    # Never rewritable regardless of which branch below is taken -- see
    # _NEVER_LLM_REWRITABLE_FIELDS's docstring.
    never_rewritable = _NEVER_LLM_REWRITABLE_FIELDS & all_fields

    direct_fields: set[str] = set()
    for issue in blocking_issues:
        direct_fields.update(_roots_from_affected_field(candidate, issue.affectedField))

    if not direct_fields:
        return all_fields - never_rewritable

    scoped = set(direct_fields)
    for field in list(direct_fields):
        scoped.update(_match_existing_keys(candidate, _DEPENDENCIES.get(field, set())))

    # Assumption transparency can be affected by edits in any generated
    # section, but only include it when the DTO actually has the field.
    scoped.update(_match_existing_keys(candidate, ["assumptions", "consistencyWarnings"]))
    return scoped - never_rewritable


def apply_scoped_rewrite(
    agent_name: str,
    original: Candidate,
    rewritten: Candidate,
    blocking_issues: list[ReviewerIssue],
) -> tuple[Candidate, set[str]]:
    """Merge a complete rewritten object while restoring untouched sections."""
    scope = revision_scope_for(agent_name, original, blocking_issues)

    if agent_name != "SEDocumentationAgent" or scope == set(original.keys()):
        return deepcopy(rewritten), scope

    merged = deepcopy(original)
    for field in scope:
        if field in rewritten:
            merged[field] = deepcopy(rewritten[field])

    # Preserve any new top-level field only when it was explicitly in scope.
    # Unknown extra fields are left to Pydantic's schema rules.
    return merged, scope
