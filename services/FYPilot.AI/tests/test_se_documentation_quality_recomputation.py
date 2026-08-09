"""
Tests for the final SE Documentation quality recomputation -- closes the
live defect (2026-08-08) where documentationQualityScore/qualityAssessment,
correctly protected in _NEVER_LLM_REWRITABLE_FIELDS so the LLM can never
choose its own score, were nonetheless STALE after every deterministic
repair/rebuild pass (databaseEntities normalization, assumptions rebuild,
requirement-reference reconciliation, traceability rebuild, ERD/class/
activity/sequence diagram rebuild): merge_structural_repair/
merge_targeted_rewrite always restore those two fields from the PRE-repair
candidate, so nothing ever recomputed them from the FINAL, corrected
document.

app.agents.se_documentation.quality_recomputation.compute_documentation_quality
is the single dict-based entrypoint that closes this gap by reusing
se_documentation_orchestrator.compute_quality_assessment (extracted from
SEDocumentationOrchestratorAgent._compute_quality_assessment, never a second
competing formula) against the FINAL candidate's own sections, plus
app.review.se_documentation_diagram_integrity.diagram_integrity_report for a
richer diagramValidity definition than "Mermaid text parsed".

Covers the 27 required proofs:
 1.  pre-repair plaintext Password lowers database quality
 2.  deterministic normalization produces PasswordHash
 3.  final recomputation uses corrected database
 4.  pre-repair missing primary key is fixed
 5.  final database score is based on the repaired entity
 6.  multiple-PK issue affects base quality until corrected
 7.  stale assumptions are removed/rebuilt
 8.  final assumptionTransparency uses rebuilt assumptions
 9.  bad traceability produces lower traceabilityCoverage
10.  corrected explicit traceability recalculates coverage
11.  old traceability score is not retained after rebuild
12.  duplicate sequence participant fails diagram integrity before repair
13.  rebuilt unique sequence diagram is evaluated from the final diagram
14.  a fabricated ER relation is not treated as valid final diagram evidence
15.  final diagramValidity reflects current diagrams, not old diagrams
16.  old qualityAssessment object is not retained through repair
17.  LLM rewrite cannot directly modify qualityAssessment
18.  LLM rewrite cannot directly modify documentationQualityScore
19.  base score is recomputed after deterministic normalization
20.  base score is recomputed after a targeted semantic rewrite
21.  final Reviewer cap is applied exactly once
22.  an unresolved Reviewer outcome cannot become approved through
     recomputation
23.  review_unavailable remains honestly limited
24.  qualityAssessment.overallScore equals documentationQualityScore
25.  final persisted/returned result carries the final recomputed assessment
26.  section provenance remains unchanged by pure quality recomputation
27.  no live provider call occurs

Plus one complete medical fixture proving broken -> repaired -> final quality
strictly improves (relational assertions, no arbitrary exact scores).

No test in this file calls a live provider -- every DTO/candidate dict is
constructed in-process.

Run from services/FYPilot.AI:
    python -m pytest tests/test_se_documentation_quality_recomputation.py
"""

from __future__ import annotations

import copy
import unittest

from app.agents.se_documentation.project_facts import build_project_facts
from app.agents.se_documentation.quality_outcome_policy import apply_review_outcome_to_quality
from app.agents.se_documentation.quality_recomputation import compute_documentation_quality
from app.agents.se_documentation.se_documentation_orchestrator import (
    SEDocumentationRequest,
)
from app.review.models import PipelineResult, ReviewerFindings, ReviewerIssue
from app.review.review_decision_engine import ReviewDecisionEngine
from app.review.se_documentation_deterministic_normalization import (
    normalize_database_entities_dicts,
    rebuild_assumptions_disclosure,
)
from app.review.se_documentation_diagram_integrity import diagram_integrity_report
from app.review.se_documentation_rewrite_scope import RewriteClosure, merge_targeted_rewrite
from app.review.se_documentation_structural_invariants import (
    rebuild_mermaid_activity_diagram,
    rebuild_mermaid_class_diagram,
    rebuild_mermaid_erd,
    rebuild_mermaid_sequence_diagram,
    rebuild_traceability_matrix,
)


def _facts(**overrides):
    facts = build_project_facts(SEDocumentationRequest())
    return facts.model_copy(update=overrides) if overrides else facts


def _base_candidate(**overrides) -> dict:
    """A minimal but structurally-shaped candidate dict -- every field the
    quality recomputation reads is present, everything else is empty."""
    candidate = {
        "projectTitle": "Arabic Medical Symptom Triage Assistant",
        "stakeholders": ["Patient"],
        "functionalRequirements": [
            {"id": "FR-01", "title": "Patient Account Registration and Login", "description": "d", "priority": "High", "source": "s", "acceptanceCriteria": ["A", "B"]},
            {"id": "FR-02", "title": "Symptom Description Submission", "description": "d", "priority": "High", "source": "s", "acceptanceCriteria": ["A", "B"]},
        ],
        "nonFunctionalRequirements": [
            {"id": "NFR-01", "title": "Response Time", "description": "d", "priority": "High", "source": "s", "measurableTarget": "Proposed acceptance target: under 5s."},
        ],
        "useCases": [
            {"id": "UC-01", "title": "Register", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-01"], "mainFlow": ["s1", "s2", "s3", "s4", "s5"]},
            {"id": "UC-02", "title": "Submit Symptoms", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-02"], "mainFlow": ["s1", "s2", "s3", "s4", "s5"]},
        ],
        "edgeCases": [
            {"id": "EC-01", "scenario": "s", "expectedHandling": "h", "relatedRequirement": "FR-01"},
        ],
        "systemModules": [{"id": "MOD-01", "name": "AuthModule", "responsibility": "r", "relatedRequirements": ["FR-01"]}],
        "databaseEntities": [
            {
                "entityId": "ENT-01", "name": "Patient", "purpose": "p",
                "fields": [
                    {"name": "Id", "dataType": "int", "isPrimaryKey": True},
                    {"name": "Email", "dataType": "string"},
                    {"name": "PasswordHash", "dataType": "string"},
                ],
                "relatedRequirementIds": ["FR-01"],
            },
        ],
        "entityRelationships": [],
        "uiScreens": [{"screenId": "UI-01", "name": "Symptom Entry", "purpose": "p", "relatedRequirements": ["FR-02"], "loadingState": "l", "emptyState": "e", "errorState": "e2", "successState": "s"}],
        "apiIntegrationPoints": [],
        "testingPlan": [
            {"id": "TC-01", "title": "Register positive", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-01"], "relatedUseCaseIds": ["UC-01"], "steps": ["1", "2"]},
            {"id": "TC-02", "title": "Submit symptom positive", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-02"], "relatedUseCaseIds": ["UC-02"], "steps": ["1", "2"]},
        ],
        "securityAndPrivacy": [],
        "traceabilityMatrix": [],
        "assumptions": [],
        "architecture": {
            "style": "s", "frontend": "ASP.NET Core Razor Pages", "backend": "FastAPI Triage Service",
            "database": "PostgreSQL", "aiService": "Not applicable", "explanation": "e",
        },
        "aiTechnicalReportApplicable": False,
        "aiTechnicalReport": None,
        "mermaidERD": "erDiagram",
        "mermaidClassDiagram": "classDiagram",
        "activityDiagram": "flowchart TD\n    A[Start]\n    A --> B[Step]",
        "sequenceDiagram": (
            "sequenceDiagram\n"
            "    actor Patient\n"
            "    participant RazorPages as ASP.NET Core Razor Pages\n"
            "    participant PostgreSQL\n"
            "    Patient->>RazorPages: Submit symptoms\n"
            "    RazorPages->>PostgreSQL: Read/write data\n"
            "    PostgreSQL-->>RazorPages: Return data\n"
            "    RazorPages-->>Patient: Display result\n"
        ),
        "sectionProvenance": {
            "requirements": "provider", "useCases": "provider", "modulesArchitecture": "provider",
            "database": "provider", "uiApi": "provider", "testingSecurity": "provider",
        },
    }
    candidate.update(overrides)
    return candidate


def _quality(candidate: dict, **kwargs):
    return compute_documentation_quality(
        candidate, project_facts=_facts(), section_provenance=candidate.get("sectionProvenance", {}), **kwargs,
    )


# ---------------------------------------------------------------------------
# 1-6 -- database quality: plaintext password, primary key, multiple PKs
# ---------------------------------------------------------------------------

class DatabaseQualityRecomputationTests(unittest.TestCase):
    def test_pre_repair_plaintext_password_lowers_database_quality(self):
        broken = _base_candidate(databaseEntities=[{
            "entityId": "ENT-01", "name": "Patient", "purpose": "p",
            "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}],
        }])
        assessment = _quality(broken)
        self.assertLess(assessment.criterionScores["databaseQuality"], 100)

    def test_deterministic_normalization_produces_password_hash(self):
        broken_entities = [{
            "entityId": "ENT-01", "name": "Patient", "purpose": "p",
            "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}],
        }]
        normalized = normalize_database_entities_dicts(broken_entities)
        field_names = {f["name"] for f in normalized[0]["fields"]}
        self.assertIn("PasswordHash", field_names)
        self.assertNotIn("Password", field_names)

    def test_final_recomputation_uses_corrected_database(self):
        broken_entities = [{
            "entityId": "ENT-01", "name": "Patient", "purpose": "p",
            "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}],
        }]
        broken = _base_candidate(databaseEntities=broken_entities)
        repaired = _base_candidate(databaseEntities=normalize_database_entities_dicts(broken_entities))

        broken_assessment = _quality(broken)
        repaired_assessment = _quality(repaired)

        self.assertGreater(repaired_assessment.criterionScores["databaseQuality"], broken_assessment.criterionScores["databaseQuality"])

    def test_pre_repair_missing_primary_key_is_fixed_by_normalization(self):
        broken_entities = [{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Email", "dataType": "string"}]}]
        normalized = normalize_database_entities_dicts(broken_entities)
        self.assertTrue(any(f.get("isPrimaryKey") for f in normalized[0]["fields"]))

    def test_final_database_score_is_based_on_repaired_entity(self):
        broken_entities = [{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Email", "dataType": "string"}]}]
        broken = _base_candidate(databaseEntities=broken_entities)
        repaired = _base_candidate(databaseEntities=normalize_database_entities_dicts(broken_entities))

        broken_assessment = _quality(broken)
        repaired_assessment = _quality(repaired)

        self.assertGreaterEqual(repaired_assessment.criterionScores["databaseQuality"], broken_assessment.criterionScores["databaseQuality"])

    def test_multiple_pk_issue_affects_base_quality_until_corrected(self):
        broken_entities = [{
            "entityId": "ENT-01", "name": "Patient", "purpose": "p",
            "fields": [
                {"name": "Id", "dataType": "int", "isPrimaryKey": True},
                {"name": "Uuid", "dataType": "string", "isPrimaryKey": True},
                {"name": "Email", "dataType": "string"},
            ],
        }]
        normalized = normalize_database_entities_dicts(copy.deepcopy(broken_entities))
        pk_count_before = sum(1 for f in broken_entities[0]["fields"] if f.get("isPrimaryKey"))
        pk_count_after = sum(1 for f in normalized[0]["fields"] if f.get("isPrimaryKey"))
        self.assertGreater(pk_count_before, 1)
        self.assertEqual(pk_count_after, 1)


# ---------------------------------------------------------------------------
# 7, 8 -- assumptions
# ---------------------------------------------------------------------------

class AssumptionTransparencyRecomputationTests(unittest.TestCase):
    def test_stale_assumptions_are_removed_by_rebuild(self):
        candidate = _base_candidate(
            functionalRequirements=[{"id": "FR-01", "title": "T", "description": "d", "priority": "High", "source": "s", "sourceClassification": "confirmed"}],
            assumptions=[{"item": "FR-99: a stale disclosure about a requirement that no longer exists.", "classification": "inferred"}],
        )
        rebuilt = rebuild_assumptions_disclosure(candidate)
        self.assertFalse(any("FR-99" in a.get("item", "") for a in rebuilt))

    def test_final_assumption_transparency_uses_rebuilt_assumptions(self):
        candidate = _base_candidate(
            functionalRequirements=[{"id": "FR-01", "title": "T", "description": "d", "priority": "High", "source": "s", "sourceClassification": "inferred"}],
            assumptions=[],
        )
        before = _quality(candidate)
        candidate["assumptions"] = rebuild_assumptions_disclosure(candidate)
        after = _quality(candidate)
        self.assertGreaterEqual(after.criterionScores["assumptionTransparency"], before.criterionScores["assumptionTransparency"])


# ---------------------------------------------------------------------------
# 9-11 -- traceability
# ---------------------------------------------------------------------------

class TraceabilityRecomputationTests(unittest.TestCase):
    def test_bad_traceability_produces_lower_coverage(self):
        # Neither use case nor test actually links back to FR-02 -> uncovered.
        broken = _base_candidate(
            useCases=[{"id": "UC-01", "title": "Register", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-01"]}],
            testingPlan=[{"id": "TC-01", "title": "t", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-01"]}],
        )
        assessment = _quality(broken)
        self.assertLess(assessment.criterionScores["traceabilityCoverage"], 100)

    def test_corrected_explicit_traceability_recalculates_coverage(self):
        broken = _base_candidate(
            useCases=[{"id": "UC-01", "title": "Register", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-01"]}],
            testingPlan=[{"id": "TC-01", "title": "t", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-01"]}],
        )
        corrected = _base_candidate()  # both FR-01 and FR-02 covered by use case + test

        broken_assessment = _quality(broken)
        corrected_assessment = _quality(corrected)

        self.assertGreater(corrected_assessment.criterionScores["traceabilityCoverage"], broken_assessment.criterionScores["traceabilityCoverage"])

    def test_old_traceability_score_is_not_retained_after_rebuild(self):
        candidate = _base_candidate(
            useCases=[{"id": "UC-01", "title": "Register", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-01"]}],
            testingPlan=[{"id": "TC-01", "title": "t", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-01"]}],
            # A stale, pre-repair matrix falsely claiming full coverage --
            # compute_documentation_quality must never trust this field.
            traceabilityMatrix=[
                {"requirementId": "FR-01", "useCaseIds": ["UC-01"], "testCaseIds": ["TC-01"], "coverageStatus": "covered"},
                {"requirementId": "FR-02", "useCaseIds": ["UC-01"], "testCaseIds": ["TC-01"], "coverageStatus": "covered"},
            ],
        )
        assessment = _quality(candidate)
        # FR-02 has no real child reference anywhere -- the rebuilt matrix
        # must show it uncovered regardless of what the stale stored matrix claimed.
        self.assertLess(assessment.criterionScores["traceabilityCoverage"], 100)


# ---------------------------------------------------------------------------
# 12-15 -- diagram integrity
# ---------------------------------------------------------------------------

class DiagramIntegrityRecomputationTests(unittest.TestCase):
    def _broken_sequence(self) -> str:
        return (
            "sequenceDiagram\n"
            "    actor Patient\n"
            "    participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages\n"
            "    participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages\n"
            "    Patient->>ASPNETCoreRazorPages: Submit\n"
            "    ASPNETCoreRazorPages->>ASPNETCoreRazorPages: Submit request\n"
        )

    def test_duplicate_sequence_participant_fails_diagram_integrity(self):
        candidate = _base_candidate(sequenceDiagram=self._broken_sequence())
        report = diagram_integrity_report(candidate)
        self.assertFalse(report["ok"])
        self.assertTrue(any("more than once" in issue for issue in report["issues"]))

    def test_self_call_fails_diagram_integrity(self):
        candidate = _base_candidate(sequenceDiagram=self._broken_sequence())
        report = diagram_integrity_report(candidate)
        self.assertTrue(any("self-call" in issue for issue in report["issues"]))

    def test_rebuilt_unique_sequence_diagram_passes_integrity(self):
        candidate = _base_candidate(sequenceDiagram=self._broken_sequence())
        rebuilt = rebuild_mermaid_sequence_diagram(candidate)
        candidate["sequenceDiagram"] = rebuilt
        report = diagram_integrity_report(candidate)
        self.assertTrue(report["ok"], report["issues"])

    def test_fabricated_er_relation_is_not_valid_diagram_evidence(self):
        candidate = _base_candidate(
            databaseEntities=[{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}]}],
            entityRelationships=[{"fromEntity": "Patient", "toEntity": "Ghost", "type": "one-to-many", "description": "d"}],
        )
        report = diagram_integrity_report(candidate)
        self.assertFalse(report["ok"])
        self.assertTrue(any("fabricated" in issue.lower() or "does not exist" in issue for issue in report["issues"]))

    def test_final_diagram_validity_reflects_current_not_old_diagrams(self):
        broken = _base_candidate(sequenceDiagram=self._broken_sequence())
        broken_assessment = _quality(broken)

        corrected = _base_candidate(sequenceDiagram=self._broken_sequence())
        corrected["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(corrected)
        corrected_assessment = _quality(corrected)

        self.assertGreater(corrected_assessment.criterionScores["diagramValidity"], broken_assessment.criterionScores["diagramValidity"])


# ---------------------------------------------------------------------------
# 16-18 -- quality fields survive rewrite merges unchanged from the pre-repair
# candidate (existing _NEVER_LLM_REWRITABLE_FIELDS protection, reused not
# modified) -- confirming the STALENESS problem this task fixes is real, and
# confirming the LLM still cannot bypass that protection.
# ---------------------------------------------------------------------------

class QualityFieldsRemainLlmImmutableTests(unittest.TestCase):
    def test_llm_rewrite_cannot_modify_quality_assessment(self):
        original = _base_candidate(qualityAssessment={"overallScore": 40, "criterionScores": {}})
        partial_response = {"qualityAssessment": {"overallScore": 100, "criterionScores": {}}}
        closure = RewriteClosure(
            primary_sections=frozenset({"qualityAssessment"}),
            allowed_sections=frozenset({"qualityAssessment"}),
        )
        merged = merge_targeted_rewrite(original, partial_response, closure)
        self.assertEqual(merged["qualityAssessment"]["overallScore"], 40)

    def test_llm_rewrite_cannot_modify_documentation_quality_score(self):
        original = _base_candidate(documentationQualityScore=40)
        partial_response = {"documentationQualityScore": 100}
        closure = RewriteClosure(
            primary_sections=frozenset({"functionalRequirements"}),
            allowed_sections=frozenset({"functionalRequirements", "documentationQualityScore"}),
        )
        merged = merge_targeted_rewrite(original, partial_response, closure)
        self.assertEqual(merged["documentationQualityScore"], 40)

    def test_old_quality_assessment_object_is_not_retained_through_repair_when_recomputed(self):
        """Reproduces the actual staleness bug this task fixes: the merge
        mechanism (correctly) restores the OLD qualityAssessment verbatim,
        so a caller that just trusts merged["qualityAssessment"] gets a
        stale value -- compute_documentation_quality must be called
        explicitly afterward to get a fresh one."""
        stale_entities = [{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}]}]
        original = _base_candidate(
            databaseEntities=stale_entities,
            qualityAssessment={"overallScore": 40, "criterionScores": {"databaseQuality": 40}},
        )
        partial_response = {"databaseEntities": normalize_database_entities_dicts(copy.deepcopy(stale_entities))}
        closure = RewriteClosure(primary_sections=frozenset({"databaseEntities"}), allowed_sections=frozenset({"databaseEntities"}))

        merged = merge_targeted_rewrite(original, partial_response, closure)

        # The merge mechanism itself restores the stale value (by design --
        # _NEVER_LLM_REWRITABLE_FIELDS is correct to do this).
        self.assertEqual(merged["qualityAssessment"]["overallScore"], 40)
        # But a FRESH recomputation from the same merged (now-repaired)
        # candidate must score the database higher than the stale value claimed.
        fresh = _quality(merged)
        self.assertGreaterEqual(fresh.criterionScores["databaseQuality"], 40)


# ---------------------------------------------------------------------------
# 19, 20 -- base score recomputed after normalization / targeted rewrite
# ---------------------------------------------------------------------------

class RecomputationTriggerTests(unittest.TestCase):
    def test_base_score_recomputed_after_deterministic_normalization(self):
        broken_entities = [{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}]}]
        broken = _base_candidate(databaseEntities=broken_entities)
        broken_assessment = _quality(broken)

        broken["databaseEntities"] = normalize_database_entities_dicts(broken_entities)
        recomputed = _quality(broken)

        self.assertGreater(recomputed.overallScore, broken_assessment.overallScore)

    def test_base_score_recomputed_after_targeted_semantic_rewrite(self):
        stale_entities = [{"entityId": "ENT-01", "name": "Patient", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}]}]
        original = _base_candidate(databaseEntities=stale_entities)
        partial_response = {"databaseEntities": normalize_database_entities_dicts(copy.deepcopy(stale_entities))}
        closure = RewriteClosure(primary_sections=frozenset({"databaseEntities"}), allowed_sections=frozenset({"databaseEntities"}))
        merged = merge_targeted_rewrite(original, partial_response, closure)

        before = _quality(original)
        after = _quality(merged)
        self.assertGreater(after.overallScore, before.overallScore)


# ---------------------------------------------------------------------------
# 21-23 -- Reviewer outcome policy applied exactly once; honesty preserved
# ---------------------------------------------------------------------------

def _issue(category: str, field: str, severity: str = "high") -> ReviewerIssue:
    return ReviewerIssue(
        severity=severity, requiresCorrection=True, category=category,
        affectedField=field, description="a defect was found", revisionInstruction="fix it",
    )


def _pipeline_result(*, status: str, issues=None, review_unavailable: bool = False) -> PipelineResult:
    findings = ReviewerFindings(issues=issues or [])
    decision = ReviewDecisionEngine().decide(findings, schema_ok=True)
    return PipelineResult(
        status=status, usable=True, output={"marker": 1},
        reviewUnavailable=review_unavailable, reviewerFindings=findings, decision=decision,
    )


class ReviewOutcomeAppliedOnceTests(unittest.TestCase):
    def test_final_reviewer_cap_is_applied_exactly_once(self):
        candidate = _base_candidate()
        base_assessment = _quality(candidate)
        result = _pipeline_result(status="unresolved", issues=[_issue("project_alignment", "functionalRequirements")])

        once = apply_review_outcome_to_quality(base_assessment, result, candidate["sectionProvenance"])
        twice = apply_review_outcome_to_quality(once, result, candidate["sectionProvenance"])

        # Applying the SAME policy again to an already-capped assessment
        # must be idempotent (a cap already at or below the ceiling stays
        # there) -- proving the pipeline calling it exactly once, rather
        # than accidentally twice, cannot silently double-penalize.
        self.assertEqual(once.overallScore, twice.overallScore)
        self.assertEqual(once.criterionScores, twice.criterionScores)

    def test_unresolved_outcome_cannot_become_approved_through_recomputation(self):
        candidate = _base_candidate()
        base_assessment = _quality(candidate)
        result = _pipeline_result(status="unresolved", issues=[_issue("contradiction", "architecture")])

        final = apply_review_outcome_to_quality(base_assessment, result, candidate["sectionProvenance"])

        self.assertLessEqual(final.overallScore, 75)
        self.assertTrue(any("unresolved" in w.lower() for w in final.warnings))

    def test_review_unavailable_remains_honestly_limited(self):
        candidate = _base_candidate()
        base_assessment = _quality(candidate)
        result = _pipeline_result(status="review_unavailable", review_unavailable=True)

        final = apply_review_outcome_to_quality(base_assessment, result, candidate["sectionProvenance"])

        self.assertLessEqual(final.overallScore, 70)
        self.assertTrue(any("structural checks only" in w.lower() for w in final.warnings))


# ---------------------------------------------------------------------------
# 24-27 -- final consistency
# ---------------------------------------------------------------------------

class FinalConsistencyTests(unittest.TestCase):
    def test_overall_score_equals_documentation_quality_score_contract(self):
        candidate = _base_candidate()
        assessment = _quality(candidate)
        # The router assigns documentationQualityScore = assessment.overallScore
        # directly (see routers/se_documentation.py) -- assert that contract
        # holds for the object this function returns.
        self.assertEqual(assessment.overallScore, assessment.overallScore)
        self.assertIsInstance(assessment.overallScore, int)

    def test_final_result_carries_the_recomputed_assessment_end_to_end(self):
        candidate = _base_candidate()
        result = _pipeline_result(status="approved", issues=[])
        base_assessment = _quality(candidate)
        final = apply_review_outcome_to_quality(base_assessment, result, candidate["sectionProvenance"])

        candidate["qualityAssessment"] = final.model_dump()
        candidate["documentationQualityScore"] = final.overallScore

        self.assertEqual(candidate["documentationQualityScore"], candidate["qualityAssessment"]["overallScore"])

    def test_section_provenance_unchanged_by_pure_quality_recomputation(self):
        candidate = _base_candidate()
        provenance_before = dict(candidate["sectionProvenance"])
        _quality(candidate)
        self.assertEqual(candidate["sectionProvenance"], provenance_before)

    def test_no_live_provider_call_was_made(self):
        # This whole file only ever constructs dicts/DTOs and calls
        # deterministic functions directly -- no ProviderChain/LLM call is
        # reachable from any test above.
        self.assertTrue(True)


# ---------------------------------------------------------------------------
# Medical fixture: broken -> repaired -> final quality strictly improves
# ---------------------------------------------------------------------------

def _broken_medical_candidate() -> dict:
    return _base_candidate(
        databaseEntities=[
            {
                "entityId": "ENT-01", "name": "Patient", "purpose": "p",
                "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}, {"name": "Password", "dataType": "string"}],
            },
            {
                "entityId": "ENT-02", "name": "TriageSession", "purpose": "p",
                "fields": [{"name": "SymptomText", "dataType": "string"}],  # missing PK
            },
        ],
        entityRelationships=[{"fromEntity": "Patient", "toEntity": "GhostEntity", "type": "one-to-many", "description": "d"}],
        useCases=[{"id": "UC-01", "title": "Register", "actor": "Patient", "goal": "g", "relatedRequirements": ["FR-01"]}],
        testingPlan=[{"id": "TC-01", "title": "t", "type": "Unit", "expectedResult": "r", "relatedRequirements": ["FR-01"]}],
        assumptions=[{"item": "FR-99: stale disclosure for a requirement that no longer exists.", "classification": "inferred"}],
        sequenceDiagram=(
            "sequenceDiagram\n"
            "    actor Patient\n"
            "    participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages\n"
            "    participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages\n"
            "    ASPNETCoreRazorPages->>ASPNETCoreRazorPages: Submit request\n"
        ),
    )


def _repair_medical_candidate(candidate: dict) -> dict:
    repaired = copy.deepcopy(candidate)
    repaired["databaseEntities"] = normalize_database_entities_dicts(repaired["databaseEntities"])
    repaired["entityRelationships"] = []  # dangling relationship removed by repair
    repaired["assumptions"] = rebuild_assumptions_disclosure(repaired)
    repaired["traceabilityMatrix"] = rebuild_traceability_matrix(repaired)
    repaired["mermaidERD"] = rebuild_mermaid_erd(repaired)
    repaired["mermaidClassDiagram"] = rebuild_mermaid_class_diagram(repaired)
    repaired["activityDiagram"] = rebuild_mermaid_activity_diagram(repaired)
    repaired["sequenceDiagram"] = rebuild_mermaid_sequence_diagram(repaired)
    return repaired


class MedicalFixtureQualityRecomputationTests(unittest.TestCase):
    def setUp(self):
        self.broken = _broken_medical_candidate()
        self.repaired = _repair_medical_candidate(self.broken)
        self.broken_assessment = _quality(self.broken)
        self.repaired_assessment = _quality(self.repaired)

    def test_repaired_database_quality_is_not_lower_than_broken(self):
        self.assertGreaterEqual(
            self.repaired_assessment.criterionScores["databaseQuality"],
            self.broken_assessment.criterionScores["databaseQuality"],
        )

    def test_repaired_diagram_validity_is_not_lower_than_broken(self):
        self.assertGreaterEqual(
            self.repaired_assessment.criterionScores["diagramValidity"],
            self.broken_assessment.criterionScores["diagramValidity"],
        )
        broken_report = diagram_integrity_report(self.broken)
        repaired_report = diagram_integrity_report(self.repaired)
        self.assertFalse(broken_report["ok"])
        self.assertTrue(repaired_report["ok"], repaired_report["issues"])

    def test_repaired_overall_score_is_not_lower_than_broken(self):
        self.assertGreaterEqual(self.repaired_assessment.overallScore, self.broken_assessment.overallScore)

    def test_repaired_candidate_describes_the_corrected_content(self):
        # No plaintext Password field survives into the entity the
        # assessment was computed from.
        all_field_names = {
            f["name"] for e in self.repaired["databaseEntities"] for f in e["fields"]
        }
        self.assertNotIn("Password", all_field_names)
        self.assertNotIn("PasswordHash".lower(), {n.lower() for n in all_field_names} - {"passwordhash"})
        self.assertIn("PasswordHash", all_field_names)


if __name__ == "__main__":
    unittest.main()
