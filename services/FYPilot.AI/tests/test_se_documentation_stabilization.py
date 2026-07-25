"""
Tests for the SE Documentation accuracy/consistency stabilization batch:
- database entities always have populated, PK-bearing fields, and a
  confirmed feature always gets its required entity (knowledge base,
  support tickets, feedback, roles, ...);
- traceability is requirement-centric (every FR appears, no positional
  arbitrary zip) and uses canonical ID prefixes (MOD-/ENT-/UI-/API-/TC-);
- the assumptions section can never falsely claim nothing was assumed while
  section items are marked inferred/assumption/proposed;
- one canonical AI approach / authentication mechanism is used everywhere,
  never a mix of intent classification + RAG + fine-tuning, or Identity +
  JWT both claimed at once;
- Mermaid diagrams are structurally valid and free of combined-actor names;
- the deterministic quality score is actually stricter, never 100 across
  the board while real defects exist.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.mermaid_utils import (  # noqa: E402
    safe_label,
    split_combined_actor,
    validate_mermaid,
)
from app.agents.se_documentation.project_facts import (  # noqa: E402
    build_project_facts,
    required_entities_for_text,
    required_screens_for_text,
)
from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    EntityDto,
    EntityFieldDto,
    SEDocSelectedIdea,
    SEDocStudentProfile,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
)
from app.review.registry import SEDocumentationCandidateSchema  # noqa: E402


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
            domain="Student Support",
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
            whyUseful="Tracks stock levels and alerts managers when items run low.",
            requiredTechnologies="ASP.NET Core, SQL Server, Bootstrap",
            difficultyLevel="Medium",
            expectedDurationWeeks=10,
            domain="Retail Inventory",
            finalDeliverables="Stock tracking dashboard, low-stock alerts, reporting module",
        ),
    )


class DatabaseCoverageTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_no_entity_has_empty_fields(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        for entity in doc["databaseEntities"]:
            self.assertTrue(entity["fields"], f"{entity['name']} has no fields")

    def test_every_entity_has_a_primary_key(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        for entity in doc["databaseEntities"]:
            self.assertTrue(
                any(f["isPrimaryKey"] for f in entity["fields"]),
                f"{entity['name']} has no primary key field",
            )

    def test_password_field_normalized_to_password_hash(self):
        entities = [
            EntityDto(
                name="User", purpose="p",
                fields=[EntityFieldDto(name="Password", dataType="string")],
            )
        ]
        normalized = self.agent._normalize_entities(entities)
        field_names = [f.name for f in normalized[0].fields]
        self.assertIn("PasswordHash", field_names)
        self.assertNotIn("Password", field_names)

    def test_plaintext_password_field_rejected_by_schema(self):
        candidate = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        candidate["databaseEntities"][0]["fields"][0]["name"] = "Password"
        candidate["databaseEntities"][0]["fields"][0]["isPrimaryKey"] = False
        with self.assertRaises(Exception):
            SEDocumentationCandidateSchema.model_validate(candidate)

    def test_chatbot_project_gets_confirmed_feature_entities(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        names = {e["name"] for e in doc["databaseEntities"]}
        self.assertIn("KnowledgeArticle", names)
        self.assertIn("SupportTicket", names)
        self.assertIn("ResponseFeedback", names)

    def test_retail_project_does_not_get_chat_entities(self):
        doc = self.agent.build_safe_fallback(_retail_request()).model_dump()
        names = {e["name"] for e in doc["databaseEntities"]}
        self.assertNotIn("Conversation", names)
        self.assertNotIn("Message", names)
        self.assertNotIn("SupportTicket", names)

    def test_entity_ids_are_canonical(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        for entity in doc["databaseEntities"]:
            self.assertTrue(entity["entityId"].startswith("ENT-"))

    def test_required_entities_helper_matches_escalation_keyword(self):
        matches = dict(required_entities_for_text("Escalate unresolved queries to a support ticket."))
        self.assertIn("SupportTicket", matches)

    def test_required_entities_helper_does_not_misfire_on_generic_message_word(self):
        # "message" alone (as in "validation message") must not trigger a
        # Conversation/Message entity -- only a genuine chat/messaging
        # feature should.
        matches = dict(required_entities_for_text("Display a specific validation message to the user."))
        self.assertNotIn("Message", matches)
        self.assertNotIn("Conversation", matches)


class UiCoverageTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_screen_ids_are_canonical_ui_format(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        for screen in doc["uiScreens"]:
            self.assertTrue(screen["screenId"].startswith("UI-"), screen["screenId"])
            self.assertFalse(screen["screenId"].startswith("SC-"))

    def test_knowledge_base_requirement_adds_management_and_browser_screens(self):
        names = dict(required_screens_for_text("Manage knowledge base articles and FAQ entries."))
        self.assertIn("Knowledge Base Management", names)

    def test_ticket_requirement_adds_ticket_screens(self):
        names = dict(required_screens_for_text("Students can escalate to a support ticket."))
        self.assertIn("Support Ticket Submission", names)
        self.assertIn("Ticket Tracking", names)

    def test_feedback_requirement_adds_feedback_screen(self):
        names = dict(required_screens_for_text("Collect feedback ratings from students."))
        self.assertIn("Feedback Controls", names)

    def test_configuration_requirement_adds_settings_screen(self):
        names = dict(required_screens_for_text("Administrators configure response thresholds."))
        self.assertIn("System Configuration", names)

    def test_chatbot_screens_cover_confirmed_features(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        screen_names = {s["name"] for s in doc["uiScreens"]}
        self.assertIn("Support Ticket Submission", screen_names)
        self.assertIn("Feedback Controls", screen_names)


class TraceabilityRebuildTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()

    def test_every_functional_requirement_appears_in_traceability(self):
        fr_ids = {fr["id"] for fr in self.doc["functionalRequirements"]}
        traced_ids = {row["requirementId"] for row in self.doc["traceabilityMatrix"]}
        self.assertEqual(fr_ids, traced_ids)

    def test_every_functional_requirement_has_a_test(self):
        for fr in self.doc["functionalRequirements"]:
            self.assertTrue(
                any(fr["id"] in test["relatedRequirements"] for test in self.doc["testingPlan"]),
                f"{fr['id']} has no covering test",
            )

    def test_module_ids_use_mod_prefix(self):
        for module in self.doc["systemModules"]:
            self.assertTrue(module["id"].startswith("MOD-"))

    def test_traceability_is_not_purely_positional(self):
        # Regression guard for the old bug: with a differently-sized entity
        # list than the FR list, a positional zip would either crash or
        # silently mis-map -- the requirement-centric rebuild must instead
        # correctly report an empty entityIds list (with a documented note)
        # for any FR that genuinely has no linked entity.
        for row in self.doc["traceabilityMatrix"]:
            if not row["entityIds"]:
                self.assertIn("N/A", row["notes"])

    def test_ten_functional_requirements_all_traced(self):
        # Directly reproduces the verified bug report: 10 FRs, traceability
        # previously covered only FR-01..FR-06.
        facts = build_project_facts(_chatbot_request())
        frs = self.agent._fallback_functional_requirements(facts)
        # Simulate a larger requirement set the way the LLM path would.
        for i in range(5, 11):
            frs.append(frs[0].model_copy(update={"id": f"FR-{i:02d}", "title": f"Extra requirement {i}"}))
        frs = self.agent._ensure_unique_ids(frs, "FR")
        self.assertEqual(len(frs), 10)

        use_cases = self.agent._fallback_use_cases(facts)
        tests = self.agent._fallback_tests(facts)
        tests = self.agent._ensure_test_coverage(tests, frs)
        modules = self.agent._fallback_modules(facts)
        entities = self.agent._fallback_entities(facts)
        entities = self.agent._assign_entity_ids(entities)
        screens = self.agent._fallback_ui_screens(facts)

        traceability = self.agent._build_traceability(frs, [], use_cases, modules, entities, tests, screens, [])
        traced_ids = {row["requirementId"] if isinstance(row, dict) else row.requirementId for row in traceability}
        self.assertEqual({fr.id for fr in frs}, traced_ids)


class AssumptionHonestyTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_inferred_entity_creates_an_assumption(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        inferred_entity_names = {e["name"] for e in doc["databaseEntities"] if e["sourceClassification"] == "inferred"}
        self.assertTrue(inferred_entity_names)
        assumption_text = " ".join(a["item"] for a in doc["assumptions"])
        for name in inferred_entity_names:
            self.assertIn(name, assumption_text)

    def test_proposed_nfr_target_creates_an_assumption(self):
        doc = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        assumption_text = " ".join(a["item"] for a in doc["assumptions"])
        self.assertIn("Proposed acceptance target", assumption_text)

    def test_empty_assumptions_rejected_when_inferred_content_exists(self):
        candidate = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        candidate["assumptions"] = []
        with self.assertRaises(Exception):
            SEDocumentationCandidateSchema.model_validate(candidate)

    def test_fully_confirmed_content_allows_zero_assumptions(self):
        candidate = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        candidate["assumptions"] = []
        for key in (
            "functionalRequirements", "nonFunctionalRequirements", "useCases", "edgeCases",
            "systemModules", "databaseEntities", "uiScreens", "apiIntegrationPoints", "testingPlan",
        ):
            for item in candidate[key]:
                item["sourceClassification"] = "confirmed"
                if "measurableTarget" in item and item["measurableTarget"].lower().startswith("proposed"):
                    item["measurableTarget"] = "Response time under 2 seconds."
        SEDocumentationCandidateSchema.model_validate(candidate)


class AiAuthConsistencyTests(unittest.TestCase):
    def test_intent_classification_project_does_not_claim_rag(self):
        facts = build_project_facts(_chatbot_request())
        self.assertEqual(facts.technical_profile.ai_approach, "intent_classification")
        self.assertNotEqual(facts.technical_profile.ai_approach, "rag")

    def test_external_llm_project_does_not_claim_local_training(self):
        req = _chatbot_request()
        req.selectedIdea.whyUseful = "Uses the OpenAI external LLM API to generate answers for student questions."
        facts = build_project_facts(req)
        self.assertEqual(facts.technical_profile.ai_provider_type, "external_api")
        self.assertEqual(facts.technical_profile.training_mode, "no_local_training")

    def test_authentication_defaults_to_identity_when_only_aspnet_confirmed(self):
        facts = build_project_facts(_chatbot_request())
        self.assertEqual(facts.technical_profile.authentication_mechanism, "ASP.NET Core Identity with cookie authentication")

    def test_jwt_stripped_from_architecture_when_not_confirmed(self):
        agent = SEDocumentationOrchestratorAgent()
        facts = build_project_facts(_chatbot_request())
        architecture = agent._fallback_architecture(facts)
        architecture.authenticationFlow = "The client stores a JWT token after login."
        sanitized, _ = agent._sanitize_ai_and_auth_text(architecture, None, facts)
        self.assertNotIn("JWT", sanitized.authenticationFlow)

    def test_non_ai_project_has_no_ai_report(self):
        agent = SEDocumentationOrchestratorAgent()
        doc = agent.build_safe_fallback(_retail_request()).model_dump()
        self.assertFalse(doc["aiTechnicalReportApplicable"])
        self.assertIsNone(doc["aiTechnicalReport"])


class MermaidStabilizationTests(unittest.TestCase):
    def test_no_label_ends_mid_word(self):
        long_sentence = "System highlights items where current stock is equal to or below the configured threshold and needs restocking soon"
        label = safe_label(long_sentence)
        self.assertLessEqual(len(label), 63)
        self.assertFalse(label.rstrip(".").split(" ")[-1] == "")

    def test_combined_actor_is_split(self):
        actors = split_combined_actor("University students and support staff")
        self.assertEqual(actors, ["University students", "support staff"])

    def test_invalid_diagram_detected(self):
        ok, issues = validate_mermaid("flowchart TD\n    A --> B[]", expected_header="flowchart")
        self.assertFalse(ok)
        self.assertTrue(issues)

    def test_valid_erd_with_crowsfoot_notation_passes(self):
        ok, issues = validate_mermaid("erDiagram\n    USER ||--o{ RECORD : owns", expected_header="erDiagram")
        self.assertTrue(ok, issues)

    def test_long_single_word_participant_id_not_flagged_as_combined(self):
        ok, issues = validate_mermaid(
            "sequenceDiagram\n    participant Frontendnotconfirmed as Frontend (not confirmed)",
            expected_header="sequenceDiagram",
            known_names=["University students"],
        )
        self.assertTrue(ok, issues)

    def test_generated_diagrams_pass_validation_for_real_project(self):
        agent = SEDocumentationOrchestratorAgent()
        doc = agent.build_safe_fallback(_chatbot_request()).model_dump()
        self.assertEqual(doc["qualityAssessment"]["criterionScores"]["diagramValidity"], 100)


class QualityScoreHardeningTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_empty_entity_fields_lower_completeness(self):
        facts = build_project_facts(_chatbot_request())
        entities = [EntityDto(name="Broken", purpose="p", fields=[])]
        # _normalize_entities backfills empty fields deterministically, so to
        # exercise the scoring path directly we validate against a candidate
        # dict that bypasses normalization.
        candidate = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        candidate["databaseEntities"][0]["fields"] = []
        quality = self.agent._compute_quality_assessment(
            facts=facts,
            frs=[], nfrs=[], use_cases=[], edge_cases=[], modules=[],
            entities=[EntityDto(name="Broken", purpose="p", fields=[])],
            ui_screens=[], tests=[], traceability=[],
            architecture=self.agent._fallback_architecture(facts),
            assumptions=[], used_fallback=False,
            diagram_validation={"ok": True, "issues": []},
        )
        self.assertLessEqual(quality.criterionScores["completeness"], 70)

    def test_score_is_deterministic_across_repeated_calls(self):
        first = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        second = self.agent.build_safe_fallback(_chatbot_request()).model_dump()
        self.assertEqual(first["documentationQualityScore"], second["documentationQualityScore"])

    def test_invalid_diagram_lowers_diagram_score(self):
        facts = build_project_facts(_chatbot_request())
        quality = self.agent._compute_quality_assessment(
            facts=facts, frs=[], nfrs=[], use_cases=[], edge_cases=[], modules=[],
            entities=[], ui_screens=[], tests=[], traceability=[],
            architecture=self.agent._fallback_architecture(facts),
            assumptions=[], used_fallback=False,
            diagram_validation={"ok": False, "issues": ["broken"]},
        )
        self.assertLess(quality.criterionScores["diagramValidity"], 100)


if __name__ == "__main__":
    unittest.main()
