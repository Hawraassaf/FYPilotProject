"""
Tests for app/review/se_documentation_rewrite_scope.py -- the targeted
(payload-minimized) SE Documentation rewrite path added after a live retest
showed the OLD full-document rewrite request (~29,987 estimated tokens: the
complete candidate + complete context + "return the complete object")
timing out on DeepInfra, returning HTTP 413 from Groq (org TPM limit 12,000),
and timing out on Ollama -- see se_documentation_rewrite_scope.py's module
docstring and app/review/pipeline.py's SE-Documentation-only rewrite branch.

Numbered comments below map to the required-tests list from the fix
specification.
"""

from __future__ import annotations

import json

from app.review.context import ReviewContext
from app.review.models import ReviewerIssue
from app.review.rewrite_agent import RewriteAgent
from app.review.se_documentation_rewrite_scope import (
    RewriteClosure,
    ScopeResolutionError,
    build_compact_context_text,
    build_compact_rewrite_candidate,
    build_reference_fields,
    build_targeted_rewrite_prompt,
    estimate_tokens,
    merge_targeted_rewrite,
    resolve_rewrite_closure,
)


def _issue(field: str, description: str = "issue description", *, category: str = "quality") -> ReviewerIssue:
    return ReviewerIssue(
        severity="high",
        requiresCorrection=True,
        category=category,
        affectedField=field,
        description=description,
        revisionInstruction="Correct this using the supplied project context.",
    )


def _candidate(**overrides) -> dict:
    base = {
        "projectTitle": "Arabic Medical Symptom Triage Assistant",
        "projectOverview": "A vague overview that does not state objectives clearly.",
        "problemStatement": "Patients need guidance on symptom urgency.",
        "objectives": ["Provide triage guidance", "Support Arabic input"],
        "stakeholders": ["Patients", "System Administrator"],
        "scope": {"inScope": ["Symptom intake"], "outOfScope": ["Diagnosis"], "futureWork": []},
        "functionalRequirements": [
            {"id": "FR-1", "title": "Submit symptoms", "description": "d", "priority": "High", "source": "confirmed"},
            {"id": "FR-2", "title": "Manage users", "description": "d", "priority": "Medium", "source": "confirmed"},
        ],
        "nonFunctionalRequirements": [],
        "useCases": [
            {"id": "UC-1", "title": "Submit Symptoms", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-1"]},
            {"id": "UC-2", "title": "Manage Users", "actor": "Admin", "goal": "g", "relatedRequirements": ["FR-2"]},
        ],
        "edgeCases": [],
        "systemModules": [
            {"id": "MOD-1", "name": "Triage Engine", "responsibility": "r", "relatedRequirements": ["FR-1"]},
            {"id": "MOD-2", "name": "User Management", "responsibility": "r", "relatedRequirements": ["FR-2"]},
        ],
        "databaseEntities": [
            {"entityId": "", "name": "Symptom", "purpose": "p", "relatedRequirementIds": ["FR-1"]},
            {"entityId": "", "name": "ResponseFeedback", "purpose": "p", "relatedRequirementIds": []},
            {"entityId": "", "name": "Role", "purpose": "p", "relatedRequirementIds": []},
        ],
        "entityRelationships": [
            {"fromEntity": "Symptom", "toEntity": "ResponseFeedback", "type": "one-to-many", "description": "d"},
        ],
        "mermaidERD": "erDiagram\n  SYMPTOM ||--o{ FEEDBACK : has",
        "mermaidClassDiagram": "classDiagram\n  class Symptom",
        "activityDiagram": "flowchart TD\n  A --> B",
        "sequenceDiagram": "sequenceDiagram\n  Patient->>System: submit",
        "architecture": {
            "style": "layered", "frontend": "Razor Pages", "backend": "ASP.NET Core",
            "database": "PostgreSQL", "aiService": "Python FastAPI + NLP model",
            "externalServices": [], "explanation": "Layered architecture.",
        },
        "apiIntegrationPoints": [
            {"name": "Submit Symptoms", "method": "POST", "endpoint": "/api/symptoms", "purpose": "p",
             "requestSummary": "r", "responseSummary": "r", "relatedRequirements": ["FR-1"]},
        ],
        "uiScreens": [
            {"screenId": "SCR-1", "name": "Symptom Intake", "authorizedRoles": ["Patient"], "relatedRequirements": ["FR-1"]},
            {"screenId": "SCR-2", "name": "User Management", "authorizedRoles": ["Admin"], "relatedRequirements": ["FR-2"]},
        ],
        "securityAndPrivacy": [{"category": "Privacy", "requirement": "Encrypt PII"}],
        "testingPlan": [
            {"id": "TC-1", "title": "Submit symptom test", "type": "functional", "steps": [], "expectedResult": "e", "relatedRequirements": ["FR-1"]},
            {"id": "TC-2", "title": "Manage users test", "type": "functional", "steps": [], "expectedResult": "e", "relatedRequirements": ["FR-2"]},
        ],
        "traceabilityMatrix": [
            {"requirementId": "FR-1", "useCaseId": "UC-1", "moduleId": "MOD-1", "entity": "Symptom", "testCaseId": "TC-1"},
            {"requirementId": "FR-2", "useCaseId": "UC-2", "moduleId": "MOD-2", "entity": "", "testCaseId": "TC-2"},
        ],
        "risksAndLimitations": ["Limited to known symptoms"],
        "expectedOutcomes": ["Faster triage"],
        "assumptions": [{"item": "Users have basic literacy", "classification": "assumption"}],
        "aiTechnicalReport": {
            "problemDefinition": "Classify symptom urgency.",
            "modelOrApproach": "Rule-based classifier, no trained model is used.",
        },
        "aiTechnicalReportApplicable": True,
        "documentationQualityScore": 80,
        "qualityAssessment": {"overallScore": 80, "criterionScores": {"completeness": 80}},
        "consistencyWarnings": [],
        "sectionProvenance": {"requirements": "provider"},
    }
    base.update(overrides)
    return base


def _context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="SEDocumentationAgent test context.",
        trusted_structural_context={"teamSize": 2, "experienceLevel": "intermediate"},
        untrusted_project_text={
            "ideaTitle": "Arabic Medical Symptom Triage Assistant",
            "problemStatement": "Patients need guidance.",
            "targetUsers": "Patients",
            "domain": "Healthcare",
            "requiredTechnologies": "ASP.NET Core, Python FastAPI, PostgreSQL",
            "roadmapSummary": "Phase 1: Build triage engine.",
            "existingNotes": "",
        },
    )


# ---------------------------------------------------------------------------
# 1-2: projectOverview finding -> payload contains projectOverview only;
# unrelated sections absent.
# ---------------------------------------------------------------------------

def test_project_overview_finding_scopes_to_projectOverview_only():
    candidate = _candidate()
    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "Overview is vague.")])

    assert closure.allowed_sections == {"projectOverview"}

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert set(payload.keys()) == {"projectOverview"}
    for unrelated in (
        "functionalRequirements", "databaseEntities", "uiScreens", "testingPlan",
        "traceabilityMatrix", "aiTechnicalReport", "mermaidERD", "sequenceDiagram",
    ):
        assert unrelated not in payload


# ---------------------------------------------------------------------------
# 3: database finding includes only database and its direct dependencies
# (diagrams are never a rewrite target; only traceabilityMatrix is
# row-filtered, per the confirmed policy).
# ---------------------------------------------------------------------------

def test_database_entity_finding_scopes_to_database_and_filtered_traceability():
    candidate = _candidate()
    issue = _issue(
        "databaseEntities",
        "Entities 'ResponseFeedback' and 'Role' have only filler placeholder fields.",
        category="quality",
    )
    closure = resolve_rewrite_closure(candidate, [issue])

    assert closure.allowed_sections == {"databaseEntities", "entityRelationships", "traceabilityMatrix"}
    # databaseEntities/entityRelationships are NOT row-scoped -- included whole.
    assert "databaseEntities" not in closure.row_scoped_sections
    assert "entityRelationships" not in closure.row_scoped_sections
    assert closure.seed_entity_names == {"ResponseFeedback", "Role"}

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert len(payload["databaseEntities"]) == 3
    assert len(payload["entityRelationships"]) == 1
    for unrelated in ("functionalRequirements", "useCases", "uiScreens", "testingPlan", "aiTechnicalReport"):
        assert unrelated not in payload


def test_database_finding_row_filters_traceability_to_referencing_rows_only():
    candidate = _candidate()
    # Make the FR-2 traceability row reference "ResponseFeedback" so we can
    # prove filtering keeps it and drops the FR-1/"Symptom" row.
    candidate["traceabilityMatrix"][1]["entity"] = "ResponseFeedback"

    issue = _issue("databaseEntities", "'ResponseFeedback' has only filler placeholder fields.", category="quality")
    closure = resolve_rewrite_closure(candidate, [issue])

    payload = build_compact_rewrite_candidate(candidate, closure)
    trace_rows = payload["traceabilityMatrix"]
    assert len(trace_rows) == 1
    assert trace_rows[0]["requirementId"] == "FR-2"
    assert trace_rows[0]["_rowKey"] == "1"


# ---------------------------------------------------------------------------
# 4: requirement finding includes only items referencing the affected
# requirement id(s).
# ---------------------------------------------------------------------------

def test_functional_requirement_finding_scopes_to_referencing_rows_only():
    candidate = _candidate()
    issue = _issue("functionalRequirements", "FR-1 lacks a measurable acceptance criterion.")
    closure = resolve_rewrite_closure(candidate, [issue])

    assert closure.allowed_sections == {
        "functionalRequirements", "useCases", "testingPlan", "systemModules",
        "uiScreens", "databaseEntities", "traceabilityMatrix",
    }
    assert closure.row_scoped_sections["functionalRequirements"] == frozenset({"FR-1"})

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert [fr["id"] for fr in payload["functionalRequirements"]] == ["FR-1"]
    assert [uc["id"] for uc in payload["useCases"]] == ["UC-1"]
    assert [tc["id"] for tc in payload["testingPlan"]] == ["TC-1"]
    assert [m["id"] for m in payload["systemModules"]] == ["MOD-1"]
    assert [s["screenId"] for s in payload["uiScreens"]] == ["SCR-1"]
    assert [e["name"] for e in payload["databaseEntities"]] == ["Symptom"]
    assert len(payload["traceabilityMatrix"]) == 1
    assert payload["traceabilityMatrix"][0]["requirementId"] == "FR-1"

    for unrelated in ("aiTechnicalReport", "architecture"):
        assert unrelated not in payload


# ---------------------------------------------------------------------------
# 5: AI contradiction includes architecture, aiTechnicalReport, and
# assumptions, but not unrelated sections.
# ---------------------------------------------------------------------------

def test_ai_contradiction_finding_scopes_to_architecture_report_and_assumptions():
    candidate = _candidate()
    issue = _issue(
        "aiTechnicalReport",
        "The AI report claims a rule-based approach, but architecture describes a trained NLP model.",
        category="contradiction",
    )
    closure = resolve_rewrite_closure(candidate, [issue])

    assert closure.allowed_sections == {"aiTechnicalReport", "architecture", "assumptions"}

    payload = build_compact_rewrite_candidate(candidate, closure)
    assert set(payload.keys()) == {"aiTechnicalReport", "architecture", "assumptions"}
    for unrelated in ("functionalRequirements", "databaseEntities", "uiScreens", "testingPlan", "traceabilityMatrix"):
        assert unrelated not in payload


# ---------------------------------------------------------------------------
# 6: unknown/unsafe scope fails closed -- never falls back to the full
# document.
# ---------------------------------------------------------------------------

def test_unresolvable_affected_field_raises_scope_resolution_error_not_full_document():
    candidate = _candidate()
    issue = _issue("totallyUnknownField", "Something is wrong somewhere.")

    try:
        resolve_rewrite_closure(candidate, [issue])
        assert False, "expected ScopeResolutionError"
    except ScopeResolutionError:
        pass


def test_no_blocking_issues_raises_scope_resolution_error():
    candidate = _candidate()
    try:
        resolve_rewrite_closure(candidate, [])
        assert False, "expected ScopeResolutionError"
    except ScopeResolutionError:
        pass


# ---------------------------------------------------------------------------
# 7: rewrite response contains only allowed sections (built prompt instructs
# this, and the payload itself only ever contains allowed keys).
# ---------------------------------------------------------------------------

def test_prompt_allowed_sections_list_matches_closure_exactly():
    candidate = _candidate()
    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "vague")])
    compact_candidate = build_compact_rewrite_candidate(candidate, closure)
    reference_fields = build_reference_fields(candidate, closure)
    context_text = build_compact_context_text(_context(), closure.primary_sections)

    prompt = build_targeted_rewrite_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=compact_candidate,
        reference_fields=reference_fields,
        blocking_issues=[_issue("projectOverview", "vague")],
        closure=closure,
        compact_context_text=context_text,
        trusted_structural_context={},
        trusted_system_instructions="",
        schema_fragment={},
    )

    assert 'ALLOWED SECTIONS TO RETURN (authoritative DATA -- return ONLY these top-level keys):\n["projectOverview"]' in prompt


# ---------------------------------------------------------------------------
# 8: nonallowed fields are restored unchanged after merge.
# ---------------------------------------------------------------------------

def test_merge_restores_every_nonallowed_field_unchanged():
    candidate = _candidate()
    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "vague")])

    partial_response = {"projectOverview": "A precise, corrected overview."}
    merged = merge_targeted_rewrite(candidate, partial_response, closure)

    assert merged["projectOverview"] == "A precise, corrected overview."
    for key, value in candidate.items():
        if key == "projectOverview":
            continue
        assert merged[key] == value, f"{key} was changed but was not in scope"


def test_merge_upserts_row_scoped_sections_without_dropping_unsent_rows():
    candidate = _candidate()
    issue = _issue("functionalRequirements", "FR-1 lacks a measurable acceptance criterion.")
    closure = resolve_rewrite_closure(candidate, [issue])

    partial_response = {
        "functionalRequirements": [
            {"id": "FR-1", "title": "Submit symptoms", "description": "corrected", "priority": "High", "source": "confirmed"},
        ],
        # useCases only returns the one row it actually saw (UC-1) -- UC-2
        # must survive the merge even though it was never sent.
        "useCases": [
            {"id": "UC-1", "title": "Submit Symptoms (revised)", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-1"]},
        ],
    }
    merged = merge_targeted_rewrite(candidate, partial_response, closure)

    assert [fr["id"] for fr in merged["functionalRequirements"]] == ["FR-1", "FR-2"]
    assert merged["functionalRequirements"][0]["description"] == "corrected"
    assert merged["functionalRequirements"][1] == candidate["functionalRequirements"][1]

    assert [uc["id"] for uc in merged["useCases"]] == ["UC-1", "UC-2"]
    assert merged["useCases"][0]["title"] == "Submit Symptoms (revised)"
    assert merged["useCases"][1] == candidate["useCases"][1]


def test_merge_upserts_traceability_rows_by_row_key_without_dropping_others():
    candidate = _candidate()
    candidate["traceabilityMatrix"][1]["entity"] = "ResponseFeedback"
    issue = _issue("databaseEntities", "'ResponseFeedback' has only filler placeholder fields.", category="quality")
    closure = resolve_rewrite_closure(candidate, [issue])

    partial_response = {
        "databaseEntities": candidate["databaseEntities"],
        "entityRelationships": candidate["entityRelationships"],
        "traceabilityMatrix": [
            {"requirementId": "FR-2", "useCaseId": "UC-2", "moduleId": "MOD-2", "entity": "ResponseFeedback",
             "testCaseId": "TC-2", "coverageStatus": "covered", "_rowKey": "1"},
        ],
    }
    merged = merge_targeted_rewrite(candidate, partial_response, closure)

    assert len(merged["traceabilityMatrix"]) == 2
    assert merged["traceabilityMatrix"][0] == candidate["traceabilityMatrix"][0]
    assert merged["traceabilityMatrix"][1]["coverageStatus"] == "covered"
    assert "_rowKey" not in merged["traceabilityMatrix"][1]


# ---------------------------------------------------------------------------
# 9: deterministic score/Mermaid/provenance fields can never be overwritten.
# ---------------------------------------------------------------------------

def test_never_rewritable_fields_survive_a_malicious_partial_response():
    candidate = _candidate()
    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "vague")])

    partial_response = {
        "projectOverview": "corrected",
        "documentationQualityScore": 100,
        "qualityAssessment": {"overallScore": 100, "criterionScores": {}},
        "mermaidERD": "erDiagram\n  HACKED",
        "mermaidClassDiagram": "classDiagram\n  HACKED",
        "activityDiagram": "flowchart TD\n  HACKED",
        "sequenceDiagram": "sequenceDiagram\n  HACKED",
    }
    merged = merge_targeted_rewrite(candidate, partial_response, closure)

    assert merged["documentationQualityScore"] == candidate["documentationQualityScore"]
    assert merged["qualityAssessment"] == candidate["qualityAssessment"]
    assert merged["mermaidERD"] == candidate["mermaidERD"]
    assert merged["mermaidClassDiagram"] == candidate["mermaidClassDiagram"]
    assert merged["activityDiagram"] == candidate["activityDiagram"]
    assert merged["sequenceDiagram"] == candidate["sequenceDiagram"]


# ---------------------------------------------------------------------------
# 10: merged candidate is fully schema-validated (uses the REAL
# SEDocumentationDto, not a hand-rolled test schema).
# ---------------------------------------------------------------------------

def test_merged_candidate_passes_real_se_documentation_schema_validation():
    from app.agents.se_documentation.se_documentation_orchestrator import (
        SEDocSelectedIdea,
        SEDocumentationOrchestratorAgent,
        SEDocumentationRequest,
    )
    from app.review.schema_validation import validate_detailed
    from app.review.registry import get_agent_config

    agent = SEDocumentationOrchestratorAgent()
    real_candidate = agent.build_safe_fallback(
        SEDocumentationRequest(selectedIdea=SEDocSelectedIdea(title="Test Project"))
    ).model_dump()

    closure = resolve_rewrite_closure(real_candidate, [_issue("projectOverview", "vague")])
    partial_response = {"projectOverview": "A precise, corrected overview of the test project."}
    merged = merge_targeted_rewrite(real_candidate, partial_response, closure)

    schema = get_agent_config("SEDocumentationAgent").schema
    result = validate_detailed(schema, merged)
    assert result.valid, result.errors


# ---------------------------------------------------------------------------
# 15/regression: estimator sanity + the live-failure regression itself.
# ---------------------------------------------------------------------------

def test_projectOverview_scenario_estimated_tokens_below_groq_limit():
    candidate = _candidate()
    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "vague")])
    compact_candidate = build_compact_rewrite_candidate(candidate, closure)
    reference_fields = build_reference_fields(candidate, closure)
    context_text = build_compact_context_text(_context(), closure.primary_sections)

    prompt = build_targeted_rewrite_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=compact_candidate,
        reference_fields=reference_fields,
        blocking_issues=[_issue("projectOverview", "vague")],
        closure=closure,
        compact_context_text=context_text,
        trusted_structural_context=_context().trusted_structural_context,
        trusted_system_instructions=_context().trusted_system_instructions,
        schema_fragment={},
    )

    assert estimate_tokens(prompt) < 12000


def test_regression_large_document_projectOverview_rewrite_fits_groq_limit():
    """
    Regression test for the live failure (2026-08-04, ProjectIdeaId 128):
    a full-document rewrite request estimated at ~29,987 tokens timed out on
    DeepInfra, got HTTP 413 from Groq (org 12,000 TPM limit), and timed out
    on Ollama. Builds an intentionally large candidate (many items per
    section, long text) and proves the OLD full-document estimate exceeds
    the Groq limit while the NEW targeted payload for a projectOverview
    finding does not.
    """
    long_text = "Detailed content. " * 200  # ~3,600 chars per field

    def _big_requirement(i: int) -> dict:
        return {
            "id": f"FR-{i}", "title": f"Requirement {i}", "description": long_text,
            "priority": "High", "source": "confirmed", "rationale": long_text,
            "acceptanceCriteria": [long_text] * 3,
        }

    def _big_usecase(i: int) -> dict:
        return {
            "id": f"UC-{i}", "title": f"Use Case {i}", "actor": "Patient", "goal": long_text,
            "mainFlow": [long_text] * 5, "relatedRequirements": [f"FR-{i}"],
        }

    candidate = _candidate(
        functionalRequirements=[_big_requirement(i) for i in range(20)],
        useCases=[_big_usecase(i) for i in range(20)],
        databaseEntities=[
            {"entityId": "", "name": f"Entity{i}", "purpose": long_text, "relatedRequirementIds": []}
            for i in range(20)
        ],
        testingPlan=[
            {"id": f"TC-{i}", "title": f"Test {i}", "type": "functional", "steps": [long_text] * 3,
             "expectedResult": long_text, "relatedRequirements": [f"FR-{i}"]}
            for i in range(20)
        ],
    )

    old_full_document_estimate = estimate_tokens(candidate)
    assert old_full_document_estimate > 12000, (
        "fixture must reproduce a payload large enough to exceed Groq's TPM limit"
    )

    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "The overview is vague.")])
    compact_candidate = build_compact_rewrite_candidate(candidate, closure)
    reference_fields = build_reference_fields(candidate, closure)
    context_text = build_compact_context_text(_context(), closure.primary_sections)

    prompt = build_targeted_rewrite_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=compact_candidate,
        reference_fields=reference_fields,
        blocking_issues=[_issue("projectOverview", "The overview is vague.")],
        closure=closure,
        compact_context_text=context_text,
        trusted_structural_context=_context().trusted_structural_context,
        trusted_system_instructions=_context().trusted_system_instructions,
        schema_fragment={},
    )
    new_targeted_estimate = estimate_tokens(prompt)

    assert new_targeted_estimate < old_full_document_estimate
    assert new_targeted_estimate < 12000

    # One scoped rewrite merges cleanly and leaves the document schema-shaped.
    merged = merge_targeted_rewrite(candidate, {"projectOverview": "Corrected, concise overview."}, closure)
    assert merged["projectOverview"] == "Corrected, concise overview."
    assert len(merged["functionalRequirements"]) == 20  # untouched, none lost


# ---------------------------------------------------------------------------
# 17: no full-document serialization occurs in the SE targeted rewrite path.
# ---------------------------------------------------------------------------

def test_targeted_prompt_never_serializes_excluded_section_content():
    candidate = _candidate()
    candidate["testingPlan"][0]["title"] = "SENTINEL_ONLY_IN_TESTING_PLAN"

    closure = resolve_rewrite_closure(candidate, [_issue("projectOverview", "vague")])
    compact_candidate = build_compact_rewrite_candidate(candidate, closure)
    reference_fields = build_reference_fields(candidate, closure)
    context_text = build_compact_context_text(_context(), closure.primary_sections)

    prompt = build_targeted_rewrite_prompt(
        agent_name="SEDocumentationAgent",
        compact_candidate=compact_candidate,
        reference_fields=reference_fields,
        blocking_issues=[_issue("projectOverview", "vague")],
        closure=closure,
        compact_context_text=context_text,
        trusted_structural_context={},
        trusted_system_instructions="",
        schema_fragment={},
    )

    assert "SENTINEL_ONLY_IN_TESTING_PLAN" not in prompt
    assert "testingPlan" not in json.dumps(compact_candidate)


# ---------------------------------------------------------------------------
# 16: other agents keep the existing full-object rewrite path untouched.
# ---------------------------------------------------------------------------

def test_non_se_documentation_agent_rewrite_prompt_still_sends_complete_candidate():
    class _RecordingChain:
        def __init__(self):
            self.last_prompt = ""

        def generate_json(self, prompt, **kwargs):
            self.last_prompt = prompt
            from app.services.llm_provider import LLMResult
            return LLMResult(ok=True, provider="test", model="test", text="", data={"reply": "ok"})

    chain = _RecordingChain()
    agent = RewriteAgent(chain)  # type: ignore[arg-type]

    candidate = {"reply": "old answer", "unrelatedSentinel": "SENTINEL_MUST_BE_PRESENT"}
    issue = ReviewerIssue(
        severity="high", requiresCorrection=True, category="contradiction",
        affectedField="reply", description="wrong", revisionInstruction="fix it",
    )
    agent.rewrite(candidate, [issue], _context(), agent_name="MentorChatAgent")

    assert "SENTINEL_MUST_BE_PRESENT" in chain.last_prompt
    assert "Return the COMPLETE object" in chain.last_prompt
