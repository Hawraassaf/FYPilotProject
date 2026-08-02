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
