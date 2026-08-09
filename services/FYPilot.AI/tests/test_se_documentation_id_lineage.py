"""
Tests for app/review/se_documentation_id_lineage.py -- stable-ID lineage
tracking used to restore unauthorized ID renames/removals a section rewrite
introduces, and to retroactively detect authorized renames against the last
known-good candidate so dangling references elsewhere can be remapped.

Numbered comments map to the required-tests list from the fix specification.
"""

from __future__ import annotations

from app.review.se_documentation_id_lineage import (
    allocate_next_id,
    added_ids,
    compute_section_lineage,
    diff_sections_against_baseline,
)


def _fr(id_, title="Submit data", description="d", priority="High"):
    return {"id": id_, "title": title, "description": description, "priority": priority}


# ---------------------------------------------------------------------------
# 1: section rewrite preserves IDs
# ---------------------------------------------------------------------------

def test_identical_items_are_all_preserved():
    original = [_fr("FR-01"), _fr("FR-02")]
    rewritten = [_fr("FR-01"), _fr("FR-02")]

    result = compute_section_lineage("functionalRequirements", original, rewritten)

    assert [i.change for i in result.lineage] == ["preserved", "preserved"]
    assert all(i.authorized for i in result.lineage)
    assert result.items == rewritten
    assert result.authorized_renames == {}


# ---------------------------------------------------------------------------
# 2: unauthorized ID rename is restored or rejected
# ---------------------------------------------------------------------------

def test_unauthorized_rename_is_restored_to_the_original_id():
    original = [_fr("FR-01", title="Submit data")]
    rewritten = [_fr("FR-99", title="Submit data")]  # same content, different id, not requested

    result = compute_section_lineage("functionalRequirements", original, rewritten, requested_ids=frozenset())

    assert result.lineage[0].change == "renamed"
    assert result.lineage[0].authorized is False
    assert result.items[0]["id"] == "FR-01"  # restored
    assert result.authorized_renames == {}


# ---------------------------------------------------------------------------
# 3: unexpected item deletion is restored or rejected
# ---------------------------------------------------------------------------

def test_unexpected_item_removal_is_restored():
    original = [_fr("FR-01"), _fr("FR-02")]
    rewritten = [_fr("FR-01")]  # FR-02 silently dropped, not requested

    result = compute_section_lineage("functionalRequirements", original, rewritten, requested_ids=frozenset())

    removal = [i for i in result.lineage if i.change == "removed"][0]
    assert removal.authorized is False
    assert any(item["id"] == "FR-02" for item in result.items)  # restored


def test_requested_item_removal_is_authorized_and_not_restored():
    original = [_fr("FR-01"), _fr("FR-02")]
    rewritten = [_fr("FR-01")]

    result = compute_section_lineage(
        "functionalRequirements", original, rewritten, requested_ids=frozenset({"FR-02"}),
    )

    removal = [i for i in result.lineage if i.change == "removed"][0]
    assert removal.authorized is True
    assert not any(item["id"] == "FR-02" for item in result.items)


# ---------------------------------------------------------------------------
# 4: authorized rename creates an old-to-new mapping
# ---------------------------------------------------------------------------

def test_authorized_rename_creates_old_to_new_mapping():
    original = [_fr("FR-01", title="Submit data")]
    rewritten = [_fr("FR-05", title="Submit data")]

    result = compute_section_lineage(
        "functionalRequirements", original, rewritten, requested_ids=frozenset({"FR-01"}),
    )

    assert result.lineage[0].change == "renamed"
    assert result.lineage[0].authorized is True
    assert result.authorized_renames == {"FR-01": "FR-05"}
    assert result.items[0]["id"] == "FR-05"  # rewritten id kept, since authorized


# ---------------------------------------------------------------------------
# 7: dangling reference caused by an authorized rename is remapped
# (baseline-diff variant used retroactively by the pipeline)
# ---------------------------------------------------------------------------

def test_diff_against_baseline_detects_rename_by_content_fingerprint():
    baseline = [_fr("FR-01", title="Submit patient symptoms")]
    current = [_fr("FR-07", title="Submit patient symptoms")]  # same content, new id

    renames = diff_sections_against_baseline("functionalRequirements", baseline, current)

    assert renames == {"FR-01": "FR-07"}


# ---------------------------------------------------------------------------
# 8 & 9: genuinely ambiguous dangling reference remains unresolved; no
# invalid reference is ever replaced with the first available id
# ---------------------------------------------------------------------------

def test_diff_against_baseline_reports_nothing_for_a_genuine_unrelated_removal():
    baseline = [_fr("FR-01", title="Submit patient symptoms"), _fr("FR-02", title="Manage users")]
    current = [_fr("FR-02", title="Manage users")]  # FR-01 just gone, nothing resembles it

    renames = diff_sections_against_baseline("functionalRequirements", baseline, current)

    assert renames == {}


def test_diff_against_baseline_never_maps_to_an_arbitrary_first_available_id():
    baseline = [_fr("FR-01", title="Submit patient symptoms")]
    # Two unrelated new items appear -- neither fingerprint-matches FR-01, so
    # neither may ever be guessed as its replacement.
    current = [_fr("FR-03", title="Completely different feature"), _fr("FR-04", title="Another feature")]

    renames = diff_sections_against_baseline("functionalRequirements", baseline, current)

    assert renames == {}


# ---------------------------------------------------------------------------
# added_ids / allocate_next_id -- support functions for duplicate-id
# reconciliation (see test_se_documentation_structural_invariants.py)
# ---------------------------------------------------------------------------

def test_added_ids_reports_only_ids_new_since_baseline():
    baseline = [_fr("FR-01")]
    current = [_fr("FR-01"), _fr("FR-02")]

    assert added_ids("functionalRequirements", baseline, current) == frozenset({"FR-02"})


def test_allocate_next_id_never_reuses_an_existing_number():
    assert allocate_next_id("FR", {"FR-01", "FR-02"}) == "FR-03"
    assert allocate_next_id("ENT", set()) == "ENT-01"
    # Case-insensitive collision avoidance.
    assert allocate_next_id("FR", {"fr-01"}) == "FR-02"
