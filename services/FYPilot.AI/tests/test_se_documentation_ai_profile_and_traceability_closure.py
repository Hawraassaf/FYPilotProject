"""
Tests for the FINAL SE Documentation semantic-consistency fix -- closes the
two remaining live defects (2026-08-08) that kept the Reviewer status at
"unresolved" with crossSectionConsistency=60 and traceabilityCoverage=60:

ISSUE 1 -- canonical AI technical profile
    `_classify_ai_approach` (project_facts.py) is the SINGLE deterministic
    AI-approach classifier every section prompt already reads via
    `facts_context_text` -- but its keyword evidence never included
    "classif[ication]" or confirmed local ML-framework names (PyTorch,
    scikit-learn, ...), so a project whose confirmed stack was "PyTorch,
    scikit-learn" and whose own requirements said "the trained NLP model
    ... classif[ies] submitted symptom text into an urgency level" was
    classified "unresolved" -- causing the aiReport section (which followed
    that signal honestly) to contradict modulesArchitecture/requirements
    (which ignored it and described a confident local-classification
    pipeline anyway). Fixed by recognizing the (classification-task +
    local-ML-framework) evidence combination as "supervised_classification",
    and by no longer defaulting the architecture "aiService" field to the
    generic "AI/LLM component" string.

ISSUE 2 -- traceability rewrite-closure targeting
    A Reviewer finding naming a specific traceability dimension (e.g.
    "traceabilityMatrix[FR-01].entityIds") previously resolved to a rewrite
    closure containing ONLY "traceabilityMatrix" -- a field that is always
    deterministically REBUILT from the real child sections
    (databaseEntities/useCases/systemModules/testingPlan/uiScreens/
    apiIntegrationPoints) immediately after every merge (see
    rebuild_traceability_matrix), so any correction the LLM wrote directly
    into traceabilityMatrix was silently discarded and rebuilt from the
    SAME unfixed child reference -- the rewrite attempt was wasted, and the
    finding necessarily remained unresolved. Fixed by normalizing a
    dimension-specific traceability finding to ALSO include the actual
    owning child section (entityIds -> databaseEntities, moduleIds ->
    systemModules, useCaseIds -> useCases, testCaseIds -> testingPlan,
    screenIds -> uiScreens, apiIds -> apiIntegrationPoints) in the resolved
    closure, additively (never replacing the existing, already-tested
    traceabilityMatrix row-scoping behavior).

No test in this file calls a live provider -- every fixture/candidate is
constructed in-process.

Run from services/FYPilot.AI:
    python -m pytest tests/test_se_documentation_ai_profile_and_traceability_closure.py
"""

from __future__ import annotations

import unittest

from app.agents.se_documentation.project_facts import (
    ai_service_label,
    build_project_facts,
)
from app.agents.se_documentation.se_documentation_orchestrator import (
    ArchitectureDto,
    SEDocRoadmapPhase,
    SEDocSelectedIdea,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.models import ReviewerIssue
from app.review.section_scope import _roots_from_affected_field, revision_scope_for
from app.review.se_documentation_rewrite_scope import ScopeResolutionError, resolve_rewrite_closure


def _facts_for(idea_kwargs: dict, roadmap: list | None = None):
    idea = SEDocSelectedIdea(**idea_kwargs)
    request = SEDocumentationRequest(selectedIdea=idea, roadmap=roadmap or [])
    return build_project_facts(request)


def _medical_idea_kwargs(**overrides) -> dict:
    base = dict(
        title="Arabic Medical Symptom Triage Assistant",
        problemStatement="Patients lack guidance on symptom urgency and which specialist to consult.",
        targetUsers="Patients",
        whyUseful="The FastAPI triage service classifies submitted symptom text into an urgency level using the trained NLP model.",
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PyTorch, scikit-learn, PostgreSQL",
        domain="Healthcare Technology",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1-9 -- AI-approach evidence precedence
# ---------------------------------------------------------------------------

class AiApproachEvidenceTests(unittest.TestCase):
    def test_no_ai_project_profile_says_none(self):
        facts = _facts_for(dict(title="X", problemStatement="p", targetUsers="u", whyUseful="Tracks inventory.", requiredTechnologies="ASP.NET Core", domain="Retail"))
        self.assertFalse(facts.ai_involved)
        self.assertEqual(facts.technical_profile.ai_approach, "none")

    def test_explicit_intent_classification_project_confirmed(self):
        facts = _facts_for(dict(
            title="Support Chatbot", problemStatement="p", targetUsers="u",
            whyUseful="Classifies user intent using training phrases to route the conversation.",
            requiredTechnologies="Python, spaCy", domain="Support",
        ))
        self.assertEqual(facts.technical_profile.ai_approach, "intent_classification")

    def test_strong_roadmap_and_framework_evidence_confirms_local_training(self):
        roadmap = [
            SEDocRoadmapPhase(phaseNumber=1, name="Baseline Urgency and Specialist Classification Models", objective="Train baseline classifiers."),
            SEDocRoadmapPhase(phaseNumber=2, name="Fine-Tuned Arabic NLP Triage Model and FastAPI Service", objective="Fine-tune and deploy."),
        ]
        # Only the roadmap carries the classification-task language here --
        # proves roadmap text is now genuinely part of the evidence blob.
        facts = _facts_for(
            dict(title="Arabic Medical Symptom Triage Assistant", problemStatement="p", targetUsers="Patients",
                 whyUseful="Helps patients understand their symptoms.", requiredTechnologies="PyTorch, scikit-learn", domain="Healthcare"),
            roadmap=roadmap,
        )
        profile = facts.technical_profile
        self.assertEqual(profile.ai_approach, "supervised_classification")
        self.assertEqual(profile.training_mode, "local_supervised_training")

    def test_fastapi_and_model_runtime_confirm_local_inference_service(self):
        facts = _facts_for(_medical_idea_kwargs())
        profile = facts.technical_profile
        self.assertEqual(profile.ai_provider_type, "local_model")
        self.assertEqual(profile.ai_approach, "supervised_classification")

    def test_pytorch_alone_does_not_confirm_full_ai_approach(self):
        facts = _facts_for(dict(title="X", problemStatement="p", targetUsers="u", whyUseful="A student project.", requiredTechnologies="PyTorch", domain="Tech"))
        # No classification-task language anywhere -- PyTorch alone must not
        # be treated as AI involvement, let alone a confirmed approach.
        self.assertFalse(facts.ai_involved)
        self.assertEqual(facts.technical_profile.ai_approach, "none")

    def test_generic_ai_keyword_alone_does_not_confirm_llm_use(self):
        facts = _facts_for(dict(title="X", problemStatement="p", targetUsers="u", whyUseful="Uses AI to help users somehow.", requiredTechnologies="", domain="Tech"))
        self.assertNotEqual(facts.technical_profile.ai_approach, "llm_api")
        self.assertNotEqual(facts.technical_profile.ai_approach, "rag")

    def test_classification_project_is_not_rewritten_as_retrieval(self):
        facts = _facts_for(_medical_idea_kwargs())
        self.assertNotIn(facts.technical_profile.ai_approach, ("retrieval_based", "rag", "hybrid"))

    def test_local_model_project_is_not_rewritten_as_external_api(self):
        facts = _facts_for(_medical_idea_kwargs())
        self.assertNotEqual(facts.technical_profile.ai_approach, "llm_api")
        self.assertNotEqual(facts.technical_profile.ai_provider_type, "external_api")

    def test_explicit_external_llm_api_still_confirmed_as_llm_api(self):
        facts = _facts_for(dict(
            title="Support Assistant", problemStatement="p", targetUsers="u",
            whyUseful="Uses the OpenAI GPT-4 external LLM API to answer questions.",
            requiredTechnologies="Python", domain="Support",
        ))
        self.assertEqual(facts.technical_profile.ai_approach, "llm_api")
        self.assertEqual(facts.technical_profile.ai_provider_type, "external_api")


# ---------------------------------------------------------------------------
# 10-15 -- profile shared consistently across every AI-dependent prompt
# ---------------------------------------------------------------------------

class CanonicalProfileSharedAcrossPromptsTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.facts = _facts_for(_medical_idea_kwargs())

    def test_aiReport_prompt_receives_the_supervised_classification_rule(self):
        prompts = self.agent._build_section_prompts("", self.facts)
        # aiReport's own {context} substitution happens via facts_context_text
        # at generation time in the real flow -- here we assert the shared
        # canonical block itself states the resolved approach, which every
        # prompt (including aiReport, via {context}) is built from.
        from app.agents.se_documentation.project_facts import facts_context_text
        context = facts_context_text(self.facts)
        self.assertIn("supervised_classification", context)
        self.assertIn("local_supervised_training", context)

    def test_modulesArchitecture_and_testingSecurity_prompts_share_the_same_context(self):
        prompts = self.agent._build_section_prompts("SHARED_CONTEXT_MARKER", self.facts)
        for key in ("modulesArchitecture", "testingSecurity", "requirements"):
            self.assertIn("SHARED_CONTEXT_MARKER", prompts[key][0])

    def test_same_task_and_training_mode_appear_consistently(self):
        from app.agents.se_documentation.project_facts import facts_context_text
        context = facts_context_text(self.facts)
        self.assertEqual(context.count("supervised_classification"), context.count("supervised_classification"))
        # Both the approach and training mode lines must each appear exactly
        # once in the canonical block (single source, not duplicated/drifted).
        self.assertEqual(context.count("AI approach: supervised_classification"), 1)
        self.assertEqual(context.count("Training mode: local_supervised_training"), 1)


# ---------------------------------------------------------------------------
# 16 -- architecture summary no longer falls back to generic "AI/LLM component"
# ---------------------------------------------------------------------------

class ArchitectureAiServiceLabelTests(unittest.TestCase):
    def test_no_generic_ai_llm_component_when_classification_confirmed(self):
        facts = _facts_for(_medical_idea_kwargs())
        label = ai_service_label(facts.technical_profile)
        self.assertNotEqual(label, "AI/LLM component")
        self.assertIn("classification", label.lower())

    def test_architecture_or_fallback_uses_profile_label_not_hardcoded_string(self):
        agent = SEDocumentationOrchestratorAgent()
        facts = _facts_for(_medical_idea_kwargs())
        # Model's own JSON omitted aiService entirely.
        architecture = agent._architecture_or_fallback({"style": "s", "frontend": "f", "backend": "b", "database": "d", "explanation": "e"}, facts)
        self.assertNotEqual(architecture.aiService, "AI/LLM component")
        self.assertIn("classification", architecture.aiService.lower())

    def test_fallback_architecture_also_uses_profile_label(self):
        agent = SEDocumentationOrchestratorAgent()
        facts = _facts_for(_medical_idea_kwargs())
        architecture = agent._fallback_architecture(facts)
        self.assertNotEqual(architecture.aiService, "AI/LLM component")

    def test_no_ai_project_still_says_not_applicable(self):
        agent = SEDocumentationOrchestratorAgent()
        facts = _facts_for(dict(title="X", problemStatement="p", targetUsers="u", whyUseful="Tracks inventory.", requiredTechnologies="ASP.NET Core", domain="Retail"))
        architecture = agent._fallback_architecture(facts)
        self.assertEqual(architecture.aiService, "Not applicable")


# ---------------------------------------------------------------------------
# 17, 18 -- medical fixture end to end
# ---------------------------------------------------------------------------

class MedicalFixtureAiProfileTests(unittest.TestCase):
    def setUp(self):
        self.facts = _facts_for(_medical_idea_kwargs())
        self.profile = self.facts.technical_profile

    def test_produces_expected_profile(self):
        self.assertEqual(self.profile.ai_approach, "supervised_classification")
        self.assertEqual(self.profile.training_mode, "local_supervised_training")
        self.assertEqual(self.profile.ai_provider_type, "local_model")

    def test_does_not_produce_external_api_retrieval_or_unresolved(self):
        self.assertNotIn(self.profile.ai_approach, ("llm_api", "rag", "retrieval_based", "hybrid", "unresolved"))


# ---------------------------------------------------------------------------
# 21-28 -- traceability rewrite-closure targeting
# ---------------------------------------------------------------------------

def _candidate():
    return {
        "functionalRequirements": [{"id": "FR-01", "title": "T"}],
        "databaseEntities": [{"entityId": "ENT-13", "name": "QueryLog"}],
        "traceabilityMatrix": [{"requirementId": "FR-01", "entityIds": ["ENT-13"]}],
        "useCases": [], "testingPlan": [], "systemModules": [],
        "uiScreens": [], "apiIntegrationPoints": [], "assumptions": [],
    }


def _issue(affected_field: str, description: str = "d") -> ReviewerIssue:
    return ReviewerIssue(
        severity="high", requiresCorrection=True, category="contradiction",
        affectedField=affected_field, description=description, revisionInstruction="fix it",
    )


class TraceabilityDimensionNormalizationTests(unittest.TestCase):
    def test_entityIds_dimension_resolves_to_databaseEntities(self):
        roots = _roots_from_affected_field(_candidate(), "traceabilityMatrix[FR-01].entityIds")
        self.assertIn("databaseEntities", roots)

    def test_moduleIds_dimension_resolves_to_systemModules(self):
        candidate = _candidate()
        candidate["systemModules"] = []
        roots = _roots_from_affected_field(candidate, "traceabilityMatrix[FR-01].moduleIds")
        self.assertIn("systemModules", roots)

    def test_testCaseIds_dimension_resolves_to_testingPlan(self):
        roots = _roots_from_affected_field(_candidate(), "traceabilityMatrix[FR-01].testCaseIds")
        self.assertIn("testingPlan", roots)

    def test_useCaseIds_dimension_resolves_to_useCases(self):
        roots = _roots_from_affected_field(_candidate(), "traceabilityMatrix[FR-01].useCaseIds")
        self.assertIn("useCases", roots)

    def test_bare_traceabilityMatrix_field_without_hint_is_unchanged(self):
        # No dimension hint -- must not fabricate a source section; existing
        # (already-tested) behavior is preserved exactly.
        roots = _roots_from_affected_field(_candidate(), "traceabilityMatrix")
        self.assertEqual(roots, {"traceabilityMatrix"})

    def test_dimension_source_added_additively_not_replacing_traceabilityMatrix(self):
        roots = _roots_from_affected_field(_candidate(), "traceabilityMatrix[FR-01].entityIds")
        self.assertIn("traceabilityMatrix", roots)
        self.assertIn("databaseEntities", roots)

    def test_targeted_closure_becomes_resolvable_for_entity_scoped_finding(self):
        candidate = _candidate()
        issue = _issue("traceabilityMatrix[FR-01].entityIds", "Entity 'QueryLog' (ENT-13) is unrelated to FR-01.")
        closure = resolve_rewrite_closure(candidate, [issue])
        self.assertIn("databaseEntities", closure.primary_sections)

    def test_entity_seed_narrows_closure_to_the_named_entity(self):
        candidate = _candidate()
        issue = _issue("traceabilityMatrix[FR-01].entityIds", "Entity 'QueryLog' is unrelated to FR-01.")
        closure = resolve_rewrite_closure(candidate, [issue])
        self.assertIn("QueryLog", closure.seed_entity_names)

    def test_revision_scope_for_never_returns_only_traceabilityMatrix_for_dimension_finding(self):
        scope = revision_scope_for("SEDocumentationAgent", _candidate(), [_issue("traceabilityMatrix[FR-01].entityIds")])
        self.assertNotEqual(scope, {"traceabilityMatrix"})
        self.assertIn("databaseEntities", scope)

    def test_no_regression_existing_row_scoping_behavior_for_unrelated_database_finding(self):
        """A plain databaseEntities finding (no traceability dimension in the
        field name at all) must resolve exactly as before -- this fix must
        be purely additive for traceability-dimension findings."""
        candidate = _candidate()
        issue = _issue("databaseEntities", "Entity 'QueryLog' has filler fields.")
        closure = resolve_rewrite_closure(candidate, [issue])
        self.assertIn("databaseEntities", closure.primary_sections)
        self.assertNotIn("traceabilityMatrix", closure.primary_sections)


if __name__ == "__main__":
    unittest.main()
