"""
Tests for SEDocumentationOrchestratorAgent._fallback_relationships -- the
deterministic ER-relationship derivation used whenever the model's own
`entityRelationships` response is empty/invalid (see
SEDocumentationOrchestratorAgent._relationships_or_fallback).

Reproduces the live incident (2026-08-07): every entity's top-level
`foreignKeys` string list came back empty despite correct field-level FK
metadata (isForeignKey/referencedEntity/referencedField), so the OLD
implementation -- which only ever read the top-level list -- found nothing
and fabricated a relationship between the first two entities in the list
("Patient owns Administrator", no such relationship existed). This module
proves that fabrication path is gone and that field-level metadata is used
as a real, deterministic source.

No test in this file calls a live provider -- EntityDto/RelationshipDto
construction and _fallback_relationships are pure, in-process.
"""

from __future__ import annotations

from app.agents.se_documentation.se_documentation_orchestrator import (
    EntityDto,
    EntityFieldDto,
    SEDocumentationOrchestratorAgent,
)


def _entity(name: str, fields: list[EntityFieldDto], foreign_keys: list[str] | None = None) -> EntityDto:
    return EntityDto(entityId="", name=name, purpose=f"Stores {name} records.", fields=fields, foreignKeys=foreign_keys or [])


def _pk_field(name: str = "Id") -> EntityFieldDto:
    return EntityFieldDto(name=name, dataType="int", isPrimaryKey=True)


def _fk_field(name: str, referenced_entity: str, referenced_field: str = "Id") -> EntityFieldDto:
    return EntityFieldDto(
        name=name, dataType="int", isForeignKey=True,
        referencedEntity=referenced_entity, referencedField=referenced_field,
    )


def _relationships(entities: list[EntityDto]):
    return SEDocumentationOrchestratorAgent()._fallback_relationships(entities)


def _pairs(relationships) -> set[tuple[str, str]]:
    return {(r.fromEntity, r.toEntity) for r in relationships}


# ---------------------------------------------------------------------------
# 1: only top-level foreignKeys populated
# ---------------------------------------------------------------------------

def test_relationship_derived_from_top_level_foreign_keys_only():
    parent = _entity("Customer", [_pk_field()])
    child = _entity("Order", [_pk_field(), EntityFieldDto(name="CustomerId", dataType="int")], foreign_keys=["CustomerId -> Customer.Id"])

    relationships = _relationships([parent, child])

    assert _pairs(relationships) == {("Customer", "Order")}


# ---------------------------------------------------------------------------
# 2: only field-level FK metadata populated
# ---------------------------------------------------------------------------

def test_relationship_derived_from_field_level_metadata_only():
    parent = _entity("Customer", [_pk_field()])
    child = _entity("Order", [_pk_field(), _fk_field("CustomerId", "Customer")])  # foreignKeys list left empty

    relationships = _relationships([parent, child])

    assert _pairs(relationships) == {("Customer", "Order")}


# ---------------------------------------------------------------------------
# 3: both populated -> one deduplicated relationship
# ---------------------------------------------------------------------------

def test_relationship_present_in_both_sources_is_deduplicated():
    parent = _entity("Customer", [_pk_field()])
    child = _entity(
        "Order", [_pk_field(), _fk_field("CustomerId", "Customer")],
        foreign_keys=["CustomerId -> Customer.Id"],
    )

    relationships = _relationships([parent, child])

    matches = [r for r in relationships if r.fromEntity == "Customer" and r.toEntity == "Order"]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# 4: several valid FKs from one entity
# ---------------------------------------------------------------------------

def test_multiple_distinct_fks_from_one_entity_are_all_preserved():
    session = _entity("TriageSession", [_pk_field()])
    specialist = _entity("Specialist", [_pk_field()])
    model_version = _entity("NlpModelVersion", [_pk_field()])
    result = _entity("TriageResult", [
        _pk_field(),
        _fk_field("TriageSessionId", "TriageSession"),
        _fk_field("SpecialistId", "Specialist"),
        _fk_field("NlpModelVersionId", "NlpModelVersion"),
    ])

    relationships = _relationships([session, specialist, model_version, result])

    assert _pairs(relationships) == {
        ("TriageSession", "TriageResult"),
        ("Specialist", "TriageResult"),
        ("NlpModelVersion", "TriageResult"),
    }


# ---------------------------------------------------------------------------
# 5: dangling referencedEntity is rejected
# ---------------------------------------------------------------------------

def test_dangling_referenced_entity_is_rejected_not_fabricated():
    order = _entity("Order", [_pk_field(), _fk_field("CustomerId", "Customer")])  # "Customer" does not exist

    relationships = _relationships([order])

    assert relationships == []


# ---------------------------------------------------------------------------
# 6: invalid referencedField is rejected when field validation is possible
# ---------------------------------------------------------------------------

def test_invalid_referenced_field_is_rejected():
    customer = _entity("Customer", [_pk_field()])  # only has "Id"
    order = _entity("Order", [_pk_field(), _fk_field("CustomerId", "Customer", referenced_field="Uuid")])  # "Uuid" doesn't exist on Customer

    relationships = _relationships([customer, order])

    assert relationships == []


def test_omitted_referenced_field_is_not_treated_as_invalid():
    customer = _entity("Customer", [_pk_field()])
    order = _entity(
        "Order", [_pk_field(), EntityFieldDto(name="CustomerId", dataType="int")],
        foreign_keys=["CustomerId -> Customer"],  # no ".Field" part at all
    )

    relationships = _relationships([customer, order])

    assert _pairs(relationships) == {("Customer", "Order")}


# ---------------------------------------------------------------------------
# 7: no FK information returns zero relationships
# ---------------------------------------------------------------------------

def test_no_fk_information_anywhere_returns_empty_list():
    a = _entity("Alpha", [_pk_field()])
    b = _entity("Beta", [_pk_field()])

    relationships = _relationships([a, b])

    assert relationships == []


# ---------------------------------------------------------------------------
# 8: no relationship between entities[0] and entities[1] is fabricated
# ---------------------------------------------------------------------------

def test_first_two_entities_are_never_fabricated_into_a_relationship():
    patient = _entity("Patient", [_pk_field()])
    administrator = _entity("Administrator", [_pk_field()])  # no FK to/from Patient at all

    relationships = _relationships([patient, administrator])

    assert relationships == []
    assert ("Patient", "Administrator") not in _pairs(relationships)
    assert ("Administrator", "Patient") not in _pairs(relationships)


# ---------------------------------------------------------------------------
# 9: self-referencing FK remains valid
# ---------------------------------------------------------------------------

def test_self_referencing_fk_is_preserved():
    category = _entity("Category", [_pk_field(), _fk_field("ParentCategoryId", "Category")])

    relationships = _relationships([category])

    assert _pairs(relationships) == {("Category", "Category")}


# ---------------------------------------------------------------------------
# 10 & 11: medical-triage fixture derives the expected relationships; the
# ERD built from them contains no fabricated Patient/Administrator edge.
# ---------------------------------------------------------------------------

def _medical_triage_fixture() -> list[EntityDto]:
    return [
        _entity("Patient", [_pk_field()]),
        _entity("Administrator", [_pk_field()]),  # deliberately unrelated to Patient
        _entity("TriageSession", [_pk_field(), _fk_field("PatientId", "Patient")]),
        _entity("SymptomEntry", [_pk_field(), _fk_field("TriageSessionId", "TriageSession")]),
        _entity("Specialist", [_pk_field()]),
        _entity("NlpModelVersion", [_pk_field()]),
        _entity("TriageResult", [
            _pk_field(),
            _fk_field("TriageSessionId", "TriageSession"),
            _fk_field("SpecialistId", "Specialist"),
            _fk_field("NlpModelVersionId", "NlpModelVersion"),
        ]),
        _entity("SymptomCorpusRecord", [_pk_field(), _fk_field("SpecialistId", "Specialist")]),
        _entity("PatientFeedback", [_pk_field(), _fk_field("TriageResultId", "TriageResult")]),
    ]


def test_medical_triage_fixture_derives_the_expected_minimum_relationships():
    relationships = _relationships(_medical_triage_fixture())

    assert _pairs(relationships) == {
        ("Patient", "TriageSession"),
        ("TriageSession", "SymptomEntry"),
        ("TriageSession", "TriageResult"),
        ("Specialist", "TriageResult"),
        ("NlpModelVersion", "TriageResult"),
        ("Specialist", "SymptomCorpusRecord"),
        ("TriageResult", "PatientFeedback"),
    }


def test_medical_triage_fixture_erd_never_contains_the_fabricated_patient_administrator_edge():
    entities = _medical_triage_fixture()
    relationships = _relationships(entities)

    erd = SEDocumentationOrchestratorAgent()._build_erd(entities, relationships)

    assert "PATIENT ||--o{ ADMINISTRATOR : owns" not in erd
    assert "ADMINISTRATOR" not in erd  # never referenced by any real relationship
    assert "PATIENT ||--o{ TRIAGESESSION" in erd
