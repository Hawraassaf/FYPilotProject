"""
Tests for app/review/se_documentation_structural_invariants.py -- structured
(not string-parsed) diagnostics for SEDocumentationCandidateSchema.
_check_structural_invariants' cross-section checks, deterministic-first
reconciliation, minimal-affected-section resolution, and deterministic
rebuild of the generated-derivative fields (traceabilityMatrix, mermaidERD).

Numbered comments map to the required-tests list from the fix specification.
"""

from __future__ import annotations

from app.review.se_documentation_structural_invariants import (
    DANGLING_REFERENCE,
    DUPLICATE_ENTITY_NAME,
    DUPLICATE_ID,
    MISSING_RELATIONSHIP_ENDPOINT,
    diagnose_structural_invariants,
    reconcile_deterministically,
    rebuild_mermaid_erd,
    rebuild_traceability_matrix,
    resolve_minimal_affected_sections,
)


def _base_candidate(**overrides) -> dict:
    base = {
        "functionalRequirements": [{"id": "FR-01", "title": "Submit symptoms"}],
        "nonFunctionalRequirements": [],
        "useCases": [{"id": "UC-01", "title": "Submit", "relatedRequirements": ["FR-01"]}],
        "edgeCases": [],
        "systemModules": [],
        "databaseEntities": [{
            "entityId": "", "name": "Symptom",
            "fields": [
                {"name": "Id", "dataType": "int", "isPrimaryKey": True},
                {"name": "Description", "dataType": "string", "isPrimaryKey": False},
                {"name": "CreatedAt", "dataType": "datetime", "isPrimaryKey": False},
            ],
            "foreignKeys": [],
        }],
        "entityRelationships": [],
        "apiIntegrationPoints": [],
        "uiScreens": [],
        "testingPlan": [{"id": "TC-01", "title": "Submit test", "relatedRequirements": ["FR-01"]}],
        "traceabilityMatrix": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 10: root $ error emits structured issue details
# ---------------------------------------------------------------------------

def test_duplicate_id_issue_carries_structured_details():
    candidate = _base_candidate(
        functionalRequirements=[{"id": "FR-01", "title": "A"}, {"id": "FR-01", "title": "B"}],
    )

    issues = diagnose_structural_invariants(candidate)
    duplicate = [i for i in issues if i.kind == DUPLICATE_ID][0]

    assert duplicate.source_section == "functionalRequirements"
    assert duplicate.invalid_id == "FR-01"
    assert duplicate.indices == (0, 1)
    assert duplicate.suggested_sections == frozenset({"functionalRequirements"})


def test_dangling_reference_issue_carries_structured_details():
    candidate = _base_candidate(
        testingPlan=[{"id": "TC-01", "title": "t", "relatedRequirements": ["FR-99"]}],
    )

    issues = diagnose_structural_invariants(candidate)
    dangling = [i for i in issues if i.kind == DANGLING_REFERENCE][0]

    assert dangling.source_section == "testingPlan"
    assert dangling.source_item_id == "TC-01"
    assert dangling.source_field == "relatedRequirements"
    assert dangling.invalid_id == "FR-99"
    assert dangling.target_section == "functionalRequirements/nonFunctionalRequirements"


def test_no_issues_found_for_a_fully_consistent_candidate():
    assert diagnose_structural_invariants(_base_candidate()) == []


# ---------------------------------------------------------------------------
# 11 & 12: minimal section closure resolution
# ---------------------------------------------------------------------------

def test_requirement_test_mismatch_resolves_to_requirements_and_testing_only():
    candidate = _base_candidate(
        testingPlan=[{"id": "TC-01", "title": "t", "relatedRequirements": ["FR-99"]}],
    )
    issues = diagnose_structural_invariants(candidate)

    closure = resolve_minimal_affected_sections(issues)

    assert closure == frozenset({"functionalRequirements", "nonFunctionalRequirements", "testingPlan"})


def test_entity_relationship_mismatch_resolves_to_database_sections_only():
    candidate = _base_candidate(
        entityRelationships=[{"fromEntity": "Symptom", "toEntity": "Ghost", "type": "one-to-many"}],
    )
    issues = diagnose_structural_invariants(candidate)

    closure = resolve_minimal_affected_sections(issues)

    assert closure == frozenset({"databaseEntities", "entityRelationships"})


# ---------------------------------------------------------------------------
# 13 & 14: overlapping issues combine into one minimal closure; unrelated
# sections are never included
# ---------------------------------------------------------------------------

def test_overlapping_issues_combine_into_one_minimal_closure_without_unrelated_sections():
    candidate = _base_candidate(
        testingPlan=[{"id": "TC-01", "title": "t", "relatedRequirements": ["FR-99"]}],
        useCases=[{"id": "UC-01", "title": "Submit", "relatedRequirements": ["FR-99"]}],
    )
    issues = diagnose_structural_invariants(candidate)

    closure = resolve_minimal_affected_sections(issues)

    assert closure == frozenset({
        "functionalRequirements", "nonFunctionalRequirements", "testingPlan", "useCases",
    })
    assert "uiScreens" not in closure
    assert "databaseEntities" not in closure
    assert "entityRelationships" not in closure


# ---------------------------------------------------------------------------
# 5 & 6: duplicate ids/entity names resolved deterministically
# ---------------------------------------------------------------------------

def test_duplicate_requirement_id_is_resolved_deterministically_when_marked_as_added():
    candidate = _base_candidate(
        functionalRequirements=[{"id": "FR-01", "title": "Original"}, {"id": "FR-01", "title": "New one this pass"}],
    )
    issues = diagnose_structural_invariants(candidate)

    updated, remaining = reconcile_deterministically(
        candidate, issues,
        added_ids_by_section={"functionalRequirements": frozenset({"FR-01"})},
    )

    assert remaining == []
    ids = [item["id"] for item in updated["functionalRequirements"]]
    assert ids.count("FR-01") == 1
    assert "FR-02" in ids  # newly allocated canonical id for the added/conflicting item


def test_duplicate_entity_name_is_resolved_deterministically_when_marked_as_added():
    _entity_fields = [
        {"name": "Id", "dataType": "int", "isPrimaryKey": True},
        {"name": "Description", "dataType": "string", "isPrimaryKey": False},
        {"name": "CreatedAt", "dataType": "datetime", "isPrimaryKey": False},
    ]
    candidate = _base_candidate(
        databaseEntities=[
            {"entityId": "", "name": "Symptom", "fields": _entity_fields, "foreignKeys": []},
            {"entityId": "", "name": "Symptom", "fields": _entity_fields, "foreignKeys": []},
        ],
    )
    issues = diagnose_structural_invariants(candidate)
    assert any(i.kind == DUPLICATE_ENTITY_NAME for i in issues)

    updated, remaining = reconcile_deterministically(
        candidate, issues,
        added_ids_by_section={"databaseEntities": frozenset({"Symptom"})},
    )

    assert remaining == []
    names = [e["name"] for e in updated["databaseEntities"]]
    assert names.count("Symptom") == 1
    assert "ENT-01" in names


def test_ambiguous_duplicate_is_left_unresolved_not_guessed():
    candidate = _base_candidate(
        functionalRequirements=[{"id": "FR-01", "title": "A"}, {"id": "FR-01", "title": "B"}],
    )
    issues = diagnose_structural_invariants(candidate)

    # No added_ids_by_section supplied -- neither occurrence is known to be
    # "this pass's own addition", so nothing may be renamed.
    updated, remaining = reconcile_deterministically(candidate, issues)

    assert remaining == issues
    assert updated["functionalRequirements"] == candidate["functionalRequirements"]


def test_dangling_reference_is_remapped_via_supplied_rename_map():
    candidate = _base_candidate(
        testingPlan=[{"id": "TC-01", "title": "t", "relatedRequirements": ["FR-01"]}],
        functionalRequirements=[{"id": "FR-07", "title": "Submit symptoms"}],  # renamed from FR-01
    )
    issues = diagnose_structural_invariants(candidate)
    assert any(i.kind == DANGLING_REFERENCE for i in issues)

    updated, remaining = reconcile_deterministically(
        candidate, issues,
        rename_maps_by_section={"functionalRequirements": {"FR-01": "FR-07"}},
    )

    assert remaining == []
    assert updated["testingPlan"][0]["relatedRequirements"] == ["FR-07"]


# ---------------------------------------------------------------------------
# 15 & 16: derivative rebuild after reconciliation
# ---------------------------------------------------------------------------

def test_traceability_matrix_is_rebuilt_from_reconciled_sections():
    candidate = _base_candidate(
        functionalRequirements=[{"id": "FR-01", "title": "Submit"}],
        useCases=[{"id": "UC-01", "title": "Submit", "relatedRequirements": ["FR-01"]}],
        testingPlan=[{"id": "TC-01", "title": "t", "relatedRequirements": ["FR-01"]}],
    )

    rows = rebuild_traceability_matrix(candidate)

    assert len(rows) == 1
    assert rows[0]["requirementId"] == "FR-01"
    assert rows[0]["useCaseIds"] == ["UC-01"]
    assert rows[0]["testCaseIds"] == ["TC-01"]
    assert rows[0]["coverageStatus"] == "covered"


def test_mermaid_erd_is_rebuilt_after_database_changes():
    candidate = _base_candidate(
        databaseEntities=[{"entityId": "", "name": "Symptom"}, {"entityId": "", "name": "Feedback"}],
        entityRelationships=[{"fromEntity": "Symptom", "toEntity": "Feedback", "type": "one-to-many"}],
    )

    erd = rebuild_mermaid_erd(candidate)

    assert "SYMPTOM" in erd
    assert "FEEDBACK" in erd
    assert erd.startswith("erDiagram")


def test_mermaid_erd_excludes_relationships_with_a_missing_endpoint():
    candidate = _base_candidate(
        databaseEntities=[{"entityId": "", "name": "Symptom"}],
        entityRelationships=[{"fromEntity": "Symptom", "toEntity": "Ghost", "type": "one-to-many"}],
    )

    erd = rebuild_mermaid_erd(candidate)

    assert "GHOST" not in erd
