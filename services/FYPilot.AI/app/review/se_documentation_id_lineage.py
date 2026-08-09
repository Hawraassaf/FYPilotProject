"""
Stable-ID lineage tracking for SE Documentation section rewrites/repairs.

A section-scoped LLM correction (see se_documentation_structural_repair_scope.py
/ se_documentation_rewrite_scope.py) is only ever asked to touch ONE section
(or a tightly-coupled group) at a time. That is what fixed the truncation
defect, but it also means the model can, in the same response, silently
rename an item's stable ID, drop an item entirely, or introduce a new item
whose ID collides with something already present elsewhere in the document
-- none of which the section's OWN schema validation can catch, since the
section still parses as perfectly valid JSON.

This module compares a section's ORIGINAL (pre-call) items against its
REWRITTEN (post-call) items by content fingerprint, not by list position (a
model is never guaranteed to preserve ordering), and:
  - restores any ID change / item removal that was not explicitly authorized
    by the correction request itself (`requested_ids` -- the ids named in the
    reviewer finding / validation error text this call was actually meant to
    address);
  - records an authorized rename's old->new mapping so callers can remap any
    OTHER section's dangling reference to the renamed id afterward, instead
    of leaving it dangling or guessing.

Never trusts item position as identity; never silently invents a mapping
for an item it cannot confidently match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.review.se_documentation_rewrite_scope import _ROW_ID_FIELDS

Candidate = dict[str, Any]
LineageChange = Literal["preserved", "renamed", "added", "removed"]


@dataclass(frozen=True)
class ItemLineage:
    original_id: str | None
    rewritten_id: str | None
    fingerprint: str
    change: LineageChange
    authorized: bool


@dataclass(frozen=True)
class SectionLineageResult:
    """`items` is the section's item list AFTER restoring any unauthorized
    rename/removal -- this, never the raw LLM response, is what callers must
    merge. `authorized_renames` (old id -> new id) is only ever populated for
    renames the caller's own `requested_ids` explicitly covered."""

    items: list[dict]
    lineage: list[ItemLineage]
    authorized_renames: dict[str, str] = field(default_factory=dict)


def _identity_field_used(section: str, row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for field_name in _ROW_ID_FIELDS.get(section, ()):
        if row.get(field_name):
            return field_name
    return None


def _row_identity(section: str, row: Any) -> str | None:
    field_name = _identity_field_used(section, row)
    return str(row[field_name]) if field_name else None


def _fingerprint(section: str, row: dict) -> str:
    """Content-based match key deliberately excluding this section's own
    identity fields (so a renamed item's otherwise-unchanged content can
    still be matched back to its original id) and excluding nested
    list/dict fields (too volatile/order-sensitive to use as a stable
    match key at this granularity)."""
    id_fields = set(_ROW_ID_FIELDS.get(section, ()))
    parts = []
    for key in sorted(row.keys()):
        if key in id_fields:
            continue
        value = row[key]
        if isinstance(value, (list, dict)):
            continue
        parts.append(f"{key}={value}")
    return "|".join(parts)


def compute_section_lineage(
    section: str,
    original_items: list[Any],
    rewritten_items: list[Any],
    *,
    requested_ids: frozenset[str] = frozenset(),
) -> SectionLineageResult:
    """
    `requested_ids` are the ORIGINAL ids the correction request explicitly
    named (e.g. from the reviewer finding's own text, or the validation
    error's referenced id) -- only a rename/removal of one of THESE ids is
    ever accepted; every other divergence from `original_items` is treated
    as an unrequested (unauthorized) change and restored.
    """
    original = [item for item in original_items if isinstance(item, dict)]
    rewritten = [item for item in rewritten_items if isinstance(item, dict)]

    original_by_id: dict[str, dict] = {}
    for item in original:
        rid = _row_identity(section, item)
        if rid is not None:
            original_by_id[rid] = item

    original_by_fingerprint: dict[str, str] = {}
    for oid, item in original_by_id.items():
        original_by_fingerprint.setdefault(_fingerprint(section, item), oid)

    lineage: list[ItemLineage] = []
    authorized_renames: dict[str, str] = {}
    restored_items: list[dict] = []
    matched_original_ids: set[str] = set()

    for item in rewritten:
        rid = _row_identity(section, item)
        fingerprint = _fingerprint(section, item)

        if rid is not None and rid in original_by_id and rid not in matched_original_ids:
            lineage.append(ItemLineage(rid, rid, fingerprint, "preserved", True))
            matched_original_ids.add(rid)
            restored_items.append(item)
            continue

        matched_original_id = original_by_fingerprint.get(fingerprint)
        if matched_original_id is not None and matched_original_id not in matched_original_ids:
            authorized = matched_original_id in requested_ids
            matched_original_ids.add(matched_original_id)
            if authorized:
                lineage.append(ItemLineage(matched_original_id, rid, fingerprint, "renamed", True))
                if rid is not None:
                    authorized_renames[matched_original_id] = rid
                restored_items.append(item)
            else:
                id_field = _identity_field_used(section, item) or _identity_field_used(
                    section, original_by_id[matched_original_id]
                )
                restored_item = dict(item)
                if id_field is not None:
                    restored_item[id_field] = matched_original_id
                lineage.append(ItemLineage(matched_original_id, rid, fingerprint, "renamed", False))
                restored_items.append(restored_item)
            continue

        # No id match, no content-fingerprint match against any unmatched
        # original item -- a genuinely new item. Always accepted (adding
        # content is never itself an unauthorized change); if its id
        # collides with something elsewhere in the document, that is a
        # separate, deterministic duplicate_id repair (see
        # se_documentation_structural_invariants.py), not a lineage concern.
        lineage.append(ItemLineage(None, rid, fingerprint, "added", True))
        restored_items.append(item)

    for oid, item in original_by_id.items():
        if oid in matched_original_ids:
            continue
        authorized = oid in requested_ids
        lineage.append(ItemLineage(oid, None, _fingerprint(section, item), "removed", authorized))
        if not authorized:
            # Unrequested removal -- restore the original item verbatim.
            restored_items.append(item)

    return SectionLineageResult(
        items=restored_items, lineage=lineage, authorized_renames=authorized_renames,
    )


def diff_sections_against_baseline(
    section: str, baseline_items: list[Any], current_items: list[Any],
) -> dict[str, str]:
    """
    Retroactive rename detection against a KNOWN-GOOD prior candidate (e.g.
    ReviewPipeline's own `state.last_structurally_valid_candidate`), used
    when reconciling a whole-document invariant failure discovered several
    pipeline iterations after whichever section rewrite actually introduced
    it -- by that point there is no live "requested_ids" context from the
    call that caused it (see compute_section_lineage's docstring for the
    live, request-scoped equivalent used immediately after a single rewrite
    call).

    Returns old_id -> new_id for every baseline item whose id disappeared
    from `current_items` while an item with matching content (fingerprint)
    appears under a different id -- the only explanation for that pairing is
    a rename, so unlike compute_section_lineage there is no authorization
    gate here: this function only ever REPORTS a detected rename for the
    caller to use in reference remapping, it never itself restores or
    rejects anything. An id with no fingerprint match anywhere in
    `current_items` is simply absent from the returned mapping (a genuine
    removal or an unrelated content change) -- never guessed.
    """
    baseline = [item for item in baseline_items if isinstance(item, dict)]
    current = [item for item in current_items if isinstance(item, dict)]

    current_ids = {_row_identity(section, item) for item in current if _row_identity(section, item)}
    current_by_fingerprint: dict[str, str] = {}
    for item in current:
        cid = _row_identity(section, item)
        if cid is not None:
            current_by_fingerprint.setdefault(_fingerprint(section, item), cid)

    renames: dict[str, str] = {}
    for item in baseline:
        bid = _row_identity(section, item)
        if bid is None or bid in current_ids:
            continue
        matched = current_by_fingerprint.get(_fingerprint(section, item))
        if matched is not None and matched != bid:
            renames[bid] = matched

    return renames


def added_ids(section: str, baseline_items: list[Any], current_items: list[Any]) -> frozenset[str]:
    """Ids present in `current_items` under this section's identity field
    that were NOT present in `baseline_items` at all -- used by duplicate-id
    reconciliation to know which occurrence of a collision this pipeline run
    itself just introduced, so only that one is ever renamed."""
    baseline_ids = {
        _row_identity(section, item) for item in baseline_items if isinstance(item, dict) and _row_identity(section, item)
    }
    current = {
        _row_identity(section, item) for item in current_items if isinstance(item, dict) and _row_identity(section, item)
    }
    return frozenset(current - baseline_ids)


def allocate_next_id(prefix: str, existing_ids: set[str], *, width: int = 2) -> str:
    """
    Deterministic next-available `{prefix}-{NN}` id, never reusing a number
    already present in `existing_ids` (case-insensitive) -- the ONLY
    sanctioned way a new id may be assigned by this module's callers (see
    se_documentation_structural_invariants.py's duplicate-id reconciliation);
    never a model-suggested id.
    """
    existing_upper = {existing_id.upper() for existing_id in existing_ids}
    n = 1
    while True:
        candidate = f"{prefix}-{n:0{width}d}"
        if candidate.upper() not in existing_upper:
            return candidate
        n += 1
