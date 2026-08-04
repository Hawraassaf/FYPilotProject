from __future__ import annotations

from app.review.context import ReviewContext
from app.review.models import ReviewerIssue
from app.review.rewrite_agent import RewriteAgent
from app.review.section_scope import apply_scoped_rewrite, revision_scope_for


def _issue(field: str) -> ReviewerIssue:
    return ReviewerIssue(
        severity="high",
        requiresCorrection=True,
        category="contradiction",
        affectedField=field,
        description="The section is inconsistent with the target project.",
        revisionInstruction="Correct this section using the supplied project context.",
    )


def _candidate() -> dict:
    return {
        "projectTitle": "Arabic Medical Symptom Triage Assistant",
        "functionalRequirements": [{"id": "FR-01", "title": "Submit symptoms"}],
        "databaseEntities": [{"entityId": "ENT-01", "name": "SymptomSubmission"}],
        "databaseRelationships": [],
        "traceabilityMatrix": [{"requirementId": "FR-01", "entityIds": ["ENT-01"]}],
        "diagramSpecifications": {"erDiagram": "old"},
        "architecture": {"style": "layered"},
        "assumptions": ["Prototype only"],
        "expectedOutcomes": ["Safer guidance"],
    }


def _candidate_with_protected_fields() -> dict:
    """Mirrors the real SEDocumentationDto's protected field names -- unlike
    _candidate() above (an older simplified fixture that predates those exact
    names), this one exercises the actual "never LLM-rewritable" guard."""
    return {
        **_candidate(),
        "documentationQualityScore": 82,
        "qualityAssessment": {"overallScore": 82, "criterionScores": {"completeness": 90}},
        "mermaidERD": "erDiagram\n  SYMPTOM ||--o{ SUBMISSION : has",
        "mermaidClassDiagram": "classDiagram\n  class SymptomSubmission",
        "activityDiagram": "flowchart TD\n  A --> B",
        "sequenceDiagram": "sequenceDiagram\n  Patient->>System: submit",
    }


def test_nested_database_issue_expands_to_only_known_dependent_sections() -> None:
    candidate = _candidate()

    scope = revision_scope_for(
        "SEDocumentationAgent",
        candidate,
        [_issue("databaseEntities[0].fields")],
    )

    assert "databaseEntities" in scope
    assert "databaseRelationships" in scope
    assert "traceabilityMatrix" in scope
    assert "diagramSpecifications" in scope
    assert "architecture" in scope
    assert "assumptions" in scope
    assert "functionalRequirements" not in scope
    assert "projectTitle" not in scope
    assert "expectedOutcomes" not in scope


def test_scoped_merge_restores_every_unaffected_top_level_section() -> None:
    original = _candidate()
    rewritten = {
        **original,
        "projectTitle": "FYPilot Documentation Generator",
        "functionalRequirements": [{"id": "FR-99", "title": "Generate documentation"}],
        "databaseEntities": [{"entityId": "ENT-01", "name": "MedicalCase"}],
        "traceabilityMatrix": [{"requirementId": "FR-01", "entityIds": ["ENT-01"]}],
        "diagramSpecifications": {"erDiagram": "corrected"},
        "architecture": {"style": "layered with FastAPI"},
        "expectedOutcomes": ["Changed by the model but not allowed"],
    }

    merged, scope = apply_scoped_rewrite(
        "SEDocumentationAgent",
        original,
        rewritten,
        [_issue("Database Design")],
    )

    assert "databaseEntities" in scope
    assert merged["databaseEntities"] == rewritten["databaseEntities"]
    assert merged["diagramSpecifications"] == rewritten["diagramSpecifications"]
    assert merged["projectTitle"] == original["projectTitle"]
    assert merged["functionalRequirements"] == original["functionalRequirements"]
    assert merged["expectedOutcomes"] == original["expectedOutcomes"]


def test_unknown_or_global_issue_allows_complete_object_rewrite() -> None:
    original = _candidate()
    rewritten = {**original, "projectTitle": "Corrected complete project"}

    merged, scope = apply_scoped_rewrite(
        "SEDocumentationAgent",
        original,
        rewritten,
        [_issue("entire document")],
    )

    assert scope == set(original.keys())
    assert merged == rewritten


def test_quality_score_and_mermaid_fields_are_never_in_scope_for_a_matched_issue() -> None:
    candidate = _candidate_with_protected_fields()

    scope = revision_scope_for(
        "SEDocumentationAgent",
        candidate,
        [_issue("databaseEntities[0].fields")],
    )

    assert "documentationQualityScore" not in scope
    assert "qualityAssessment" not in scope
    assert "mermaidERD" not in scope
    assert "mermaidClassDiagram" not in scope
    assert "activityDiagram" not in scope
    assert "sequenceDiagram" not in scope


def test_quality_score_and_mermaid_fields_are_never_in_scope_for_an_unmatched_global_issue() -> None:
    """The exact gap this guards against: an unknown/global affectedField
    used to fall back to "every top-level field", which -- for a real
    candidate -- included documentationQualityScore/qualityAssessment/the
    four mermaid fields, letting a Rewrite LLM call silently overwrite
    platform-computed values it was never supposed to touch."""
    candidate = _candidate_with_protected_fields()

    scope = revision_scope_for(
        "SEDocumentationAgent",
        candidate,
        [_issue("entire document")],
    )

    assert "documentationQualityScore" not in scope
    assert "qualityAssessment" not in scope
    assert "mermaidERD" not in scope
    assert "mermaidClassDiagram" not in scope
    assert "activityDiagram" not in scope
    assert "sequenceDiagram" not in scope
    # Every other top-level field is still eligible, unchanged from before.
    assert "projectTitle" in scope
    assert "functionalRequirements" in scope


def test_apply_scoped_rewrite_preserves_protected_fields_even_when_the_llm_hallucinates_new_ones() -> None:
    original = _candidate_with_protected_fields()
    rewritten = {
        **original,
        "projectTitle": "Corrected complete project",
        # A rewrite LLM call returns a complete object per its schema -- if
        # it hallucinates a different score/diagram, that value must never
        # reach the merged candidate.
        "documentationQualityScore": 100,
        "qualityAssessment": {"overallScore": 100, "criterionScores": {}},
        "mermaidERD": "erDiagram\n  HALLUCINATED ||--o{ ENTITY : has",
    }

    merged, scope = apply_scoped_rewrite(
        "SEDocumentationAgent",
        original,
        rewritten,
        [_issue("entire document")],
    )

    assert merged["documentationQualityScore"] == original["documentationQualityScore"]
    assert merged["qualityAssessment"] == original["qualityAssessment"]
    assert merged["mermaidERD"] == original["mermaidERD"]
    assert merged["projectTitle"] == rewritten["projectTitle"]


def test_non_se_agent_keeps_existing_complete_rewrite_behavior() -> None:
    original = {"summary": "old", "score": 70}
    rewritten = {"summary": "new", "score": 80}

    merged, scope = apply_scoped_rewrite(
        "ProjectDNAAgent",
        original,
        rewritten,
        [_issue("summary")],
    )

    assert scope == set(original.keys())
    assert merged == rewritten


class FakeProviderChain:
    def __init__(self) -> None:
        self.prompt = ""

    def generate_json(self, prompt: str, *, use_search: bool = False):
        self.prompt = prompt
        return {"ok": True}


def test_rewrite_prompt_lists_authoritative_allowed_sections() -> None:
    chain = FakeProviderChain()
    agent = RewriteAgent(chain)  # type: ignore[arg-type]
    candidate = _candidate()

    agent.rewrite(
        candidate,
        [_issue("databaseEntities[0].fields")],
        ReviewContext(agent_name="SEDocumentationAgent"),
        agent_name="SEDocumentationAgent",
    )

    assert "ALLOWED TOP-LEVEL SECTIONS TO CHANGE" in chain.prompt
    assert '"databaseEntities"' in chain.prompt
    assert '"traceabilityMatrix"' in chain.prompt
    assert "The pipeline will deterministically restore those sections" in chain.prompt
