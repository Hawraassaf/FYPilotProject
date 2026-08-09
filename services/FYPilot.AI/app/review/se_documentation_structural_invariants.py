"""
Structured, machine-readable diagnostics + deterministic-first reconciliation
for SE Documentation's whole-document structural invariants
(SEDocumentationCandidateSchema._check_structural_invariants in registry.py).

That validator is a Pydantic `model_validator(mode="after")` -- it compares
MULTIPLE sections against each other (e.g. "does every useCase's
relatedRequirements id actually exist in functionalRequirements") and raises
on the FIRST violation with `loc=()`, which schema_validation.py's
_format_location renders as the unlocalizable `"$"` root location. Before
this module, an unlocalizable `$` error had exactly one path: fall back to
resending the COMPLETE document for a full LLM repair -- which is exactly
the oversized-payload problem the per-section repair queue exists to avoid,
so it was correctly refused rather than sent, and the run ended honestly
unresolved.

This module gives the pipeline a way to actually FIX the common, well-
understood cases of that failure without ever touching the fragile-regex
path the task explicitly forbids: it reimplements a KNOWN SUBSET of
_check_structural_invariants' checks (duplicate ids, duplicate entity names,
dangling requirement references, unresolvable relationship endpoints) as a
pure function returning STRUCTURED `InvariantIssue` objects instead of
raising -- collecting every violation in one pass, not just the first.

Deliberately does NOT replace or modify _check_structural_invariants itself:
that validator remains the single authoritative fail-fast gate (zero
regression risk to its existing, already-tested behavior, including checks
this module does not attempt to reconcile -- e.g. the uiScreens
dev-module-naming heuristic, the database entity field/primary-key/password
hard rules, and the assumption-zero rule). When diagnose_structural_
invariants finds nothing (because the actual failure is one of those
uncovered checks), the caller falls through to the existing, unchanged
full-repair-or-reject path -- exactly today's behavior, never a regression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.review.se_documentation_id_lineage import allocate_next_id

Candidate = dict[str, Any]

# Every kind this module can emit. Kept as a closed set (not a free string)
# so a caller can exhaustively branch on `issue.kind` and never guess.
DUPLICATE_ID = "duplicate_id"
MISSING_ID = "missing_id"
DANGLING_REFERENCE = "dangling_reference"
INVALID_REFERENCE_NAMESPACE = "invalid_reference_namespace"
UNEXPECTED_ITEM_REMOVAL = "unexpected_item_removal"
UNAUTHORIZED_ID_CHANGE = "unauthorized_id_change"
MISSING_RELATIONSHIP_ENDPOINT = "missing_relationship_endpoint"
DUPLICATE_ENTITY_NAME = "duplicate_entity_name"
FOREIGN_KEY_TARGET_MISSING = "foreign_key_target_missing"

# Added for full SEDocumentationCandidateSchema._check_structural_invariants
# coverage (see this module's coverage matrix in the fix report).
#
# MISSING_PRIMARY_KEY, MULTIPLE_PRIMARY_KEYS, EMPTY_ENTITY_FIELDS,
# INSUFFICIENT_ENTITY_FIELDS, PLAINTEXT_PASSWORD_FIELD, and
# MISSING_ASSUMPTION_DISCLOSURE are fixed by a DIFFERENT, earlier mechanism
# than reconcile_deterministically below: se_documentation_deterministic_
# normalization.py's normalize_database_entities_dicts/
# rebuild_assumptions_disclosure run unconditionally right after every
# rewrite that touches databaseEntities/any section (see pipeline.py's
# per-section repair and per-group semantic-rewrite loops), so by the time
# diagnose_structural_invariants runs again at this module's whole-document
# reconciliation stage, these issues should already be gone. They remain
# OUT of RECONCILABLE_KINDS below on purpose -- this fallback stage has no
# safe strategy of its own for them (it would have to fabricate content),
# and does not need one now that normalization runs earlier.
#
# DEV_MODULE_NAMED_AS_SCREEN is different: there is no deterministic rule
# that can SYNTHESIZE a good screen name, so it stays in RECONCILABLE_KINDS
# -- resolved by restoring the previous known-good screen when one exists
# (see reconcile_deterministically's baseline_screens_by_id parameter),
# falling back to the existing minimal-closure targeted-correction path
# (uiScreens only) otherwise -- never an automatic whole-document failure.
DEV_MODULE_NAMED_AS_SCREEN = "dev_module_named_as_screen"
MISSING_PRIMARY_KEY = "missing_primary_key"
MULTIPLE_PRIMARY_KEYS = "multiple_primary_keys"
EMPTY_ENTITY_FIELDS = "empty_entity_fields"
INSUFFICIENT_ENTITY_FIELDS = "insufficient_entity_fields"
PLAINTEXT_PASSWORD_FIELD = "plaintext_password_field"
MISSING_ASSUMPTION_DISCLOSURE = "missing_assumption_disclosure"

# The subset of `kind` values reconcile_deterministically/the pipeline's
# targeted-correction step is actually wired to act on -- see
# _attempt_se_documentation_invariant_reconciliation in pipeline.py, which
# filters diagnose_structural_invariants' full result down to this set
# before doing anything with it. Every OTHER kind above is either fixed
# earlier by unconditional normalization (see the comment above) or, if it
# still somehow reaches this stage, reported (logged, available for local
# replay/testing) but deliberately never triggers a deterministic edit or
# an LLM call here: this codebase does not have a verified safe
# reconciliation strategy for it AT THIS STAGE, and guessing one is exactly
# the "fabricate a mapping" behavior this module's docstring forbids.
RECONCILABLE_KINDS = frozenset({
    DUPLICATE_ID, DUPLICATE_ENTITY_NAME, DANGLING_REFERENCE,
    INVALID_REFERENCE_NAMESPACE, MISSING_RELATIONSHIP_ENDPOINT,
    DEV_MODULE_NAMED_AS_SCREEN,
})


@dataclass(frozen=True)
class InvariantIssue:
    kind: str
    source_section: str | None = None
    source_item_id: str | None = None
    source_field: str | None = None
    invalid_id: str | None = None
    target_section: str | None = None
    indices: tuple[int, ...] = ()
    previous_id: str | None = None
    rewritten_id: str | None = None
    suggested_sections: frozenset[str] = frozenset()
    # A short, student-safe sentence describing the mismatch -- never
    # includes raw provider/system internals, safe to show in a UI or log
    # line a non-technical reader might see.
    description: str = ""


# Sections whose items reference a functionalRequirements/nonFunctionalRequirements
# id, and the field(s) on each row that carry that reference. Mirrors
# SEDocumentationCandidateSchema._check_structural_invariants' own checks.
_REQUIREMENT_REFERENCING_SECTIONS: dict[str, tuple[str, ...]] = {
    "useCases": ("relatedRequirements",),
    "edgeCases": ("relatedRequirement",),
    "systemModules": ("relatedRequirements",),
    "testingPlan": ("relatedRequirements",),
    "uiScreens": ("relatedRequirements",),
}

_REQUIREMENT_ID_PATTERN = re.compile(r"^(FR|NFR)-\d+$", re.IGNORECASE)

_ID_FIELD_BY_SECTION: dict[str, str] = {
    "functionalRequirements": "id",
    "nonFunctionalRequirements": "id",
    "useCases": "id",
    "edgeCases": "id",
    "systemModules": "id",
    "testingPlan": "id",
    "uiScreens": "screenId",
}

# resolve_minimal_affected_sections' table (see this task's spec section 5).
_MINIMAL_CLOSURE_BY_SOURCE_SECTION: dict[str, frozenset[str]] = {
    "useCases": frozenset({"functionalRequirements", "nonFunctionalRequirements", "useCases"}),
    "edgeCases": frozenset({"functionalRequirements", "nonFunctionalRequirements", "edgeCases"}),
    "systemModules": frozenset({"functionalRequirements", "nonFunctionalRequirements", "systemModules"}),
    "testingPlan": frozenset({"functionalRequirements", "nonFunctionalRequirements", "testingPlan"}),
    "uiScreens": frozenset({"useCases", "uiScreens"}),
    "apiIntegrationPoints": frozenset({"functionalRequirements", "nonFunctionalRequirements", "apiIntegrationPoints"}),
    "entityRelationships": frozenset({"databaseEntities", "entityRelationships"}),
    "aiTechnicalReport": frozenset({"architecture", "aiTechnicalReport"}),
}


def _items(candidate: Candidate, section: str) -> list[dict]:
    value = candidate.get(section)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


_DEV_MODULE_SUFFIXES = ("module", "service layer", "repository", "engine", "middleware", "pipeline", "worker")
_SCREEN_LOOKING_WORDS = ("screen", "page", "dashboard", "view")


def _is_dev_module_screen_name(name: str) -> bool:
    """Byte-for-byte the same heuristic SEDocumentationCandidateSchema.
    _check_structural_invariants uses -- shared here so diagnose_structural_
    invariants (detection) and reconcile_deterministically (restoration; see
    DEV_MODULE_NAMED_AS_SCREEN's handling below) can never disagree about
    what counts as a dev-module-shaped name."""
    lowered = name.lower()
    looks_like_module = any(suffix in lowered for suffix in _DEV_MODULE_SUFFIXES)
    looks_like_screen = any(word in lowered for word in _SCREEN_LOOKING_WORDS)
    return looks_like_module and not looks_like_screen


def diagnose_structural_invariants(candidate: Candidate) -> list[InvariantIssue]:
    """
    Pure function -- never mutates `candidate`, never raises. Returns every
    issue this module knows how to reconcile (an empty list means either the
    document is fine, or its actual failure is one of
    _check_structural_invariants' OTHER checks this module does not attempt
    to reconcile -- see this module's docstring).
    """
    issues: list[InvariantIssue] = []

    # -- duplicate_id: FR/NFR/UC/EC/MOD/TC (by "id"), uiScreens (by "screenId") --
    for section, id_field in _ID_FIELD_BY_SECTION.items():
        items = _items(candidate, section)
        seen: dict[str, list[int]] = {}
        for index, item in enumerate(items):
            item_id = item.get(id_field)
            if item_id:
                seen.setdefault(str(item_id), []).append(index)
        for dup_id, indices in seen.items():
            if len(indices) > 1:
                issues.append(InvariantIssue(
                    kind=DUPLICATE_ID,
                    source_section=section,
                    invalid_id=dup_id,
                    indices=tuple(indices),
                    suggested_sections=frozenset({section}),
                    description=(
                        f"The id {dup_id!r} is used by {len(indices)} different entries in "
                        f"{section}; every id in this section must be unique."
                    ),
                ))

    # -- duplicate_entity_name: databaseEntities (by "name") --
    entities = _items(candidate, "databaseEntities")
    seen_names: dict[str, list[int]] = {}
    for index, entity in enumerate(entities):
        name = entity.get("name")
        if name:
            seen_names.setdefault(str(name), []).append(index)
    for dup_name, indices in seen_names.items():
        if len(indices) > 1:
            issues.append(InvariantIssue(
                kind=DUPLICATE_ENTITY_NAME,
                source_section="databaseEntities",
                invalid_id=dup_name,
                indices=tuple(indices),
                suggested_sections=frozenset({"databaseEntities"}),
                description=(
                    f"The database entity name {dup_name!r} is used by {len(indices)} "
                    "different entities; every entity name must be unique."
                ),
            ))

    # -- dangling_reference / invalid_reference_namespace: *.relatedRequirement(s) -> FR/NFR --
    requirement_ids = {
        str(item["id"]) for item in _items(candidate, "functionalRequirements") if item.get("id")
    } | {
        str(item["id"]) for item in _items(candidate, "nonFunctionalRequirements") if item.get("id")
    }

    for section, ref_fields in _REQUIREMENT_REFERENCING_SECTIONS.items():
        for item in _items(candidate, section):
            item_id = str(item.get(_ID_FIELD_BY_SECTION.get(section, "id")) or "") or None
            for ref_field in ref_fields:
                raw_value = item.get(ref_field)
                referenced_ids = (
                    [str(v) for v in raw_value if v] if isinstance(raw_value, list)
                    else ([str(raw_value)] if raw_value else [])
                )
                for referenced_id in referenced_ids:
                    if referenced_id in requirement_ids:
                        continue
                    closure = _MINIMAL_CLOSURE_BY_SOURCE_SECTION.get(section, frozenset({section}))
                    if _REQUIREMENT_ID_PATTERN.match(referenced_id):
                        issues.append(InvariantIssue(
                            kind=DANGLING_REFERENCE,
                            source_section=section,
                            source_item_id=item_id,
                            source_field=ref_field,
                            invalid_id=referenced_id,
                            target_section="functionalRequirements/nonFunctionalRequirements",
                            suggested_sections=closure,
                            description=(
                                f"{section} entry {item_id!r} references requirement "
                                f"{referenced_id!r}, which does not exist in this document."
                            ),
                        ))
                    else:
                        issues.append(InvariantIssue(
                            kind=INVALID_REFERENCE_NAMESPACE,
                            source_section=section,
                            source_item_id=item_id,
                            source_field=ref_field,
                            invalid_id=referenced_id,
                            target_section="functionalRequirements/nonFunctionalRequirements",
                            suggested_sections=closure,
                            description=(
                                f"{section} entry {item_id!r} references {referenced_id!r}, "
                                "which is not a valid requirement id (expected FR-N or NFR-N)."
                            ),
                        ))

    # -- missing_relationship_endpoint: entityRelationships.fromEntity/toEntity -> databaseEntities --
    entity_names = {str(e["name"]) for e in entities if e.get("name")}
    for index, relationship in enumerate(_items(candidate, "entityRelationships")):
        for endpoint_field in ("fromEntity", "toEntity"):
            endpoint = relationship.get(endpoint_field)
            if endpoint and str(endpoint) not in entity_names:
                issues.append(InvariantIssue(
                    kind=MISSING_RELATIONSHIP_ENDPOINT,
                    source_section="entityRelationships",
                    source_field=endpoint_field,
                    invalid_id=str(endpoint),
                    target_section="databaseEntities",
                    indices=(index,),
                    suggested_sections=_MINIMAL_CLOSURE_BY_SOURCE_SECTION["entityRelationships"],
                    description=(
                        f"entityRelationships row {index} references entity {str(endpoint)!r} "
                        f"as its {endpoint_field}, but no databaseEntities entry has that name."
                    ),
                ))

    # -- dev_module_named_as_screen: uiScreens whose name reads like a
    # backend/dev component rather than a user-facing screen. Mirrors
    # SEDocumentationCandidateSchema._check_structural_invariants' own
    # heuristic exactly (see _is_dev_module_screen_name below) so this
    # diagnostic's detection can never disagree with why the real validator
    # raised.
    for index, screen in enumerate(_items(candidate, "uiScreens")):
        name = str(screen.get("name") or "")
        if _is_dev_module_screen_name(name):
            issues.append(InvariantIssue(
                kind=DEV_MODULE_NAMED_AS_SCREEN,
                source_section="uiScreens",
                source_item_id=str(screen.get("screenId") or "") or None,
                source_field="name",
                invalid_id=name,
                indices=(index,),
                suggested_sections=frozenset({"uiScreens"}),
                description=(
                    f"uiScreens entry {name!r} reads like a development module or backend "
                    "service, not a user-facing screen."
                ),
            ))

    # -- database entity field/primary-key/password checks. Mirrors
    # SEDocumentationCandidateSchema._check_structural_invariants' own
    # database hard rules exactly. VISIBILITY ONLY -- see RECONCILABLE_KINDS.
    for index, entity in enumerate(entities):
        name = str(entity.get("name") or f"entity[{index}]")
        fields = entity.get("fields") or []
        foreign_keys = entity.get("foreignKeys") or []

        if not fields:
            issues.append(InvariantIssue(
                kind=EMPTY_ENTITY_FIELDS,
                source_section="databaseEntities",
                source_item_id=name,
                indices=(index,),
                suggested_sections=frozenset({"databaseEntities"}),
                description=f"Database entity {name!r} has no field details (empty fields list).",
            ))
            continue

        is_junction = len(foreign_keys) >= 2 and len(fields) <= 3
        if not is_junction and len(fields) < 3:
            issues.append(InvariantIssue(
                kind=INSUFFICIENT_ENTITY_FIELDS,
                source_section="databaseEntities",
                source_item_id=name,
                indices=(index,),
                suggested_sections=frozenset({"databaseEntities"}),
                description=(
                    f"Database entity {name!r} has fewer than 3 fields and is not a junction table."
                ),
            ))

        primary_key_fields = [f for f in fields if isinstance(f, dict) and f.get("isPrimaryKey")]
        if not primary_key_fields:
            issues.append(InvariantIssue(
                kind=MISSING_PRIMARY_KEY,
                source_section="databaseEntities",
                source_item_id=name,
                indices=(index,),
                suggested_sections=frozenset({"databaseEntities"}),
                description=f"Database entity {name!r} has no primary key field.",
            ))
        elif len(primary_key_fields) > 1:
            issues.append(InvariantIssue(
                kind=MULTIPLE_PRIMARY_KEYS,
                source_section="databaseEntities",
                source_item_id=name,
                indices=(index,),
                suggested_sections=frozenset({"databaseEntities"}),
                description=(
                    f"Database entity {name!r} has {len(primary_key_fields)} fields marked as "
                    "primary key; exactly one is expected."
                ),
            ))

        for field_item in fields:
            if not isinstance(field_item, dict):
                continue
            field_name = str(field_item.get("name") or "").strip().lower()
            if field_name == "password":
                issues.append(InvariantIssue(
                    kind=PLAINTEXT_PASSWORD_FIELD,
                    source_section="databaseEntities",
                    source_item_id=name,
                    source_field="password",
                    indices=(index,),
                    suggested_sections=frozenset({"databaseEntities"}),
                    description=(
                        f"Database entity {name!r} has a plaintext 'Password' field; use "
                        "'PasswordHash' instead."
                    ),
                ))

    # -- missing_assumption_disclosure: assumption-zero rule. Mirrors
    # SEDocumentationCandidateSchema._check_structural_invariants exactly.
    # VISIBILITY ONLY -- see RECONCILABLE_KINDS.
    non_confirmed = {"inferred", "assumption", "proposed_target", "unknown", "unresolved"}
    if not candidate.get("assumptions"):
        classified_items = [
            item.get("sourceClassification")
            for section in (
                "functionalRequirements", "nonFunctionalRequirements", "useCases", "edgeCases",
                "systemModules", "databaseEntities", "uiScreens", "apiIntegrationPoints", "testingPlan",
            )
            for item in _items(candidate, section)
        ]
        if any(classification in non_confirmed for classification in classified_items):
            issues.append(InvariantIssue(
                kind=MISSING_ASSUMPTION_DISCLOSURE,
                source_section="assumptions",
                suggested_sections=frozenset({"assumptions"}),
                description=(
                    "The assumptions list is empty but at least one section item is classified "
                    "as inferred/assumption/proposed_target/unknown/unresolved."
                ),
            ))

    return issues


def resolve_minimal_affected_sections(issues: list[InvariantIssue]) -> frozenset[str]:
    """Union of every remaining issue's own `suggested_sections` -- never
    widened beyond what the issues themselves name, and overlapping closures
    are naturally combined by set union rather than duplicated."""
    closure: set[str] = set()
    for issue in issues:
        closure |= set(issue.suggested_sections)
    return frozenset(closure)


def reconcile_deterministically(
    candidate: Candidate,
    issues: list[InvariantIssue],
    *,
    added_ids_by_section: dict[str, frozenset[str]] | None = None,
    rename_maps_by_section: dict[str, dict[str, str]] | None = None,
    baseline_screens_by_id: dict[str, dict] | None = None,
) -> tuple[Candidate, list[InvariantIssue]]:
    """
    Resolves what CAN be resolved without another LLM call; returns
    (possibly-updated candidate, remaining unresolved issues).

    `added_ids_by_section`: ids this repair PASS itself just introduced into
    a section (from se_documentation_id_lineage's per-section lineage,
    change="added") -- duplicate_id is only ever auto-resolved by renaming
    one of THESE ids (the item this pass is responsible for), never an id
    that was already present before this pass started. Ambiguous duplicates
    (neither side is a known just-added id) are left unresolved rather than
    guessed.

    `rename_maps_by_section`: old->new id maps from AUTHORIZED renames this
    pass already applied to OTHER sections (see se_documentation_id_lineage)
    -- used to remap a dangling_reference that is dangling only because its
    target was legitimately renamed earlier in this same pass.

    `baseline_screens_by_id`: the last known-good candidate's uiScreens,
    keyed by screenId -- used ONLY to resolve DEV_MODULE_NAMED_AS_SCREEN by
    restoring the previous valid screen wholesale when one exists and its
    OWN name was not itself dev-module-shaped (never "fixing" by
    reintroducing the same problem). When no matching baseline screen
    exists, the issue is left unresolved for the caller's targeted-
    correction fallback (uiScreens only) -- never fabricated.
    """
    added_ids_by_section = added_ids_by_section or {}
    rename_maps_by_section = rename_maps_by_section or {}
    baseline_screens_by_id = baseline_screens_by_id or {}
    remaining: list[InvariantIssue] = []
    updated = {key: (list(value) if isinstance(value, list) else value) for key, value in candidate.items()}

    # Combine every requirement-namespace rename map (FR/NFR renames are the
    # only ones dangling_reference in this module ever points at).
    requirement_rename_map: dict[str, str] = {}
    for section in ("functionalRequirements", "nonFunctionalRequirements"):
        requirement_rename_map.update(rename_maps_by_section.get(section, {}))

    for issue in issues:
        if issue.kind == DEV_MODULE_NAMED_AS_SCREEN:
            screen_id = issue.source_item_id
            baseline_screen = baseline_screens_by_id.get(screen_id) if screen_id else None

            if baseline_screen is not None and not _is_dev_module_screen_name(str(baseline_screen.get("name") or "")):
                items = updated.get("uiScreens")
                if isinstance(items, list):
                    for index, item in enumerate(items):
                        if isinstance(item, dict) and item.get("screenId") == screen_id:
                            items[index] = dict(baseline_screen)
                    continue

            # No usable baseline (never existed, or was itself dev-module-
            # shaped) -- never invent a replacement name; leave for the
            # caller's targeted-correction fallback (uiScreens only).
            remaining.append(issue)
            continue

        if issue.kind == DANGLING_REFERENCE and issue.invalid_id in requirement_rename_map:
            new_id = requirement_rename_map[issue.invalid_id]
            items = updated.get(issue.source_section)
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get(_ID_FIELD_BY_SECTION.get(issue.source_section, "id")) != issue.source_item_id:
                        continue
                    raw_value = item.get(issue.source_field)
                    if isinstance(raw_value, list):
                        item[issue.source_field] = [
                            new_id if v == issue.invalid_id else v for v in raw_value
                        ]
                    elif raw_value == issue.invalid_id:
                        item[issue.source_field] = new_id
            continue

        if issue.kind in (DUPLICATE_ID, DUPLICATE_ENTITY_NAME):
            section = issue.source_section
            id_field = "name" if issue.kind == DUPLICATE_ENTITY_NAME else _ID_FIELD_BY_SECTION.get(section, "id")
            just_added = added_ids_by_section.get(section, frozenset())

            if issue.invalid_id not in just_added:
                # Neither/ambiguous which occurrence this pass is
                # responsible for -- never guess which one to rename.
                remaining.append(issue)
                continue

            items = updated.get(section)
            if not isinstance(items, list):
                remaining.append(issue)
                continue

            existing_ids = {
                str(item.get(id_field)) for item in items if isinstance(item, dict) and item.get(id_field)
            }
            prefix = _id_prefix_for_section(section)
            new_id = allocate_next_id(prefix, existing_ids)

            renamed_once = False
            for item in items:
                if not isinstance(item, dict) or renamed_once:
                    continue
                if item.get(id_field) == issue.invalid_id:
                    item[id_field] = new_id
                    renamed_once = True
            continue

        remaining.append(issue)

    return updated, remaining


def _id_prefix_for_section(section: str | None) -> str:
    return {
        "functionalRequirements": "FR",
        "nonFunctionalRequirements": "NFR",
        "useCases": "UC",
        "edgeCases": "EC",
        "systemModules": "MOD",
        "databaseEntities": "ENT",
        "uiScreens": "UI",
        "apiIntegrationPoints": "API",
        "testingPlan": "TC",
    }.get(section or "", "ITEM")


def rebuild_traceability_matrix(candidate: Candidate) -> list[dict]:
    """
    Deterministically rebuilds traceabilityMatrix from the (already
    reconciled) candidate's own sections -- byte-for-byte the same
    requirement-centric construction SEDocumentationOrchestratorAgent.
    _build_traceability uses at Writer time (see that method's docstring),
    just operating on plain dicts instead of typed DTOs so it can run again
    here, after reconciliation, without reconstructing the Writer's full
    ProjectFacts/architecture context. Never asks an LLM to repair this
    field -- it is always fully derived from the other sections' own real
    relatedRequirements/relatedRequirementIds back-references.
    """
    frs = _items(candidate, "functionalRequirements")
    use_cases = _items(candidate, "useCases")
    modules = _items(candidate, "systemModules")
    entities = _items(candidate, "databaseEntities")
    ui_screens = _items(candidate, "uiScreens")
    api_points = _items(candidate, "apiIntegrationPoints")
    tests = _items(candidate, "testingPlan")

    rows: list[dict] = []
    for fr in frs:
        fr_id = fr.get("id")
        matched_use_cases = [uc.get("id") for uc in use_cases if fr_id in (uc.get("relatedRequirements") or [])]
        matched_modules = [m.get("id") for m in modules if fr_id in (m.get("relatedRequirements") or [])]
        matched_entities = [
            e.get("entityId") or e.get("name") for e in entities if fr_id in (e.get("relatedRequirementIds") or [])
        ]
        matched_screens = [s.get("screenId") for s in ui_screens if fr_id in (s.get("relatedRequirements") or [])]
        matched_apis = [a.get("apiId") or a.get("name") for a in api_points if fr_id in (a.get("relatedRequirements") or [])]
        matched_tests = [t.get("id") for t in tests if fr_id in (t.get("relatedRequirements") or [])]

        notes = []
        if not matched_entities:
            notes.append("No database entity applies to this requirement (N/A).")
        if not matched_screens:
            notes.append("No UI screen applies to this requirement (N/A).")
        if not matched_apis:
            notes.append("No API/integration applies to this requirement (N/A).")

        if matched_tests and matched_use_cases:
            coverage_status = "covered"
        elif matched_tests:
            coverage_status = "partially_covered"
        else:
            coverage_status = "uncovered"

        rows.append({
            "requirementId": fr_id,
            "useCaseId": matched_use_cases[0] if matched_use_cases else "",
            "moduleId": matched_modules[0] if matched_modules else "",
            "entity": matched_entities[0] if matched_entities else "",
            "testCaseId": matched_tests[0] if matched_tests else "",
            "screenId": matched_screens[0] if matched_screens else "",
            "apiId": matched_apis[0] if matched_apis else "",
            "useCaseIds": matched_use_cases,
            "moduleIds": matched_modules,
            "entityIds": matched_entities,
            "screenIds": matched_screens,
            "apiIds": matched_apis,
            "testCaseIds": matched_tests,
            "coverageStatus": coverage_status,
            "notes": "; ".join(notes),
        })

    return rows


def _mermaid_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (value or "").strip())
    return cleaned.upper() or "ENTITY"


def rebuild_mermaid_erd(candidate: Candidate) -> str:
    """Byte-for-byte the same construction as SEDocumentationOrchestratorAgent
    ._build_erd (see that method) -- rebuilt here on plain dicts so a
    databaseEntities/entityRelationships reconciliation can refresh the
    diagram without any LLM involvement; a diagram field is always in
    _NEVER_LLM_REWRITABLE_FIELDS and is never itself an LLM correction
    target."""
    entities = _items(candidate, "databaseEntities")
    relationships = _items(candidate, "entityRelationships")
    entity_names = {e.get("name") for e in entities if e.get("name")}

    lines = ["erDiagram"]
    for relationship in relationships:
        from_entity = relationship.get("fromEntity")
        to_entity = relationship.get("toEntity")
        if from_entity not in entity_names or to_entity not in entity_names:
            continue
        left = _mermaid_name(from_entity)
        right = _mermaid_name(to_entity)
        symbol = "||--o{" if "many" in str(relationship.get("type") or "") else "||--||"
        lines.append(f"    {left} {symbol} {right} : relates")

    if len(lines) == 1 and len(entities) >= 2:
        lines.append(f"    {_mermaid_name(entities[0].get('name'))} ||--o{{ {_mermaid_name(entities[1].get('name'))} : owns")

    return "\n".join(lines)


def rebuild_mermaid_class_diagram(candidate: Candidate) -> str:
    """Byte-for-byte the same construction as SEDocumentationOrchestratorAgent
    ._build_class_diagram (see that method) -- rebuilt here on plain dicts
    for the same reason rebuild_mermaid_erd is: a databaseEntities
    reconciliation must be able to refresh this diagram deterministically,
    without any LLM involvement (mermaidClassDiagram is always in
    _NEVER_LLM_REWRITABLE_FIELDS)."""
    import re as _re

    lines = ["classDiagram"]
    for entity in _items(candidate, "databaseEntities"):
        name = _mermaid_name(str(entity.get("name") or ""))
        lines.append(f"    class {name} {{")
        field_names = [f.get("name") for f in (entity.get("fields") or []) if isinstance(f, dict)] or entity.get("importantFields") or []
        for field_name in list(field_names)[:6]:
            safe_field = _re.sub(r"[^A-Za-z0-9_]", "", str(field_name))
            lines.append(f"      string {safe_field or 'Field'}")
        lines.append("    }")

    return "\n".join(lines)


def _facts_from_candidate(candidate: Candidate):
    """Builds a minimal ProjectFacts standing in for the Writer-time facts
    object (never persisted on the candidate itself), using only what the
    candidate document already discloses -- projectTitle and the first
    stakeholder as primary_actor, aiTechnicalReportApplicable for
    ai_involved. Good enough for _select_primary_use_case/
    _build_activity_diagram/_build_sequence_diagram, which only ever read
    facts.title/primary_actor/ai_involved, never anything requiring the
    original idea-selection context."""
    from app.agents.se_documentation.project_facts import ProjectFacts

    stakeholders = candidate.get("stakeholders") or []
    primary_actor = str(stakeholders[0]) if stakeholders else "User"
    return ProjectFacts(
        title=str(candidate.get("projectTitle") or "Project"),
        problem="", solution="",
        primary_actor=primary_actor,
        ai_involved=bool(candidate.get("aiTechnicalReportApplicable")),
    )


def rebuild_mermaid_activity_diagram(candidate: Candidate) -> str:
    """
    Byte-for-byte the same construction as SEDocumentationOrchestratorAgent
    ._build_activity_diagram (reused directly, not duplicated, via a lazy
    import -- see _facts_from_candidate) -- rebuilt here on a plain dict so
    a useCases/functionalRequirements reconciliation can refresh the
    diagram deterministically, without any LLM involvement. Before this
    function existed, activityDiagram was never rebuilt after a rewrite/
    repair pass touched useCases or functionalRequirements, so it could go
    stale -- e.g. still showing an old primary workflow after the use case
    it was built from was corrected or removed.
    """
    from app.agents.se_documentation.se_documentation_orchestrator import (
        EdgeCaseDto,
        RequirementDto,
        SEDocumentationOrchestratorAgent,
        UseCaseDto,
    )

    try:
        frs = [RequirementDto.model_validate(x) for x in _items(candidate, "functionalRequirements")]
        use_cases = [UseCaseDto.model_validate(x) for x in _items(candidate, "useCases")]
        edge_cases = [EdgeCaseDto.model_validate(x) for x in _items(candidate, "edgeCases")]
    except Exception:
        # A section that doesn't even parse into its own DTO is a SEPARATE
        # schema-validity problem the normal validation/repair path is
        # responsible for -- never crash the reconciliation pass over it.
        return str(candidate.get("activityDiagram") or "")

    agent = SEDocumentationOrchestratorAgent()
    facts = _facts_from_candidate(candidate)
    primary = agent._select_primary_use_case(facts, frs, use_cases)
    return agent._build_activity_diagram(facts, primary, edge_cases)


def rebuild_mermaid_sequence_diagram(candidate: Candidate) -> str:
    """
    Byte-for-byte the same construction as SEDocumentationOrchestratorAgent
    ._build_sequence_diagram (reused directly, not duplicated -- see
    rebuild_mermaid_activity_diagram's docstring for why activity/sequence
    are rebuilt this way instead of a dict-only reimplementation like
    rebuild_mermaid_erd/rebuild_mermaid_class_diagram use). Rebuilt on a
    plain dict so a useCases/functionalRequirements/modulesArchitecture/
    uiApi (apiIntegrationPoints) reconciliation can refresh the diagram
    deterministically. A databaseEntities-only change never alters the
    inputs this function reads (functionalRequirements/useCases/
    systemModules/architecture/apiIntegrationPoints), so calling this after
    such a change is a harmless no-op rebuild, never an unnecessary content
    change.
    """
    from app.agents.se_documentation.se_documentation_orchestrator import (
        ApiPointDto,
        ArchitectureDto,
        ModuleDto,
        RequirementDto,
        SEDocumentationOrchestratorAgent,
        UseCaseDto,
    )

    architecture_raw = candidate.get("architecture")
    if not isinstance(architecture_raw, dict):
        return str(candidate.get("sequenceDiagram") or "")

    try:
        frs = [RequirementDto.model_validate(x) for x in _items(candidate, "functionalRequirements")]
        use_cases = [UseCaseDto.model_validate(x) for x in _items(candidate, "useCases")]
        modules = [ModuleDto.model_validate(x) for x in _items(candidate, "systemModules")]
        api_points = [ApiPointDto.model_validate(x) for x in _items(candidate, "apiIntegrationPoints")]
        architecture = ArchitectureDto.model_validate(architecture_raw)
    except Exception:
        return str(candidate.get("sequenceDiagram") or "")

    agent = SEDocumentationOrchestratorAgent()
    facts = _facts_from_candidate(candidate)
    primary = agent._select_primary_use_case(facts, frs, use_cases)
    return agent._build_sequence_diagram(facts, architecture, primary, frs, modules, api_points)
