"""
Tests for the SE Documentation content-depth batch:
- the deterministic fallback derives content from a canonical feature model
  (project_facts.derive_canonical_features), never from the raw `domain`
  dropdown value -- fixes the verified "Manage core AI/Data Science
  records" / "AI/DataScienceRecord" / generic "Summary Dashboard" bug;
- a single failed/rate-limited LLM section falls back to detailed,
  project-specific content for JUST that section (section_provenance),
  instead of collapsing the entire document to the generic 4-FR fallback;
- the AI technical report fallback is complete, never an empty placeholder;
- the deterministic quality score's new contentDepth criterion actually
  distinguishes shallow content from detailed content.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.project_facts import (  # noqa: E402
    build_project_facts,
    derive_canonical_features,
)
from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    SEDocSelectedIdea,
    SEDocStudentProfile,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.registry import SEDocumentationCandidateSchema  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


def _chatbot_request() -> SEDocumentationRequest:
    return SEDocumentationRequest(
        studentProfile=SEDocStudentProfile(teamSize=2, skills=["Python", "FastAPI"]),
        selectedIdea=SEDocSelectedIdea(
            title="Chatbot for University Student Support Services",
            problemStatement="Students struggle to get fast answers to common university questions outside office hours.",
            targetUsers="University students",
            whyUseful=(
                "Uses intent classification with training phrases and FAQ knowledge base lookup, "
                "escalates unresolved questions to support staff as tickets, and collects feedback ratings."
            ),
            requiredTechnologies="ASP.NET Core Identity, Python NLP, PostgreSQL",
            difficultyLevel="High",
            expectedDurationWeeks=14,
            domain="AI/Data Science",
            finalDeliverables="Chat widget, knowledge base admin panel, escalation workflow",
        ),
    )


def _retail_request() -> SEDocumentationRequest:
    return SEDocumentationRequest(
        studentProfile=SEDocStudentProfile(teamSize=1, skills=["C#", "ASP.NET"]),
        selectedIdea=SEDocSelectedIdea(
            title="Inventory Management System for a Retail Store",
            problemStatement="Small retail stores struggle to track stock levels manually.",
            targetUsers="Store managers",
            whyUseful="Tracks stock levels in real time, records sales transactions, manages suppliers, and generates reports.",
            requiredTechnologies="ASP.NET Core, SQL Server, Bootstrap",
            difficultyLevel="Medium",
            expectedDurationWeeks=10,
            domain="Retail Inventory",
            finalDeliverables="Stock tracking dashboard, low-stock alerts, reporting module",
        ),
    )


class CanonicalFeatureModelTests(unittest.TestCase):
    def test_chatbot_features_are_project_specific(self):
        facts = build_project_facts(_chatbot_request())
        features = derive_canonical_features(facts)
        names = {f.name for f in features}
        self.assertIn("Submit and Process User Query", names)
        self.assertIn("Escalate Unresolved Query to Support Staff", names)
        self.assertNotIn("Manage core AI/Data Science records", names)

    def test_retail_features_never_mention_chat_concepts(self):
        facts = build_project_facts(_retail_request())
        features = derive_canonical_features(facts)
        blob = " ".join(f.name + f.description for f in features).lower()
        for term in ("chatbot", "conversation", "intent", "knowledge base"):
            self.assertNotIn(term, blob)

    def test_core_feature_never_uses_domain_string_in_name(self):
        # Regression guard for the verified bug: domain dropdown values like
        # "AI/Data Science" must never be concatenated into an entity/FR name.
        facts = build_project_facts(_chatbot_request())
        features = derive_canonical_features(facts)
        for feature in features:
            self.assertNotIn("AI/Data Science", feature.name)
            self.assertNotIn("DataScience", feature.name.replace(" ", ""))


class ProjectSpecificFallbackTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_chatbot_fallback_has_no_generic_ai_data_science_record(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        blob = str(doc).lower().replace(" ", "")
        self.assertNotIn("ai/datasciencerecord", blob)
        self.assertNotIn("datasciencerecord", blob)

    def test_chatbot_fallback_has_no_manage_core_records_requirement(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        titles = [fr["title"].lower() for fr in doc["functionalRequirements"]]
        self.assertFalse(any("manage core" in t for t in titles))

    def test_chatbot_fallback_covers_expected_core_features(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        entity_names = {e["name"] for e in doc["databaseEntities"]}
        screen_names = {s["name"] for s in doc["uiScreens"]}
        self.assertTrue({"Conversation", "Message", "KnowledgeArticle", "SupportTicket"} <= entity_names)
        self.assertTrue({"Chat Interface", "Knowledge Base Browser", "Support Ticket Submission"} <= screen_names)

    def test_retail_fallback_covers_inventory_concepts_not_chatbot(self):
        doc = self.agent.build_safe_fallback(_retail_request()).model_dump()
        entity_names = {e["name"] for e in doc["databaseEntities"]}
        self.assertTrue({"Product", "StockTransaction", "Supplier"} <= entity_names)
        for forbidden in ("Conversation", "Message", "Intent", "KnowledgeArticle", "SupportTicket"):
            self.assertNotIn(forbidden, entity_names)

    def test_fallback_is_meaningfully_deeper_than_old_four_fr_baseline(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        self.assertGreaterEqual(len(doc["functionalRequirements"]), 8)
        self.assertGreaterEqual(len(doc["useCases"]), 6)
        self.assertGreaterEqual(len(doc["databaseEntities"]), 8)
        self.assertGreaterEqual(len(doc["uiScreens"]), 8)
        self.assertGreaterEqual(len(doc["testingPlan"]), 12)

    def test_fallback_entities_are_linked_to_requirements_in_traceability(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        covered_by_entities = sum(1 for row in doc["traceabilityMatrix"] if row["entityIds"])
        self.assertGreater(covered_by_entities, 0)

    def test_fallback_passes_full_schema_validation(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        SEDocumentationCandidateSchema.model_validate(doc)


class AiReportFallbackTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_ai_report_fallback_has_no_empty_fields(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        report = doc["aiTechnicalReport"]
        self.assertIsNotNone(report)
        for key, value in report.items():
            self.assertTrue(value not in (None, "", []), f"{key} is empty in fallback AI report")

    def test_ai_report_approach_matches_technical_profile(self):
        facts = build_project_facts(_chatbot_request())
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        self.assertIn("intent classification", doc["aiTechnicalReport"]["modelOrApproach"].lower())
        self.assertEqual(facts.technical_profile.ai_approach, "intent_classification")


class PartialProviderFailureTests(unittest.TestCase):
    """
    Directly reproduces the verified bug: a single failed LLM section used
    to discard every other section that already succeeded and collapse the
    whole document to the generic 4-FR fallback.
    """

    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_one_failed_section_preserves_other_provider_sections(self):
        good_requirements = {
            "functionalRequirements": [
                {"id": "FR-01", "title": "Real Provider FR", "description": "d", "priority": "High", "source": "s", "acceptanceCriteria": ["a check"]}
            ],
            "nonFunctionalRequirements": [
                {"id": "NFR-01", "title": "Real NFR", "description": "d", "priority": "High", "source": "s", "measurableTarget": "t", "verificationMethod": "v"}
            ],
        }

        def fake_generate_json(prompt, use_search=False, max_tokens=None):
            if "functionalRequirements" in prompt and "nonFunctionalRequirements" in prompt:
                return LLMResult(ok=True, provider="fake", model="fake-model", text="", data=good_requirements)
            return LLMResult(ok=False, provider="fake", model="fake-model", text="", data=None, error="simulated failure")

        self.agent.provider_chain.generate_json = fake_generate_json

        doc = self.agent.generate(_chatbot_request()).model_dump()

        self.assertTrue(self.agent.last_llm_used)
        self.assertEqual(doc["sectionProvenance"]["requirements"], "provider")
        self.assertEqual(doc["sectionProvenance"]["useCases"], "fallback")
        self.assertEqual(len(doc["functionalRequirements"]), 1)
        self.assertEqual(doc["functionalRequirements"][0]["title"], "Real Provider FR")
        # Other sections still got detailed, project-specific fallback content
        # instead of an empty/generic document.
        self.assertGreaterEqual(len(doc["uiScreens"]), 3)
        self.assertIn("Chat Interface", [s["name"] for s in doc["uiScreens"]])

    def test_all_sections_failing_is_reported_as_full_fallback(self):
        def always_fails(prompt, use_search=False, max_tokens=None):
            return LLMResult(ok=False, provider="fake", model="fake-model", text="", data=None, error="simulated failure")

        self.agent.provider_chain.generate_json = always_fails
        doc = self.agent.generate(_chatbot_request()).model_dump()

        self.assertFalse(self.agent.last_llm_used)
        self.assertTrue(all(status == "fallback" for status in doc["sectionProvenance"].values()))
        self.assertLessEqual(doc["documentationQualityScore"], 70)

    def test_partial_fallback_warning_names_only_failed_sections(self):
        def fails_use_cases_only(prompt, use_search=False, max_tokens=None):
            if "mainFlow" in prompt:
                return LLMResult(ok=False, provider="fake", model="fake-model", text="", data=None, error="simulated failure")
            return LLMResult(ok=False, provider="fake", model="fake-model", text="", data=None, error="simulated failure")

        # Simplify: fail everything except requirements, confirm warning text
        # mentions specific section keys rather than a blanket "all sections".
        good_requirements = {
            "functionalRequirements": [{"id": "FR-01", "title": "X", "description": "d", "priority": "High", "source": "s", "acceptanceCriteria": ["c"]}],
            "nonFunctionalRequirements": [{"id": "NFR-01", "title": "Y", "description": "d", "priority": "High", "source": "s", "measurableTarget": "t", "verificationMethod": "v"}],
        }

        def fake_generate_json(prompt, use_search=False, max_tokens=None):
            if "functionalRequirements" in prompt and "nonFunctionalRequirements" in prompt:
                return LLMResult(ok=True, provider="fake", model="fake-model", text="", data=good_requirements)
            return LLMResult(ok=False, provider="fake", model="fake-model", text="", data=None, error="simulated failure")

        self.agent.provider_chain.generate_json = fake_generate_json
        doc = self.agent.generate(_chatbot_request()).model_dump()

        warning_text = " ".join(doc["consistencyWarnings"])
        self.assertIn("useCases", warning_text)
        self.assertNotIn("requirements,", warning_text)  # requirements succeeded, must not be listed as fallback


class SectionsTimeBudgetTests(unittest.TestCase):
    """
    Live verification of this batch surfaced a real sustained Groq+Gemini
    rate-limit outage: retrying every failed section once, with no overall
    cap, could make a single request take tens of minutes. These tests lock
    in the fix -- an overall soft time budget after which remaining
    attempts/retries are skipped in favor of immediate fallback.
    """

    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_expired_budget_skips_the_call_entirely(self):
        self.agent._sections_deadline = 0.0  # already in the past
        calls = {"n": 0}

        def should_never_be_called(prompt, use_search=False, max_tokens=None):
            calls["n"] += 1
            return LLMResult(ok=True, provider="fake", model="fake", text="", data={})

        self.agent.provider_chain.generate_json = should_never_be_called
        result = self.agent._call_section_safe("requirements", "prompt", max_tokens=1000)

        self.assertIsNone(result)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(self.agent.section_provenance["requirements"], "fallback")

    def test_budget_expiring_after_first_attempt_skips_the_retry(self):
        import time as time_module

        self.agent._sections_deadline = time_module.monotonic() + 100.0

        def fails_and_expires_budget(prompt, use_search=False, max_tokens=None):
            self.agent._sections_deadline = 0.0  # simulate budget running out mid-call
            return LLMResult(ok=False, provider="fake", model="fake", text="", data=None, error="simulated")

        self.agent.provider_chain.generate_json = fails_and_expires_budget
        result = self.agent._call_section_safe("requirements", "prompt", max_tokens=1000)

        self.assertIsNone(result)
        self.assertEqual(self.agent.section_provenance["requirements"], "fallback")

    def test_ample_budget_still_allows_the_normal_retry(self):
        import time as time_module

        self.agent._sections_deadline = time_module.monotonic() + 100.0
        calls = {"n": 0}

        def fails_once_then_succeeds(prompt, use_search=False, max_tokens=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResult(ok=False, provider="fake", model="fake", text="", data=None, error="simulated")
            return LLMResult(ok=True, provider="fake", model="fake", text="", data={"ok": True})

        self.agent.provider_chain.generate_json = fails_once_then_succeeds
        result = self.agent._call_section_safe("requirements", "prompt", max_tokens=1000)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.agent.section_provenance["requirements"], "provider")


class ContentDepthScoringTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_content_depth_criterion_present(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        self.assertIn("contentDepth", doc["qualityAssessment"]["criterionScores"])

    def test_shallow_content_scores_lower_than_detailed_fallback(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        detailed_depth = doc["qualityAssessment"]["criterionScores"]["contentDepth"]

        # Simulate the OLD shallow fallback shape directly against the
        # scoring function: one-line FRs, no acceptance criteria, 2-step
        # use cases, 2-field entities, screens missing states.
        from app.agents.se_documentation.se_documentation_orchestrator import (
            EntityDto, EntityFieldDto, RequirementDto, TestCaseDto, UiScreenDto, UseCaseDto,
            ArchitectureDto,
        )

        facts = build_project_facts(_chatbot_request())
        shallow_frs = [RequirementDto(id="FR-01", title="Do thing", description="Does a thing.", priority="High", source="s")]
        shallow_ucs = [UseCaseDto(id="UC-01", title="Do thing", actor="User", goal="g", mainFlow=["1. Do it."], relatedRequirements=["FR-01"])]
        shallow_entities = [EntityDto(name="Thing", purpose="p", fields=[EntityFieldDto(name="Id", dataType="int", isPrimaryKey=True)], primaryKey="Id")]
        shallow_screens = [UiScreenDto(screenId="UI-01", name="Main", purpose="p")]
        shallow_tests = [TestCaseDto(id="TC-01", title="t", type="Functional", steps=["Do it."], expectedResult="ok", relatedRequirements=["FR-01"])]
        architecture = self.agent._fallback_architecture(facts)

        quality = self.agent._compute_quality_assessment(
            facts=facts, frs=shallow_frs, nfrs=[], use_cases=shallow_ucs, edge_cases=[],
            modules=[], entities=shallow_entities, ui_screens=shallow_screens, tests=shallow_tests,
            traceability=[], architecture=architecture, assumptions=[], used_fallback=True,
            diagram_validation={"ok": True, "issues": []},
        )

        self.assertLess(quality.criterionScores["contentDepth"], detailed_depth)


if __name__ == "__main__":
    unittest.main()
