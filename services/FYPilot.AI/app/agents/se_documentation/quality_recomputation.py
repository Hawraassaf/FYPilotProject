"""
Final-candidate quality recomputation for SE Documentation.

Context: documentationQualityScore/qualityAssessment are correctly listed in
_NEVER_LLM_REWRITABLE_FIELDS (see app/review/section_scope.py) -- the LLM
must never choose its own final numerical score. But that exact protection
means merge_structural_repair/merge_targeted_rewrite (see
app/review/se_documentation_structural_repair_scope.py,
app/review/se_documentation_rewrite_scope.py) always RESTORE those two
fields from the PRE-repair candidate on every merge. Every deterministic
correction this project makes AFTER Writer-assembly time --
databaseEntities normalization (PasswordHash, primary keys), assumptions
rebuild, requirement-reference reconciliation, requirement reverse-reference
rebuild, traceability rebuild, ERD/class/activity/sequence diagram rebuild
-- therefore left the persisted quality fields silently describing the OLD,
pre-repair candidate.

compute_documentation_quality is the single dict-based entrypoint that
closes that gap: called once, on the FINAL candidate (after every repair/
rebuild pass and after ReviewPipeline.run() returns, immediately before
quality_outcome_policy.apply_review_outcome_to_quality -- see
routers/se_documentation.py's call order), it reparses the final
candidate's own sections into the same typed DTOs
SEDocumentationOrchestratorAgent._compute_quality_assessment uses at Writer
time and calls the exact same underlying calculator
(se_documentation_orchestrator.compute_quality_assessment) -- never a
second, competing scoring formula.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from app.agents.se_documentation.project_facts import ProjectFacts
from app.agents.se_documentation.se_documentation_orchestrator import (
    QUALITY_CRITERION_WEIGHTS,
    AiTechnicalReportDto,
    ArchitectureDto,
    AssumptionDto,
    EdgeCaseDto,
    EntityDto,
    ModuleDto,
    QualityAssessmentDto,
    RequirementDto,
    TestCaseDto,
    TraceabilityDto,
    UiScreenDto,
    UseCaseDto,
    compute_quality_assessment,
)
from app.review.se_documentation_diagram_integrity import diagram_integrity_report
from app.review.se_documentation_structural_invariants import (
    diagnose_structural_invariants,
    rebuild_traceability_matrix,
)

Candidate = Dict[str, Any]

# Reuses the exact "limited confidence" cap value quality_outcome_policy.py
# already applies for analogous situations -- this module invents no new
# numeric convention (see that module's own _LIMITED_CONFIDENCE_CAP).
_STRUCTURAL_INVARIANT_ISSUE_CAP = 70


def _recompute_overall(criterion_scores: Dict[str, int]) -> int:
    return round(sum(
        criterion_scores.get(name, 100) * weight for name, weight in QUALITY_CRITERION_WEIGHTS.items()
    ))


def _parse_dtos(items: Any, dto_cls: Type[BaseModel]) -> List[Any]:
    """Parses a raw list of dicts into typed DTOs, silently skipping any
    entry that doesn't validate -- a section that doesn't even parse into
    its own DTO is a SEPARATE schema-validity problem the normal
    validation/repair path is responsible for; this recomputation must
    never crash over it (see the same convention used throughout
    se_documentation_deterministic_normalization.py /
    se_documentation_structural_invariants.py)."""
    out: List[Any] = []
    for item in (items or []):
        if not isinstance(item, dict):
            continue
        try:
            out.append(dto_cls.model_validate(item))
        except Exception:
            continue
    return out


def compute_documentation_quality(
    candidate: Candidate,
    *,
    project_facts: ProjectFacts,
    section_provenance: Optional[Dict[str, str]] = None,
) -> QualityAssessmentDto:
    """
    Recomputes a fresh, deterministic BASE qualityAssessment from the given
    FINAL candidate dict -- see this module's docstring for why this must
    be called again after Writer time rather than trusting the persisted
    (possibly stale) qualityAssessment field.

    traceabilityMatrix is always rebuilt HERE via rebuild_traceability_matrix
    rather than read from `candidate["traceabilityMatrix"]` directly, so
    this can never be fed a pre-repair matrix by accident -- cheap and
    idempotent, exactly like this codebase's other post-repair rebuilds.

    diagramValidity is computed via diagram_integrity_report (structural
    integrity checks -- duplicate participants, undeclared endpoints,
    self-calls, fabricated ER relationship endpoints -- on top of, not
    instead of, the existing Mermaid text-safety validation), never the
    bare "Mermaid text parsed" signal alone.

    used_fallback is reconstructed from section_provenance (whole-document
    fallback = every known section is "fallback"), reusing the same
    provenance signal SEDocumentationOrchestratorAgent already tracks,
    rather than inventing a new provenance-aware scoring policy.
    """
    frs = _parse_dtos(candidate.get("functionalRequirements"), RequirementDto)
    nfrs = _parse_dtos(candidate.get("nonFunctionalRequirements"), RequirementDto)
    use_cases = _parse_dtos(candidate.get("useCases"), UseCaseDto)
    edge_cases = _parse_dtos(candidate.get("edgeCases"), EdgeCaseDto)
    modules = _parse_dtos(candidate.get("systemModules"), ModuleDto)
    entities = _parse_dtos(candidate.get("databaseEntities"), EntityDto)
    ui_screens = _parse_dtos(candidate.get("uiScreens"), UiScreenDto)
    tests = _parse_dtos(candidate.get("testingPlan"), TestCaseDto)
    assumptions = _parse_dtos(candidate.get("assumptions"), AssumptionDto)

    traceability = _parse_dtos(rebuild_traceability_matrix(candidate), TraceabilityDto)

    try:
        architecture = ArchitectureDto.model_validate(candidate.get("architecture") or {})
    except Exception:
        architecture = ArchitectureDto(style="", frontend="", backend="", database="", aiService="", explanation="")

    ai_applicable = bool(candidate.get("aiTechnicalReportApplicable"))
    ai_report = None
    ai_report_raw = candidate.get("aiTechnicalReport")
    if isinstance(ai_report_raw, dict):
        try:
            ai_report = AiTechnicalReportDto.model_validate(ai_report_raw)
        except Exception:
            ai_report = None

    diagram_validation = diagram_integrity_report(candidate)

    provenance_values = list((section_provenance or {}).values())
    used_fallback = bool(provenance_values) and all(value == "fallback" for value in provenance_values)

    assessment = compute_quality_assessment(
        project_facts, frs, nfrs, use_cases, edge_cases, modules, entities, ui_screens, tests,
        traceability, architecture, assumptions, used_fallback, diagram_validation,
        ai_report=ai_report, ai_applicable=ai_applicable,
    )

    # crossSectionConsistency's base ref_valid/ref_total ratio (see
    # compute_quality_assessment) only proves referenced ids EXIST -- it
    # cannot see a duplicate id, an invalid reference namespace, or an
    # unresolvable relationship endpoint that diagnose_structural_invariants
    # (already used elsewhere in this pipeline for repair, reused here
    # read-only) DOES catch. "All ids exist" must never read as "100" when
    # this deterministic check still finds something wrong.
    invariant_issues = diagnose_structural_invariants(candidate)
    if invariant_issues:
        criterion_scores = dict(assessment.criterionScores)
        current = criterion_scores.get("crossSectionConsistency", 100)
        if current > _STRUCTURAL_INVARIANT_ISSUE_CAP:
            criterion_scores["crossSectionConsistency"] = _STRUCTURAL_INVARIANT_ISSUE_CAP
            warnings = list(assessment.warnings)
            note = (
                f"{len(invariant_issues)} structural invariant issue(s) remained in the final "
                f"candidate (e.g. duplicate ids, dangling references, or unresolved relationship "
                f"endpoints); cross-section consistency was capped accordingly."
            )
            if note not in warnings:
                warnings.append(note)
            assessment = assessment.model_copy(update={
                "criterionScores": criterion_scores,
                "overallScore": max(0, min(100, _recompute_overall(criterion_scores))),
                "warnings": warnings,
            })

    return assessment
