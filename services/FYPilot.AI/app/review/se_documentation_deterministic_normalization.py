"""
Deterministic post-rewrite normalization for SE Documentation.

SEDocumentationOrchestratorAgent already guarantees several hard rules at
WRITER time (see se_documentation_orchestrator.py):
  - _normalize_entities: no databaseEntities entry renders with an empty
    field list, every non-junction entity has >=3 fields, exactly one
    primary key, plaintext "Password" fields become "PasswordHash", and
    dangling foreign keys are dropped rather than left pointing nowhere.
  - _collect_assumptions: scans every section's own sourceClassification
    and ensures the assumptions list discloses every inferred/assumed/
    proposed/unresolved item, never silently claims "no assumptions" while
    content is actually uncertain.

Neither function runs again after ReviewPipeline merges a section rewrite or
structural repair -- so a rewrite that reintroduces an empty field list, a
missing primary key, a plaintext password, or new inferred content had no
enforcement past the Writer's own initial pass. This module closes that gap
by REUSING those exact functions/algorithms against a post-merge candidate
dict, rather than duplicating competing validation logic.
"""

from __future__ import annotations

import re
from typing import Any

Candidate = dict[str, Any]

# Matches the leading id token of a disclosure this module (or
# SEDocumentationOrchestratorAgent._collect_assumptions, which uses the
# identical "{item_id}: ..." format) generated -- e.g. "ENT-05: ...",
# "FR-01: ...". Only entries matching this pattern are ever reconciled by
# rebuild_assumptions_disclosure below; anything else (a free-form project-
# level assumption, an unresolved-technical-decision entry, or the used-
# fallback disclaimer -- none of which carry an id prefix, see
# SEDocumentationOrchestratorAgent._collect_assumptions) is left completely
# untouched, in either order or content.
_ASSUMPTION_ID_PREFIX_PATTERN = re.compile(r"^([A-Za-z]+-\d+):\s")

# Mirrors SEDocumentationOrchestratorAgent._collect_assumptions' own
# per-section scan (id field, human-readable label field) -- see that
# method's per-section `_add(...)` calls. Kept in the same order so the
# resulting disclosure list's construction order matches Writer-time
# behavior as closely as possible.
_ASSUMPTION_SCAN_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("functionalRequirements", "id", "title"),
    ("nonFunctionalRequirements", "id", "title"),
    ("useCases", "id", "title"),
    ("edgeCases", "id", "scenario"),
    ("systemModules", "id", "name"),
    ("databaseEntities", "entityId", "name"),
    ("uiScreens", "screenId", "name"),
    ("apiIntegrationPoints", "apiId", "name"),
    ("testingPlan", "id", "title"),
)

_NON_CONFIRMED_CLASSIFICATIONS = {"inferred", "assumption", "proposed_target", "unknown", "unresolved"}


def normalize_database_entities_dicts(entities: list[dict]) -> list[dict]:
    """
    Runs SEDocumentationOrchestratorAgent._normalize_entities -- the SAME
    Writer-time hard-rule normalization -- against a plain-dict entity list,
    by round-tripping through the orchestrator's own EntityDto. Reused, not
    duplicated, so this can never drift from what a freshly-generated
    section already guarantees. Never fabricates a foreign-key target: like
    _normalize_entities, a dangling foreign key is DROPPED, never redirected
    to a guessed entity.

    Additionally deduplicates multiple isPrimaryKey=True fields on the same
    entity down to exactly one (keeping the first) -- a case
    _normalize_entities itself does not handle (it only ever ADDS a primary
    key when none exists, see that method's docstring), needed for this
    task's MULTIPLE_PRIMARY_KEYS coverage.
    """
    from app.agents.se_documentation.se_documentation_orchestrator import (
        EntityDto,
        SEDocumentationOrchestratorAgent,
    )

    try:
        typed_entities = [EntityDto(**entity) for entity in entities]
    except Exception:
        # A response missing a required EntityDto field (e.g. `purpose`) is
        # a SEPARATE schema-validity problem the normal schema-validation /
        # repair path is responsible for -- never crash the reconciliation
        # pass over it; leave the entities exactly as received so the
        # caller's own full-document validate_detailed reports the real
        # error honestly.
        return [dict(entity) for entity in entities]

    normalized = SEDocumentationOrchestratorAgent()._normalize_entities(typed_entities)

    for entity in normalized:
        primary_fields = [field for field in entity.fields if field.isPrimaryKey]
        for extra in primary_fields[1:]:
            extra.isPrimaryKey = False

    return [entity.model_dump() for entity in normalized]


def rebuild_assumptions_disclosure(candidate: Candidate) -> list[dict]:
    """
    Mirrors SEDocumentationOrchestratorAgent._collect_assumptions' own
    per-section sourceClassification scan (see that method), applied here to
    a post-rewrite MERGED candidate dict instead of Writer-time typed DTOs --
    so a rewrite that introduces new inferred/assumed/proposed/unresolved
    content can never leave the assumptions section silently stale.

    Reconciles rather than only appending -- observed live 2026-08-06/07: a
    rewrite renamed a databaseEntities entry (e.g. ENT-05 from
    'ResponseFeedback' to 'TriageResult'), and the ORIGINAL Writer-time
    disclosure ("ENT-05: database entity 'ResponseFeedback' is classified
    as...") was never updated, leaving a permanently stale reference to a
    name that no longer exists anywhere in the document; separately, this
    function's own phrasing differed slightly from _collect_assumptions'
    own wording, so re-running it produced a near-duplicate entry about the
    SAME item instead of replacing the original. For every existing entry
    whose text starts with a recognized "{item_id}: " prefix (see
    _ASSUMPTION_ID_PREFIX_PATTERN):
      - if that id no longer exists in ANY scanned section at all, the
        disclosure is dropped (nothing left to refer to);
      - if the id exists but is no longer classified as non-confirmed, the
        disclosure is dropped (no longer needed);
      - if the id exists and is still non-confirmed, the entry is replaced
        with the freshly recomputed text for that id (using its CURRENT
        name/label) -- never left showing a stale name, and never
        duplicated alongside the fresh one.
    Every entry that does NOT match the id-prefix pattern (a free-form
    project-level assumption, an unresolved-technical-decision entry, the
    used-fallback disclaimer) is preserved completely untouched, in order.
    """
    existing = [dict(item) for item in (candidate.get("assumptions") or []) if isinstance(item, dict)]

    current_by_id: dict[str, dict] = {}
    all_current_ids: set[str] = set()
    for section, id_field, label_field in _ASSUMPTION_SCAN_SECTIONS:
        for item in candidate.get(section) or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get(id_field) or "")
            if not item_id:
                continue
            all_current_ids.add(item_id)

            classification = item.get("sourceClassification")
            if classification not in _NON_CONFIRMED_CLASSIFICATIONS:
                continue
            label = item.get(label_field) or item_id
            text = f"{item_id}: {label} is classified as '{classification}' rather than confirmed."
            current_by_id[item_id] = {"item": text, "classification": classification}

    reconciled: list[dict] = []
    seen_texts: set[str] = set()
    ids_handled: set[str] = set()

    for entry in existing:
        text = entry.get("item") or ""
        match = _ASSUMPTION_ID_PREFIX_PATTERN.match(text)

        if not match:
            # Free-form / manually authored / fallback disclosure -- never
            # reconciled, never deduplicated against anything else.
            reconciled.append(entry)
            continue

        item_id = match.group(1)
        ids_handled.add(item_id)

        if item_id not in all_current_ids:
            continue  # referenced item no longer exists at all -- drop

        fresh = current_by_id.get(item_id)
        if fresh is None:
            continue  # item exists but is now confirmed -- no longer needed

        if fresh["item"] in seen_texts:
            continue  # already emitted (e.g. a duplicate original entry)

        seen_texts.add(fresh["item"])
        reconciled.append(fresh)

    for item_id, fresh in current_by_id.items():
        if item_id in ids_handled or fresh["item"] in seen_texts:
            continue
        seen_texts.add(fresh["item"])
        reconciled.append(fresh)

    return reconciled
