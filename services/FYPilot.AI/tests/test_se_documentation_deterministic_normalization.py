"""
Tests proving each newly-diagnosed structural-invariant kind can be resolved
DETERMINISTICALLY -- zero provider calls -- via
se_documentation_deterministic_normalization.py's normalization, reused
directly (unit tests) and through the full ReviewPipeline (pipeline-level
tests, proving the wiring in pipeline.py's per-section/per-group loops and
_attempt_se_documentation_invariant_reconciliation actually applies it).

Numbered comments map to this task's required-tests list (items 9 and 10).
"""

from __future__ import annotations

from app.review.context import ReviewContext
from app.review.pipeline import ReviewPipeline, _PipelineState
from app.review.se_documentation_deterministic_normalization import (
    normalize_database_entities_dicts,
    rebuild_assumptions_disclosure,
)
from app.review.se_documentation_id_lineage import compute_section_lineage
from app.review.se_documentation_structural_invariants import (
    diagnose_structural_invariants,
    reconcile_deterministically,
)
from app.services.llm_provider import LLMResult


def _real_valid_candidate() -> dict:
    from app.agents.se_documentation.se_documentation_orchestrator import (
        SEDocSelectedIdea,
        SEDocumentationOrchestratorAgent,
        SEDocumentationRequest,
    )

    agent = SEDocumentationOrchestratorAgent()
    return agent.build_safe_fallback(
        SEDocumentationRequest(selectedIdea=SEDocSelectedIdea(title="Test Project"))
    ).model_dump()


def _llm_ok(data, provider="deepinfra", model="test-model"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


def _llm_fail(error="provider unavailable"):
    return LLMResult(ok=False, provider="none", model=None, text="", data=None, error=error)


class _RecordingRewriteAgent:
    def __init__(self):
        self.rewrite_targeted_calls = 0
        self.fix_structure_calls = 0
        self.fix_structure_scoped_calls = 0

    def rewrite_targeted(self, *args, **kwargs):
        self.rewrite_targeted_calls += 1
        return _llm_fail("must not be called for a deterministically resolvable issue")

    def fix_structure(self, *args, **kwargs):
        self.fix_structure_calls += 1
        return _llm_fail("full-document repair must remain blocked")

    def fix_structure_scoped(self, *args, **kwargs):
        self.fix_structure_scoped_calls += 1
        return _llm_fail("must not be called for a deterministically resolvable issue")


class _FakeReviewerAgentApprovesCleanly:
    def analyze(self, candidate, context, **kwargs):
        return _llm_ok({"strengths": [], "issues": [], "qualityScore": 95, "overallAssessment": "fine"})


def _se_doc_context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="Test context.",
        untrusted_user_input="",
    )


def _pipeline(rewrite_agent) -> ReviewPipeline:
    from app.review.registry import get_agent_config

    return ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgentApprovesCleanly(),
        rewrite_agent=rewrite_agent,
        config=get_agent_config("SEDocumentationAgent"),
    )


def _run_with_single_entity_violation(mutate_first_entity) -> tuple:
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    entities = [dict(e) for e in invalid_candidate["databaseEntities"]]
    entities[0] = dict(entities[0])
    entities[0]["fields"] = [dict(f) for f in entities[0]["fields"]]
    mutate_first_entity(entities[0])
    invalid_candidate["databaseEntities"] = entities

    rewrite_agent = _RecordingRewriteAgent()
    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )
    return result, rewrite_agent


def _assert_resolved_with_zero_llm_calls(result, rewrite_agent):
    assert rewrite_agent.rewrite_targeted_calls == 0
    assert rewrite_agent.fix_structure_calls == 0
    assert rewrite_agent.fix_structure_scoped_calls == 0
    assert result.status == "approved"
    assert result.usable


# ---------------------------------------------------------------------------
# 9: each newly diagnosed invariant resolved without any provider call
# ---------------------------------------------------------------------------

def test_empty_entity_fields_resolved_end_to_end_with_zero_llm_calls():
    def mutate(entity):
        entity["fields"] = []

    result, rewrite_agent = _run_with_single_entity_violation(mutate)
    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    fields = result.output["databaseEntities"][0]["fields"]
    assert len(fields) >= 3


def test_insufficient_entity_fields_resolved_end_to_end_with_zero_llm_calls():
    def mutate(entity):
        entity["fields"] = entity["fields"][:1]
        entity["foreignKeys"] = []  # not a junction table

    result, rewrite_agent = _run_with_single_entity_violation(mutate)
    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    assert len(result.output["databaseEntities"][0]["fields"]) >= 3


def test_missing_primary_key_resolved_end_to_end_with_zero_llm_calls():
    def mutate(entity):
        for field in entity["fields"]:
            field["isPrimaryKey"] = False

    result, rewrite_agent = _run_with_single_entity_violation(mutate)
    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    pk_fields = [f for f in result.output["databaseEntities"][0]["fields"] if f["isPrimaryKey"]]
    assert len(pk_fields) == 1


def test_multiple_primary_keys_resolved_end_to_end_with_zero_llm_calls():
    def mutate(entity):
        for field in entity["fields"][:2]:
            field["isPrimaryKey"] = True

    result, rewrite_agent = _run_with_single_entity_violation(mutate)
    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    pk_fields = [f for f in result.output["databaseEntities"][0]["fields"] if f["isPrimaryKey"]]
    assert len(pk_fields) == 1


def test_plaintext_password_field_resolved_end_to_end_with_zero_llm_calls():
    def mutate(entity):
        entity["fields"].append({
            "name": "Password", "dataType": "string", "nullable": False, "defaultValue": "",
            "description": "raw password", "constraints": "", "isSensitive": True,
            "isPrimaryKey": False, "isForeignKey": False, "referencedEntity": "", "referencedField": "",
        })

    result, rewrite_agent = _run_with_single_entity_violation(mutate)
    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    names = [f["name"] for f in result.output["databaseEntities"][0]["fields"]]
    assert "Password" not in names
    assert "PasswordHash" in names


def test_missing_assumption_disclosure_resolved_end_to_end_with_zero_llm_calls():
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    invalid_candidate["assumptions"] = []
    frs = [dict(fr) for fr in invalid_candidate["functionalRequirements"]]
    frs[0] = dict(frs[0])
    frs[0]["sourceClassification"] = "inferred"
    invalid_candidate["functionalRequirements"] = frs

    rewrite_agent = _RecordingRewriteAgent()
    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    _assert_resolved_with_zero_llm_calls(result, rewrite_agent)
    assert len(result.output["assumptions"]) >= 1


# ---------------------------------------------------------------------------
# Unit-level: normalize_database_entities_dicts / rebuild_assumptions_
# disclosure never fabricate a foreign-key target and never discard
# existing disclosure.
# ---------------------------------------------------------------------------

def test_normalization_drops_dangling_foreign_keys_never_fabricates_a_target():
    entities = [{
        "entityId": "ENT-01", "name": "Order", "purpose": "p",
        "fields": [
            {"name": "Id", "dataType": "int", "isPrimaryKey": True},
            {"name": "CustomerId", "dataType": "int", "isPrimaryKey": False},
            {"name": "CreatedAt", "dataType": "datetime", "isPrimaryKey": False},
        ],
        "foreignKeys": ["CustomerId -> Customer.Id"],  # Customer entity does not exist
    }]

    normalized = normalize_database_entities_dicts(entities)

    assert normalized[0]["foreignKeys"] == []


def test_assumptions_rebuild_preserves_every_existing_entry():
    candidate = {
        "assumptions": [{"item": "Pre-existing manual assumption.", "classification": "assumption"}],
        "functionalRequirements": [{"id": "FR-01", "title": "X", "sourceClassification": "confirmed"}],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    assert any(a["item"] == "Pre-existing manual assumption." for a in rebuilt)


def test_assumptions_rebuild_prunes_a_disclosure_referencing_a_renamed_entity():
    """
    Reproduces the live incident: an entity was renamed from 'ResponseFeedback'
    to 'PatientFeedback' during a rewrite, but the ORIGINAL disclosure
    (referencing the old name) was never updated. rebuild_assumptions_
    disclosure must replace it with the current name, not leave both.
    """
    candidate = {
        "assumptions": [
            {"item": "ENT-06: database entity 'ResponseFeedback' is classified as 'inferred' rather than confirmed.", "classification": "inferred"},
        ],
        "databaseEntities": [
            {"entityId": "ENT-06", "name": "PatientFeedback", "sourceClassification": "inferred"},
        ],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    texts = [a["item"] for a in rebuilt]
    assert not any("ResponseFeedback" in t for t in texts)
    assert any("PatientFeedback" in t for t in texts)
    assert len([t for t in texts if t.startswith("ENT-06:")]) == 1


def test_assumptions_rebuild_drops_a_disclosure_for_an_item_that_no_longer_exists():
    candidate = {
        "assumptions": [
            {"item": "ENT-99: database entity 'Ghost' is classified as 'inferred' rather than confirmed.", "classification": "inferred"},
        ],
        "databaseEntities": [],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    assert rebuilt == []


def test_assumptions_rebuild_drops_a_disclosure_once_the_item_becomes_confirmed():
    candidate = {
        "assumptions": [
            {"item": "FR-01: Submit data is classified as 'inferred' rather than confirmed.", "classification": "inferred"},
        ],
        "functionalRequirements": [
            {"id": "FR-01", "title": "Submit data", "sourceClassification": "confirmed"},
        ],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    assert rebuilt == []


def test_assumptions_rebuild_collapses_near_duplicate_phrasing_for_the_same_id():
    """
    Reproduces the live incident where the Writer's own phrasing and this
    module's phrasing for the SAME item differed slightly, producing two
    near-identical entries instead of one.
    """
    candidate = {
        "assumptions": [
            {"item": "UI-08: Model Performance Monitoring Page is classified as 'assumption' rather than confirmed.", "classification": "assumption"},
            {"item": "UI-08: UI screen 'Model Performance Monitoring Page' is classified as 'assumption' rather than confirmed.", "classification": "assumption"},
        ],
        "uiScreens": [
            {"screenId": "UI-08", "name": "Model Performance Monitoring Page", "sourceClassification": "assumption"},
        ],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    ui08_entries = [a for a in rebuilt if a["item"].startswith("UI-08:")]
    assert len(ui08_entries) == 1


def test_assumptions_rebuild_never_touches_free_form_entries_without_an_id_prefix():
    candidate = {
        "assumptions": [
            {"item": "This document was generated using deterministic fallback content.", "classification": "assumption"},
            {"item": "Some unresolved technical decision with no id prefix.", "classification": "unresolved"},
        ],
    }

    rebuilt = rebuild_assumptions_disclosure(candidate)

    assert rebuilt == candidate["assumptions"]


# ---------------------------------------------------------------------------
# 10: dev_module_named_as_screen never discards an otherwise-valid document
# ---------------------------------------------------------------------------

def test_dev_module_screen_name_is_restored_from_baseline_when_available():
    real_candidate = _real_valid_candidate()
    original_screen = dict(real_candidate["uiScreens"][0])

    corrupted_screen = dict(original_screen)
    corrupted_screen["name"] = "User Management Service Layer"  # dev-module-shaped

    issues = diagnose_structural_invariants({**real_candidate, "uiScreens": [corrupted_screen]})
    assert issues and issues[0].kind == "dev_module_named_as_screen"

    updated, remaining = reconcile_deterministically(
        {**real_candidate, "uiScreens": [corrupted_screen]},
        issues,
        baseline_screens_by_id={original_screen["screenId"]: original_screen},
    )

    assert remaining == []
    assert updated["uiScreens"][0]["name"] == original_screen["name"]


def test_dev_module_screen_name_without_a_usable_baseline_falls_through_to_targeted_correction_never_full_document():
    """
    No baseline screen available (e.g. first-ever candidate) -- must be left
    for the targeted uiScreens-only correction, never guessed, and must
    never fall back to a full-document LLM repair.
    """
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    screens = [dict(s) for s in invalid_candidate["uiScreens"]]
    screens[0] = dict(screens[0])
    screens[0]["name"] = "User Management Service Layer"
    invalid_candidate["uiScreens"] = screens

    fixed_screens = [dict(s) for s in screens]
    fixed_screens[0] = dict(fixed_screens[0])
    fixed_screens[0]["name"] = "User Management Dashboard"

    class _TargetedFixRewriteAgent(_RecordingRewriteAgent):
        def rewrite_targeted(self, candidate, closure, blocking_issues, context, *, agent_name, schema_cls, deadline=None, max_tokens_override=None):
            self.rewrite_targeted_calls += 1
            assert closure.allowed_sections == frozenset({"uiScreens"})  # uiScreens-ONLY, never the whole document
            return _llm_ok({"uiScreens": fixed_screens})

    rewrite_agent = _TargetedFixRewriteAgent()
    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.rewrite_targeted_calls == 1
    assert rewrite_agent.fix_structure_calls == 0  # full-document repair never used
    assert result.status == "approved"
    assert result.output["uiScreens"][0]["name"] == "User Management Dashboard"


def test_dev_module_screen_name_with_no_resolution_still_never_uses_full_document_repair():
    real_candidate = _real_valid_candidate()
    invalid_candidate = dict(real_candidate)
    screens = [dict(s) for s in invalid_candidate["uiScreens"]]
    screens[0] = dict(screens[0])
    screens[0]["name"] = "User Management Service Layer"
    invalid_candidate["uiScreens"] = screens

    rewrite_agent = _RecordingRewriteAgent()  # every call fails
    result = _pipeline(rewrite_agent).run(
        lambda: _llm_ok(invalid_candidate),
        _se_doc_context(),
        writer_trusted_parts={}, writer_untrusted_parts={},
    )

    assert rewrite_agent.fix_structure_calls == 0
    assert result.status == "schema_invalid"  # honest failure, not silently approved
    assert not result.usable
