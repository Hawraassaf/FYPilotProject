"""
Phase 2B regression tests -- the "Arabic Medical Symptom Triage Assistant"
8-defect scenario used to determine whether the existing semantic-review
rubric (registry._SEDOC_EXTRA_RUBRIC) actually covers, or is blind to, four
specific defect classes:

  A. Cross-domain contamination (an inventory-management feature bleeding
     into a medical-triage document)
  B. Wrong-role UI authorization (an end-user role assigned admin-only
     screens)
  C. Semantic traceability mismatch (structurally valid ids connected to
     the wrong, unrelated section -- proving the reviewer must judge
     MEANING, not merely that an id exists)
  D. Architecture-vs-AI-technical-report internal contradiction
  E. A clean, internally consistent control candidate (negative control --
     must not trip any of the above)

Two independent levels, per the stabilization task's requirement that "unit
tests must not depend on external provider availability":

  Level 1 (*ContractTests) -- no LLM involved at all. Proves the
  deterministic plumbing (ReviewDecisionEngine, and the exact set of fields
  ReviewPipeline hands to the Reviewer) behaves correctly given a
  hand-specified issue shape.

  Level 2 (*RegressionTests) -- exercises the real ReviewPipeline with a
  controlled/fake ReviewerAgent standing in for what a live LLM reviewer is
  EXPECTED to return for each defect. Proves that IF a reviewer produces
  that exact finding, the pipeline correctly rewrites exactly once and
  approves the corrected candidate -- and that the clean control is never
  rewritten. This does not prove a live LLM will actually produce that
  finding; that is a separate, later, manually-run live-provider check (see
  the stabilization task's Step 8).

Both levels build their fixtures from the REAL deterministic fallback path
(SEDocumentationOrchestratorAgent.build_safe_fallback), mirroring
test_se_documentation_accuracy.py, so every fixture is guaranteed to be a
genuine, schema-valid SEDocumentationDto rather than a hand-rolled dict that
might silently drift from the real schema.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest
from copy import deepcopy

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    SEDocSelectedIdea,
    SEDocStudentProfile,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.context import ReviewContext  # noqa: E402
from app.review.models import ReviewerFindings, ReviewerIssue  # noqa: E402
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.review.registry import _SEDOC_EXTRA_RUBRIC, get_agent_config  # noqa: E402
from app.review.review_decision_engine import ReviewDecisionEngine  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fakes -- same shape as tests/test_review_pipeline.py's
# _FakeReviewerAgent/_FakeRewriteAgent, kept local so this file has no
# cross-file test dependency.
# ---------------------------------------------------------------------------

def _ok(data, provider="groq", model="llama-3.3-70b-versatile"):
    return LLMResult(ok=True, provider=provider, model=model, text="", data=data)


def _fail(error="provider unavailable"):
    return LLMResult(ok=False, provider="none", model=None, text="", data=None, error=error)


def _issue(severity="high", requires_correction=True, category="contradiction", field_name="x"):
    return {
        "severity": severity,
        "requiresCorrection": requires_correction,
        "category": category,
        "affectedField": field_name,
        "description": "issue description",
        "revisionInstruction": "fix it",
    }


def _reviewer_ok(issues=None, quality=90):
    return _ok({
        "strengths": [],
        "issues": issues or [],
        "qualityScore": quality,
        "overallAssessment": "assessment",
    })


class _FakeReviewerAgent:
    def __init__(self, results):
        self._results = list(results)

    def analyze(self, candidate, context, **kwargs):
        if not self._results:
            return _fail("reviewer exhausted")
        return self._results.pop(0)


class _RecordingReviewerAgent:
    """Spy that records the exact candidate dict it was handed, standing in
    for the real ReviewerAgent so Step 4 can assert on what the pipeline
    actually sent -- never invokes a provider."""

    def __init__(self):
        self.received_candidates: list[dict] = []

    def analyze(self, candidate, context, **kwargs):
        self.received_candidates.append(candidate)
        return _reviewer_ok(issues=[])


class _FakeRewriteAgent:
    def __init__(self, rewrite_results=None, fix_results=None):
        self._rewrite_results = list(rewrite_results or [])
        self._fix_results = list(fix_results or [])

    def rewrite(self, candidate, blocking_issues, context, *, agent_name):
        if not self._rewrite_results:
            return _fail("rewrite exhausted")
        return self._rewrite_results.pop(0)

    def fix_structure(self, candidate, *, agent_name, validation_errors=None, expected_schema=None):
        if not self._fix_results:
            return _fail("fix_structure exhausted")
        return self._fix_results.pop(0)


def _sedoc_context() -> ReviewContext:
    return ReviewContext(
        agent_name="SEDocumentationAgent",
        trusted_system_instructions="SEDocumentationAgent test context.",
        trusted_structural_context={"teamSize": 2, "experienceLevel": "intermediate"},
        untrusted_project_text={"ideaTitle": "Arabic Medical Symptom Triage Assistant"},
    )


def _make_pipeline(*, reviewer_results, rewrite_results=None, fix_results=None):
    return ReviewPipeline(
        "SEDocumentationAgent",
        reviewer_agent=_FakeReviewerAgent(reviewer_results),
        rewrite_agent=_FakeRewriteAgent(rewrite_results, fix_results),
        config=get_agent_config("SEDocumentationAgent"),
    )


# ---------------------------------------------------------------------------
# Fixture: the Arabic Medical Symptom Triage Assistant candidate, and the
# four defect-injection mutations (A-D). Built on top of the real
# deterministic fallback so every fixture is a genuine, schema-valid
# SEDocumentationDto -- the defects below are semantic, not structural.
# ---------------------------------------------------------------------------

def _medical_triage_request() -> SEDocumentationRequest:
    return SEDocumentationRequest(
        studentProfile=SEDocStudentProfile(teamSize=2, skills=["Python", "NLP"]),
        selectedIdea=SEDocSelectedIdea(
            title="Arabic Medical Symptom Triage Assistant",
            problemStatement=(
                "Patients describe symptoms in Arabic and the system classifies "
                "urgency and suggests next steps using a curated medical knowledge base."
            ),
            targetUsers="Patients seeking initial guidance on symptom urgency",
            whyUseful=(
                "Provides fast, accessible initial triage guidance in Arabic before "
                "a patient decides whether to seek in-person care."
            ),
            requiredTechnologies="ASP.NET Core, Python FastAPI, PostgreSQL, local NLP model",
            difficultyLevel="High",
            expectedDurationWeeks=14,
            domain="Healthcare",
            finalDeliverables="Symptom intake chat, triage classification, escalation guidance",
        ),
    )


def _base_candidate() -> dict:
    agent = SEDocumentationOrchestratorAgent()
    candidate = agent.build_safe_fallback(_medical_triage_request()).model_dump()

    # Force a known, deterministic AI-technical-report starting point so
    # scenario D's mutation is self-contained and doesn't depend on
    # project_facts.py's own text-classification heuristics.
    candidate["aiTechnicalReportApplicable"] = True
    candidate["aiTechnicalReport"] = {
        "problemDefinition": "Classify the urgency of a patient's described symptoms.",
        "taskType": "Symptom urgency intent classification",
        "inputData": "Free-text Arabic symptom description",
        "output": "Urgency classification and next-step guidance",
        "modelOrApproach": "Confirmed local NLP intent-classification model with knowledge-base lookup",
        "trainingVsInference": "Inference only, against a pre-trained local model",
        "retrievalStrategy": "Knowledge-base lookup for matched guidance",
        "fallbackStrategy": "Escalate to human guidance when confidence is low",
        "confidenceHandling": "Low-confidence classifications are escalated",
        "evaluationMetrics": ["Classification accuracy"],
        "datasetNeeds": "Labeled Arabic symptom/urgency examples",
        "biasAndSafetyRisks": "Misclassifying urgent symptoms as non-urgent",
        "hallucinationMitigation": "Knowledge-base grounding, not free generation",
        "monitoring": "Track escalation rate and confidence distribution",
        "limitations": "Limited to symptoms represented in the knowledge base",
    }
    candidate["architecture"]["aiService"] = (
        "Local NLP intent-classification model with knowledge-base lookup"
    )
    candidate["architecture"]["explanation"] = (
        str(candidate["architecture"].get("explanation", ""))
        + " The AI component uses a confirmed local intent-classification model "
        "combined with knowledge-base lookup to classify symptom urgency."
    )
    return candidate


def _with_cross_domain_contamination(candidate: dict) -> dict:
    """Scenario A: an inventory-management FR (wrong domain entirely) mixed
    into a medical-triage document, with a Store Manager actor and
    stock/inventory-transaction content."""
    candidate = deepcopy(candidate)

    existing_ids = {fr["id"] for fr in candidate["functionalRequirements"]}
    new_id = "FR-INV-CONTAM-01"
    assert new_id not in existing_ids, "fixture id collided with the real deterministic generator's output"

    candidate["functionalRequirements"].append({
        "id": new_id,
        "title": "Generate Inventory Reports",
        "description": (
            "The system generates reports of stock levels and inventory "
            "transactions for the store manager."
        ),
        "priority": "Medium",
        "source": "confirmed",
        "primaryActor": "Store Manager",
        "systemBehavior": "Aggregates stock levels and inventory transactions into a report.",
        "sourceClassification": "confirmed",
    })
    candidate["traceabilityMatrix"].append({
        "requirementId": new_id,
        "coverageStatus": "covered",
    })
    return candidate


def _with_wrong_role_authorization(candidate: dict) -> dict:
    """Scenario B: end-user "Patient" role assigned admin-only screens."""
    candidate = deepcopy(candidate)

    existing_ids = {screen["screenId"] for screen in candidate["uiScreens"]}
    new_screens = [
        ("UI-WRONGROLE-01", "Analytics Dashboard"),
        ("UI-WRONGROLE-02", "System Configuration"),
        ("UI-WRONGROLE-03", "User and Role Management"),
    ]
    for screen_id, _ in new_screens:
        assert screen_id not in existing_ids, "fixture screenId collided with the real generator's output"

    for screen_id, name in new_screens:
        candidate["uiScreens"].append({
            "screenId": screen_id,
            "name": name,
            "authorizedRoles": ["Patient"],
            "purpose": f"{name} screen.",
        })
    return candidate


def _with_semantic_traceability_mismatch(candidate: dict) -> dict:
    """Scenario C: swap two existing, real traceability rows' linked
    module/entity ids between each other. Every id used is genuinely
    present in the candidate (structurally valid) -- only the linkage is
    now semantically nonsensical, proving a reviewer must judge MEANING,
    not merely that an id exists."""
    candidate = deepcopy(candidate)
    matrix = candidate["traceabilityMatrix"]
    assert len(matrix) >= 2, "expected at least 2 traceability rows in the base fallback candidate"

    row_a, row_b = matrix[0], matrix[1]
    row_a["moduleIds"], row_b["moduleIds"] = (
        list(row_b.get("moduleIds") or []),
        list(row_a.get("moduleIds") or []),
    )
    row_a["entityIds"], row_b["entityIds"] = (
        list(row_b.get("entityIds") or []),
        list(row_a.get("entityIds") or []),
    )
    return candidate


def _with_ai_report_contradiction(candidate: dict) -> dict:
    """Scenario D: architecture confirms a local NLP approach; the AI
    technical report says the task type/approach is unresolved and an
    external LLM API is still possible -- an internal contradiction between
    two sections of the SAME candidate."""
    candidate = deepcopy(candidate)
    candidate["aiTechnicalReport"]["taskType"] = "Task type not yet confirmed"
    candidate["aiTechnicalReport"]["modelOrApproach"] = (
        "Approach not yet confirmed; an external LLM API is still a possible option"
    )
    candidate["aiTechnicalReport"]["trainingVsInference"] = "Not yet determined"
    return candidate


# ---------------------------------------------------------------------------
# Semantic-prompt-content tests -- these do not exercise any code path; they
# assert the literal rubric TEXT the real Reviewer prompt will actually
# contain (see ReviewerAgent.build_prompt's extra_rubric_block) names each of
# the 4 defect classes explicitly. This is a distinct, previously-missing
# layer of proof: the contract/decision-engine/pipeline tests below prove
# the PLUMBING reacts correctly to a given finding; these prove the
# INSTRUCTION a live reviewer will read actually asks it to look for that
# finding. Neither layer proves a live LLM will actually produce the
# finding -- that is the separate, later, manually-run live-provider check.
# ---------------------------------------------------------------------------

class SeDocRubricContentTests(unittest.TestCase):
    def test_rubric_instructs_reviewer_to_flag_cross_domain_contamination(self):
        self.assertIn("Domain contamination", _SEDOC_EXTRA_RUBRIC)
        self.assertIn("DIFFERENT", _SEDOC_EXTRA_RUBRIC)

    def test_rubric_instructs_reviewer_to_flag_wrong_role_authorization(self):
        self.assertIn("Role-appropriate authorization", _SEDOC_EXTRA_RUBRIC)
        self.assertIn("authorizedRoles", _SEDOC_EXTRA_RUBRIC)

    def test_rubric_instructs_reviewer_to_check_semantic_traceability(self):
        self.assertIn("Semantic traceability", _SEDOC_EXTRA_RUBRIC)
        self.assertIn("ids exist", _SEDOC_EXTRA_RUBRIC)

    def test_rubric_instructs_reviewer_to_check_architecture_ai_report_consistency(self):
        self.assertIn("Architecture/AI report consistency", _SEDOC_EXTRA_RUBRIC)
        self.assertIn("aiTechnicalReport", _SEDOC_EXTRA_RUBRIC)


# ---------------------------------------------------------------------------
# Level 1 -- deterministic contract tests (no LLM call anywhere)
# ---------------------------------------------------------------------------

class SeDocFinalCandidateReachesReviewerTests(unittest.TestCase):
    """Step 4: prove the Reviewer receives the FINAL, fully-assembled
    candidate -- after section generation, deterministic merge-back,
    traceability, assumptions, provenance, diagrams, and the base
    deterministic quality calculation -- never a partial per-section batch."""

    _REQUIRED_FINAL_FIELDS = [
        "functionalRequirements",
        "nonFunctionalRequirements",
        "useCases",
        "edgeCases",
        "systemModules",
        "databaseEntities",
        "entityRelationships",
        "uiScreens",
        "apiIntegrationPoints",
        "architecture",
        "testingPlan",
        "traceabilityMatrix",
        "aiTechnicalReport",
        "assumptions",
        "sectionProvenance",
    ]

    def test_reviewer_receives_every_required_final_field(self):
        candidate = _base_candidate()
        spy = _RecordingReviewerAgent()

        pipeline = ReviewPipeline(
            "SEDocumentationAgent",
            reviewer_agent=spy,
            rewrite_agent=_FakeRewriteAgent(),
            config=get_agent_config("SEDocumentationAgent"),
        )
        result = pipeline.run(
            lambda: _ok(candidate),
            _sedoc_context(),
            writer_trusted_parts={"system_instructions": "x"},
            writer_untrusted_parts={"idea": "x"},
        )

        self.assertTrue(result.usable)
        self.assertEqual(len(spy.received_candidates), 1)
        reviewed = spy.received_candidates[0]

        for field_name in self._REQUIRED_FINAL_FIELDS:
            self.assertIn(field_name, reviewed, f"reviewer never received '{field_name}'")

        # The substantive sections must actually be populated, not merely
        # present as empty placeholders -- otherwise "received the field"
        # would trivially pass even for a half-assembled candidate.
        for non_empty_field in (
            "functionalRequirements", "useCases", "systemModules",
            "databaseEntities", "uiScreens", "testingPlan", "traceabilityMatrix",
        ):
            self.assertTrue(reviewed[non_empty_field], f"'{non_empty_field}' was empty")
        self.assertTrue(reviewed["architecture"])

        # sectionProvenance specifically -- the reviewer must be able to see
        # per-section provider-vs-fallback origin, not just a bare key.
        self.assertTrue(reviewed["sectionProvenance"], "sectionProvenance was empty")


class SeDocDecisionEngineContractTests(unittest.TestCase):
    """No LLM call at all -- proves the shared, deterministic
    ReviewDecisionEngine treats each defect's EXACT intended issue shape
    (severity/category/requiresCorrection) as a material blocking issue,
    independent of whether any given LLM reviewer actually produces it."""

    @staticmethod
    def _decide(**issue_kwargs):
        findings = ReviewerFindings(issues=[ReviewerIssue(
            description="d",
            revisionInstruction="fix",
            affectedField="x",
            **issue_kwargs,
        )])
        return ReviewDecisionEngine().decide(findings, schema_ok=True)

    def test_cross_domain_contamination_issue_blocks(self):
        decision = self._decide(severity="critical", requiresCorrection=True, category="project_alignment")
        self.assertTrue(decision.requiresRewrite)

    def test_wrong_role_authorization_issue_blocks(self):
        decision = self._decide(severity="high", requiresCorrection=True, category="contradiction")
        self.assertTrue(decision.requiresRewrite)

    def test_semantic_traceability_mismatch_issue_blocks(self):
        decision = self._decide(severity="high", requiresCorrection=True, category="contradiction")
        self.assertTrue(decision.requiresRewrite)

    def test_ai_report_contradiction_issue_blocks(self):
        decision = self._decide(severity="high", requiresCorrection=True, category="contradiction")
        self.assertTrue(decision.requiresRewrite)

    def test_clean_candidate_has_no_blocking_issues(self):
        decision = ReviewDecisionEngine().decide(ReviewerFindings(issues=[]), schema_ok=True)
        self.assertFalse(decision.requiresRewrite)


# ---------------------------------------------------------------------------
# Level 2 -- reviewer-behavior regression, using a controlled (fake)
# reviewer result standing in for what a live LLM reviewer is expected to
# return. Proves the real ReviewPipeline (decision engine + rewrite loop)
# reacts correctly to each defect's finding: exactly one rewrite, then
# approval; the clean control is never rewritten.
# ---------------------------------------------------------------------------

class SeDocReviewerBehaviorRegressionTests(unittest.TestCase):
    def _run(self, candidate, reviewer_results, rewrite_results=None):
        pipeline = _make_pipeline(
            reviewer_results=reviewer_results,
            rewrite_results=rewrite_results or [],
        )
        return pipeline.run(
            lambda: _ok(candidate),
            _sedoc_context(),
            writer_trusted_parts={"system_instructions": "x"},
            writer_untrusted_parts={"idea": "x"},
        )

    def test_cross_domain_contamination_triggers_exactly_one_rewrite(self):
        base = _base_candidate()
        defective = _with_cross_domain_contamination(base)
        fixed = _base_candidate()  # corrected candidate drops the off-domain FR

        issue = _issue(
            severity="critical", requires_correction=True, category="project_alignment",
            field_name="functionalRequirements",
        )
        result = self._run(
            defective,
            reviewer_results=[_reviewer_ok(issues=[issue]), _reviewer_ok(issues=[])],
            rewrite_results=[_ok(fixed)],
        )

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.attempts, 2)
        operations = [record.operation for record in result.attemptHistory]
        self.assertEqual(operations.count("semantic_rewrite"), 1)

    def test_wrong_role_authorization_triggers_exactly_one_rewrite(self):
        base = _base_candidate()
        defective = _with_wrong_role_authorization(base)
        fixed = _base_candidate()

        issue = _issue(
            severity="high", requires_correction=True, category="contradiction",
            field_name="uiScreens",
        )
        result = self._run(
            defective,
            reviewer_results=[_reviewer_ok(issues=[issue]), _reviewer_ok(issues=[])],
            rewrite_results=[_ok(fixed)],
        )

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.attempts, 2)
        operations = [record.operation for record in result.attemptHistory]
        self.assertEqual(operations.count("semantic_rewrite"), 1)

    def test_semantic_traceability_mismatch_triggers_exactly_one_rewrite(self):
        base = _base_candidate()
        defective = _with_semantic_traceability_mismatch(base)
        fixed = _base_candidate()  # correct linkage restored

        issue = _issue(
            severity="high", requires_correction=True, category="contradiction",
            field_name="traceabilityMatrix",
        )
        result = self._run(
            defective,
            reviewer_results=[_reviewer_ok(issues=[issue]), _reviewer_ok(issues=[])],
            rewrite_results=[_ok(fixed)],
        )

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.attempts, 2)
        operations = [record.operation for record in result.attemptHistory]
        self.assertEqual(operations.count("semantic_rewrite"), 1)

    def test_ai_report_contradiction_triggers_exactly_one_rewrite(self):
        base = _base_candidate()
        defective = _with_ai_report_contradiction(base)
        fixed = _base_candidate()

        issue = _issue(
            severity="high", requires_correction=True, category="contradiction",
            field_name="aiTechnicalReport",
        )
        result = self._run(
            defective,
            reviewer_results=[_reviewer_ok(issues=[issue]), _reviewer_ok(issues=[])],
            rewrite_results=[_ok(fixed)],
        )

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.attempts, 2)
        operations = [record.operation for record in result.attemptHistory]
        self.assertEqual(operations.count("semantic_rewrite"), 1)

    def test_clean_control_candidate_is_never_rewritten(self):
        clean = _base_candidate()

        result = self._run(clean, reviewer_results=[_reviewer_ok(issues=[])])

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.attempts, 1)
        operations = [record.operation for record in result.attemptHistory]
        self.assertNotIn("semantic_rewrite", operations)


if __name__ == "__main__":
    unittest.main()
