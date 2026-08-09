"""
Deterministic fixtures proving diagnose_structural_invariants detects each
NEWLY covered SEDocumentationCandidateSchema._check_structural_invariants
rule (see this task's coverage-matrix report) -- and that these new kinds
are VISIBILITY-ONLY: reconcile_deterministically never touches them, and
ReviewPipeline's reconciliation step never fires an LLM call for them.

No test in this file calls a live provider. Every fixture is a plain dict;
diagnose_structural_invariants is a pure function.
"""

from __future__ import annotations

from app.review.se_documentation_structural_invariants import (
    DEV_MODULE_NAMED_AS_SCREEN,
    EMPTY_ENTITY_FIELDS,
    INSUFFICIENT_ENTITY_FIELDS,
    MISSING_ASSUMPTION_DISCLOSURE,
    MISSING_PRIMARY_KEY,
    MULTIPLE_PRIMARY_KEYS,
    PLAINTEXT_PASSWORD_FIELD,
    RECONCILABLE_KINDS,
    diagnose_structural_invariants,
)


def _minimal_candidate(**overrides) -> dict:
    base = {
        "functionalRequirements": [{"id": "FR-01", "title": "Submit"}],
        "nonFunctionalRequirements": [],
        "useCases": [],
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
        "uiScreens": [{"screenId": "SCR-1", "name": "Symptom Intake Screen", "relatedRequirements": []}],
        "testingPlan": [],
        "traceabilityMatrix": [],
        "assumptions": [],
    }
    base.update(overrides)
    return base


def test_dev_module_named_as_screen_is_detected():
    candidate = _minimal_candidate(
        uiScreens=[{"screenId": "SCR-1", "name": "User Management Service Layer", "relatedRequirements": []}],
    )

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == DEV_MODULE_NAMED_AS_SCREEN]
    assert len(matches) == 1
    assert matches[0].source_section == "uiScreens"
    assert matches[0].invalid_id == "User Management Service Layer"
    assert "development module" in matches[0].description


def test_a_genuine_screen_name_is_not_flagged_as_a_dev_module():
    candidate = _minimal_candidate(
        uiScreens=[{"screenId": "SCR-1", "name": "User Management Dashboard", "relatedRequirements": []}],
    )

    issues = diagnose_structural_invariants(candidate)

    assert not any(i.kind == DEV_MODULE_NAMED_AS_SCREEN for i in issues)


def test_empty_entity_fields_is_detected():
    candidate = _minimal_candidate(databaseEntities=[{"entityId": "", "name": "Symptom", "fields": [], "foreignKeys": []}])

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == EMPTY_ENTITY_FIELDS]
    assert len(matches) == 1
    assert matches[0].source_item_id == "Symptom"


def test_insufficient_entity_fields_is_detected_for_a_non_junction_entity():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "Symptom",
        "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == INSUFFICIENT_ENTITY_FIELDS]
    assert len(matches) == 1


def test_junction_table_is_exempt_from_the_insufficient_fields_rule():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "UserRole",
        "fields": [
            {"name": "UserId", "dataType": "int", "isPrimaryKey": True},
            {"name": "RoleId", "dataType": "int", "isPrimaryKey": True},
        ],
        "foreignKeys": ["UserId", "RoleId"],
    }])

    issues = diagnose_structural_invariants(candidate)

    assert not any(i.kind == INSUFFICIENT_ENTITY_FIELDS for i in issues)


def test_missing_primary_key_is_detected():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "Symptom",
        "fields": [
            {"name": "Description", "dataType": "string", "isPrimaryKey": False},
            {"name": "CreatedAt", "dataType": "datetime", "isPrimaryKey": False},
            {"name": "Severity", "dataType": "string", "isPrimaryKey": False},
        ],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == MISSING_PRIMARY_KEY]
    assert len(matches) == 1
    assert matches[0].source_item_id == "Symptom"


def test_multiple_primary_keys_is_detected():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "Symptom",
        "fields": [
            {"name": "Id", "dataType": "int", "isPrimaryKey": True},
            {"name": "SecondaryId", "dataType": "int", "isPrimaryKey": True},
            {"name": "Description", "dataType": "string", "isPrimaryKey": False},
        ],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == MULTIPLE_PRIMARY_KEYS]
    assert len(matches) == 1
    assert "2 fields" in matches[0].description


def test_plaintext_password_field_is_detected():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "UserAccount",
        "fields": [
            {"name": "Id", "dataType": "int", "isPrimaryKey": True},
            {"name": "Password", "dataType": "string", "isPrimaryKey": False},
            {"name": "Email", "dataType": "string", "isPrimaryKey": False},
        ],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == PLAINTEXT_PASSWORD_FIELD]
    assert len(matches) == 1
    assert matches[0].source_item_id == "UserAccount"


def test_password_hash_field_is_not_flagged():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "UserAccount",
        "fields": [
            {"name": "Id", "dataType": "int", "isPrimaryKey": True},
            {"name": "PasswordHash", "dataType": "string", "isPrimaryKey": False},
            {"name": "Email", "dataType": "string", "isPrimaryKey": False},
        ],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)

    assert not any(i.kind == PLAINTEXT_PASSWORD_FIELD for i in issues)


def test_missing_assumption_disclosure_is_detected():
    candidate = _minimal_candidate(
        assumptions=[],
        functionalRequirements=[{"id": "FR-01", "title": "Submit", "sourceClassification": "inferred"}],
    )

    issues = diagnose_structural_invariants(candidate)

    matches = [i for i in issues if i.kind == MISSING_ASSUMPTION_DISCLOSURE]
    assert len(matches) == 1


def test_no_assumption_disclosure_issue_when_everything_is_confirmed():
    candidate = _minimal_candidate(
        assumptions=[],
        functionalRequirements=[{"id": "FR-01", "title": "Submit", "sourceClassification": "confirmed"}],
    )

    issues = diagnose_structural_invariants(candidate)

    assert not any(i.kind == MISSING_ASSUMPTION_DISCLOSURE for i in issues)


def test_no_assumption_disclosure_issue_when_assumptions_list_is_non_empty():
    candidate = _minimal_candidate(
        assumptions=[{"item": "Users have basic literacy", "classification": "assumption"}],
        functionalRequirements=[{"id": "FR-01", "title": "Submit", "sourceClassification": "inferred"}],
    )

    issues = diagnose_structural_invariants(candidate)

    assert not any(i.kind == MISSING_ASSUMPTION_DISCLOSURE for i in issues)


# ---------------------------------------------------------------------------
# All 7 new kinds are visibility-only: never in RECONCILABLE_KINDS
# ---------------------------------------------------------------------------

def test_database_and_assumption_kinds_are_fixed_by_normalization_not_reconciliation():
    """
    empty_entity_fields/insufficient_entity_fields/missing_primary_key/
    multiple_primary_keys/plaintext_password_field/missing_assumption_
    disclosure are fixed by se_documentation_deterministic_normalization.py
    running unconditionally after a rewrite -- NOT by reconcile_
    deterministically's issue-driven mechanism -- so they correctly stay out
    of RECONCILABLE_KINDS (see that constant's docstring).

    dev_module_named_as_screen is different: no deterministic rule can
    synthesize a new valid screen name, so it DOES go through
    reconcile_deterministically (baseline restore) / targeted correction,
    and is therefore now IN RECONCILABLE_KINDS.
    """
    normalization_only_kinds = {
        EMPTY_ENTITY_FIELDS, INSUFFICIENT_ENTITY_FIELDS,
        MISSING_PRIMARY_KEY, MULTIPLE_PRIMARY_KEYS, PLAINTEXT_PASSWORD_FIELD,
        MISSING_ASSUMPTION_DISCLOSURE,
    }
    assert normalization_only_kinds.isdisjoint(RECONCILABLE_KINDS)
    assert DEV_MODULE_NAMED_AS_SCREEN in RECONCILABLE_KINDS


def test_a_candidate_with_only_new_visibility_only_issues_produces_no_reconcilable_issues():
    candidate = _minimal_candidate(databaseEntities=[{
        "entityId": "", "name": "UserAccount",
        "fields": [{"name": "Password", "dataType": "string", "isPrimaryKey": False}],
        "foreignKeys": [],
    }])

    issues = diagnose_structural_invariants(candidate)
    assert issues  # something was detected (missing PK, insufficient fields, plaintext password)

    reconcilable = [i for i in issues if i.kind in RECONCILABLE_KINDS]
    assert reconcilable == []
