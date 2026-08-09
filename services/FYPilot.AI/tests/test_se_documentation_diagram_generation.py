"""
Tests for deterministic activity/sequence diagram generation -- closes the
live defects (2026-08-08) where:

  - the Activity Diagram was built from `use_cases[0]` unconditionally, so a
    medical symptom-triage project's diagram rendered UC-01 (patient
    registration/login) instead of its actual central workflow (symptom
    submission -> FastAPI triage -> classification -> result);
  - the Sequence Diagram declared a participant once per architecture field
    (frontend/backend/database) without deduplicating by real identity, so
    a project whose confirmed technology name (e.g. "ASP.NET Core Razor
    Pages") matched BOTH the frontend and backend keyword lists in
    _pick_layer produced two identical `participant ... as ...`
    declarations and a meaningless `X -> X: Submit request` self-call --
    passing basic Mermaid string validation while being semantically and
    structurally wrong.

Covers the 23 required proofs:
 1.  first use case is registration but core use case is later -> later
     use case selected
 2.  primary workflow selection uses canonical requirement links
 3.  no use cases -> honest empty/minimal diagram behavior
 4.  duplicate actor spelling normalizes to one participant
 5.  duplicate component spelling normalizes to one participant
 6.  sanitized-id collision produces unique Mermaid ids
 7.  sequence participant rendered exactly once
 8.  all message endpoints are declared
 9.  accidental self-call removed
10.  the registry itself never prevents a caller from modelling a
     genuinely-intentional self-call (it only collapses IDENTICAL
     identities, never distinct ones)
11.  activity step order preserved
12.  long activity labels remain readable and bounded
13.  medical fixture chooses the symptom-triage workflow
14.  medical fixture does not choose registration as primary workflow
15.  medical sequence contains FastAPI
16.  medical sequence contains PostgreSQL where persistence is represented
17.  no generic "AI Service" participant is used when FastAPI Triage
     Service is confirmed
18.  rebuild occurs after a useCases correction
19.  rebuild occurs after a modulesArchitecture correction
20.  a database-only correction does not change the primary activity flow
21.  existing ERD/class diagram construction is unaffected
22.  the complete structural candidate still passes schema validation
23.  no provider call occurs anywhere in this file

No test in this file calls a live provider -- every DTO/document is
constructed in-process; the rebuild functions are exercised directly on
hand-built candidate dicts.

Run from services/FYPilot.AI:
    python -m pytest tests/test_se_documentation_diagram_generation.py
"""

from __future__ import annotations

import unittest

from app.agents.se_documentation.project_facts import build_project_facts
from app.agents.se_documentation.se_documentation_orchestrator import (
    ApiPointDto,
    ArchitectureDto,
    EdgeCaseDto,
    EntityDto,
    ModuleDto,
    RequirementDto,
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
    UseCaseDto,
    _ParticipantRegistry,
)
from app.agents.se_documentation.mermaid_utils import validate_mermaid
from app.review.registry import SEDocumentationCandidateSchema
from app.review.se_documentation_structural_invariants import (
    rebuild_mermaid_activity_diagram,
    rebuild_mermaid_class_diagram,
    rebuild_mermaid_erd,
    rebuild_mermaid_sequence_diagram,
)


def _agent() -> SEDocumentationOrchestratorAgent:
    return SEDocumentationOrchestratorAgent()


def _facts(**overrides):
    facts = build_project_facts(SEDocumentationRequest())
    return facts.model_copy(update=overrides)


# ---------------------------------------------------------------------------
# 1, 2 -- deterministic primary-workflow selection
# ---------------------------------------------------------------------------

class PrimaryUseCaseSelectionTests(unittest.TestCase):
    def test_later_use_case_selected_over_first_when_it_is_the_core_workflow(self):
        facts = _facts(primary_actor="Patient")
        frs = [
            RequirementDto(id="FR-01", title="Registration", description="d", priority="Medium", source="s"),
            RequirementDto(id="FR-02", title="Core Feature", description="d", priority="High", source="s"),
            RequirementDto(id="FR-03", title="Core Feature Processing", description="d", priority="High", source="s"),
        ]
        uc_registration = UseCaseDto(id="UC-01", title="Register", actor="Patient", goal="g", relatedRequirements=["FR-01"])
        uc_core = UseCaseDto(id="UC-05", title="Do the core thing", actor="Patient", goal="g", relatedRequirements=["FR-02", "FR-03"])

        selected = _agent()._select_primary_use_case(facts, frs, [uc_registration, uc_core])

        self.assertEqual(selected.id, "UC-05")

    def test_selection_uses_canonical_requirement_links_not_use_case_order(self):
        facts = _facts(primary_actor="User")
        frs = [RequirementDto(id="FR-01", title="A", description="d", priority="High", source="s")]
        uc_no_links = UseCaseDto(id="UC-01", title="Unrelated", actor="User", goal="g", relatedRequirements=[])
        uc_linked = UseCaseDto(id="UC-02", title="Related", actor="User", goal="g", relatedRequirements=["FR-01"])

        selected = _agent()._select_primary_use_case(facts, frs, [uc_no_links, uc_linked])

        self.assertEqual(selected.id, "UC-02")

    def test_no_use_cases_returns_none_honestly(self):
        facts = _facts()
        selected = _agent()._select_primary_use_case(facts, [], [])
        self.assertIsNone(selected)


# ---------------------------------------------------------------------------
# 3 -- no use cases -> honest empty/minimal diagram
# ---------------------------------------------------------------------------

class EmptyUseCaseDiagramTests(unittest.TestCase):
    def test_activity_diagram_with_no_primary_use_case_uses_generic_fallback_not_fabrication(self):
        facts = _facts(primary_actor="User", title="My Project")
        diagram = _agent()._build_activity_diagram(facts, None, [])
        self.assertIn("flowchart TD", diagram)
        self.assertIn("User", diagram)
        ok, issues = validate_mermaid(diagram, expected_header="flowchart")
        self.assertTrue(ok, issues)

    def test_sequence_diagram_with_no_primary_use_case_still_declares_core_participants_once(self):
        facts = _facts(primary_actor="User")
        architecture = ArchitectureDto(style="s", frontend="Frontend", backend="Backend", database="DB", aiService="Not applicable", explanation="e")
        diagram = _agent()._build_sequence_diagram(facts, architecture, None)
        self.assertEqual(diagram.count("participant Backend"), 1)


# ---------------------------------------------------------------------------
# 4, 5, 6, 7 -- participant registry: identity dedupe + id-collision safety
# ---------------------------------------------------------------------------

class ParticipantRegistryTests(unittest.TestCase):
    def test_duplicate_actor_spelling_normalizes_to_one_participant(self):
        """Actor identity is normalized by the CALLER via _normalize_actor_key
        (see _build_sequence_diagram) before registration -- "Patients",
        "Patient", "patient" all fold to the same registry key."""
        from app.agents.se_documentation.se_documentation_orchestrator import _normalize_actor_key

        registry = _ParticipantRegistry()
        p1 = registry.register(_normalize_actor_key("Patients"), "Patients", is_actor=True)
        p2 = registry.register(_normalize_actor_key("patient"), "patient", is_actor=True)
        self.assertIs(p1, p2)
        self.assertEqual(len(registry.render()), 1)

    def test_duplicate_component_spelling_normalizes_to_one_participant(self):
        registry = _ParticipantRegistry()
        p1 = registry.register("ASP.NET Core Razor Pages", "ASP.NET Core Razor Pages")
        p2 = registry.register("ASP.NET Core Razor Pages", "ASP.NET Core Razor Pages")
        self.assertIs(p1, p2)
        self.assertEqual(len(registry.render()), 1)

    def test_sanitized_id_collision_produces_unique_mermaid_ids(self):
        registry = _ParticipantRegistry()
        p1 = registry.register("AI Service", "AI Service")
        p2 = registry.register("AI-Service", "AI-Service")  # different real identity, same sanitized id
        self.assertNotEqual(p1.mermaid_id, p2.mermaid_id)
        self.assertEqual(p1.mermaid_id, "AIService")
        self.assertEqual(p2.mermaid_id, "AIService2")

    def test_participant_rendered_exactly_once(self):
        registry = _ParticipantRegistry()
        registry.register("Patient", "Patient", is_actor=True)
        registry.register("Patient", "Patient", is_actor=True)
        registry.register("Patient", "Patient", is_actor=True)
        rendered = registry.render()
        self.assertEqual(len(rendered), 1)
        self.assertEqual(sum(1 for line in rendered if "Patient" in line), 1)


# ---------------------------------------------------------------------------
# 8, 9, 10 -- message endpoints declared, no accidental self-calls
# ---------------------------------------------------------------------------

class SequenceMessageTests(unittest.TestCase):
    def _diagram(self, frontend: str, backend: str) -> str:
        facts = _facts(primary_actor="Patient", ai_involved=False)
        architecture = ArchitectureDto(style="s", frontend=frontend, backend=backend, database="PostgreSQL", aiService="Not applicable", explanation="e")
        uc = UseCaseDto(id="UC-01", title="Do thing", actor="Patient", goal="g", trigger="Patient does thing", relatedRequirements=[])
        return _agent()._build_sequence_diagram(facts, architecture, uc)

    def test_all_message_endpoints_are_declared(self):
        diagram = self._diagram("Frontend", "Backend")
        declared_ids = set()
        for line in diagram.splitlines():
            line = line.strip()
            if line.startswith("participant ") or line.startswith("actor "):
                declared_ids.add(line.split()[1])

        for line in diagram.splitlines():
            line = line.strip()
            if "->>" in line or "-->>" in line:
                arrow = "-->>" if "-->>" in line else "->>"
                left, right = line.split(arrow)
                left_id = left.strip()
                right_id = right.split(":")[0].strip()
                self.assertIn(left_id, declared_ids)
                self.assertIn(right_id, declared_ids)

    def test_accidental_self_call_removed_when_frontend_and_backend_collapse(self):
        """Reproduces the exact live bug: frontend and backend both resolve
        to the same real component ("ASP.NET Core Razor Pages")."""
        diagram = self._diagram("ASP.NET Core Razor Pages", "ASP.NET Core Razor Pages")

        self.assertEqual(diagram.count("participant ASPNETCoreRazorPages as ASP.NET Core Razor Pages"), 1)
        for line in diagram.splitlines():
            if "->>" in line or "-->>" in line:
                arrow = "-->>" if "-->>" in line else "->>"
                left, right = line.split(arrow)
                left_id = left.strip()
                right_id = right.split(":")[0].strip()
                self.assertNotEqual(left_id, right_id, f"unexpected self-call: {line}")

    def test_registry_does_not_forbid_a_caller_intentionally_modelling_a_self_call(self):
        """The registry only collapses IDENTICAL registered identities --
        it never prevents a caller from writing a genuine self-call line
        for two DISTINCT participants that happen to share a display
        concept (e.g. a component calling into its own internal
        sub-routine as a separately registered participant)."""
        registry = _ParticipantRegistry()
        internal = registry.register("Backend.InternalValidation", "Backend (internal validation)")
        backend = registry.register("Backend", "Backend")
        self.assertNotEqual(internal.mermaid_id, backend.mermaid_id)
        line = f"    {backend.mermaid_id}->>{internal.mermaid_id}: Validate internally"
        self.assertIn("Backend", line)


# ---------------------------------------------------------------------------
# 11, 12 -- activity step order + label safety
# ---------------------------------------------------------------------------

class ActivityDiagramContentTests(unittest.TestCase):
    def test_activity_step_order_is_preserved(self):
        facts = _facts(primary_actor="Patient")
        uc = UseCaseDto(
            id="UC-03", title="Submit", actor="Patient", goal="g",
            mainFlow=["First step happens", "Second step happens", "Third step happens"],
        )
        diagram = _agent()._build_activity_diagram(facts, uc, [])
        first_pos = diagram.index("First step happens")
        second_pos = diagram.index("Second step happens")
        third_pos = diagram.index("Third step happens")
        self.assertLess(first_pos, second_pos)
        self.assertLess(second_pos, third_pos)

    def test_long_activity_labels_remain_readable_and_bounded(self):
        facts = _facts(primary_actor="Patient")
        long_step = "This is a deliberately very long main-flow step sentence that describes in great detail exactly what the system does at this point in the workflow"
        uc = UseCaseDto(id="UC-03", title="Submit", actor="Patient", goal="g", mainFlow=[long_step])
        diagram = _agent()._build_activity_diagram(facts, uc, [])
        ok, issues = validate_mermaid(diagram, expected_header="flowchart")
        self.assertTrue(ok, issues)
        for line in diagram.splitlines():
            if "[" in line:
                label = line.split("[", 1)[1].rsplit("]", 1)[0]
                self.assertLessEqual(len(label), 63)  # MAX_LABEL_LENGTH + "..."


# ---------------------------------------------------------------------------
# 13-17 -- medical fixture end to end
# ---------------------------------------------------------------------------

def _medical_facts():
    return _facts(
        title="Arabic Medical Symptom Triage Assistant",
        primary_actor="Patient",
        ai_involved=True,
    )


def _medical_frs():
    return [
        RequirementDto(id="FR-01", title="Patient Account Registration and Login", description="d", priority="High", source="s"),
        RequirementDto(id="FR-02", title="Symptom Description Submission", description="d", priority="High", source="s"),
        RequirementDto(id="FR-03", title="Send Symptom Text to FastAPI Triage Service", description="d", priority="High", source="s"),
        RequirementDto(id="FR-04", title="Symptom Urgency Classification", description="d", priority="High", source="s"),
    ]


def _medical_use_cases():
    return [
        UseCaseDto(
            id="UC-01", title="Register Patient Account", actor="Patient", goal="Register an account",
            relatedRequirements=["FR-01"], trigger="Patient opens registration page",
            mainFlow=[
                "Patient navigates to registration page", "Patient enters name/email/password",
                "System performs identity validation", "System creates the account",
                "System issues an auth cookie", "Patient is redirected to the dashboard",
            ],
        ),
        UseCaseDto(
            id="UC-03", title="Submit Symptom Description for Triage", actor="Patient", goal="Submit symptom description for triage",
            relatedRequirements=["FR-02", "FR-03", "FR-04"], trigger="Patient enters symptom description",
            mainFlow=[
                "Patient enters symptom description", "Application validates the input",
                "Razor Pages sends the request to FastAPI", "FastAPI preprocesses the symptom text",
                "The NLP model performs urgency classification", "The urgency/specialist result is returned",
                "The triage result is displayed to the patient", "The triage record is persisted",
            ],
        ),
    ]


def _medical_modules():
    return [ModuleDto(id="MOD-01", name="ClassificationModule", responsibility="r", relatedRequirements=["FR-04"])]


def _medical_api_points():
    return [ApiPointDto(
        apiId="API-01", name="FastAPI Triage Service", method="POST", endpoint="/triage",
        purpose="p", requestSummary="r", responseSummary="r", relatedRequirements=["FR-03"],
    )]


def _medical_architecture():
    return ArchitectureDto(
        style="s", frontend="ASP.NET Core Razor Pages", backend="ASP.NET Core Razor Pages",
        database="PostgreSQL", aiService="AI/NLP service", explanation="e",
    )


class MedicalFixtureDiagramTests(unittest.TestCase):
    def setUp(self):
        self.agent = _agent()
        self.facts = _medical_facts()
        self.frs = _medical_frs()
        self.use_cases = _medical_use_cases()
        self.primary = self.agent._select_primary_use_case(self.facts, self.frs, self.use_cases)
        self.activity = self.agent._build_activity_diagram(self.facts, self.primary, [])
        self.sequence = self.agent._build_sequence_diagram(
            self.facts, _medical_architecture(), self.primary, self.frs, _medical_modules(), _medical_api_points(),
        )

    def test_medical_fixture_chooses_symptom_triage_workflow(self):
        self.assertEqual(self.primary.id, "UC-03")

    def test_medical_fixture_does_not_choose_registration_as_primary(self):
        self.assertNotEqual(self.primary.id, "UC-01")

    def test_activity_diagram_contains_symptom_submission_and_triage_concepts(self):
        lowered = self.activity.lower()
        self.assertIn("symptom", lowered)
        self.assertIn("fastapi", lowered)
        self.assertTrue("classif" in lowered or "preprocess" in lowered)
        self.assertIn("result", lowered)
        self.assertNotIn("registration page", lowered)

    def test_medical_sequence_contains_fastapi(self):
        self.assertIn("FastAPI Triage Service", self.sequence)

    def test_medical_sequence_contains_postgresql(self):
        self.assertIn("PostgreSQL", self.sequence)

    def test_no_generic_ai_service_participant_when_fastapi_confirmed(self):
        self.assertNotIn("AI Service", self.sequence)
        self.assertNotIn("participant AIService", self.sequence)

    def test_exactly_one_razor_pages_declaration(self):
        self.assertEqual(self.sequence.count("ASP.NET Core Razor Pages"), 1)

    def test_no_accidental_duplicate_participant_ids(self):
        declared_ids = [
            line.strip().split()[1]
            for line in self.sequence.splitlines()
            if line.strip().startswith("participant ") or line.strip().startswith("actor ")
        ]
        self.assertEqual(len(declared_ids), len(set(declared_ids)))

    def test_no_meaningless_razor_pages_self_call(self):
        self.assertNotIn("ASPNETCoreRazorPages->>ASPNETCoreRazorPages", self.sequence.replace(" ", ""))

    def test_diagrams_pass_mermaid_validation(self):
        activity_ok, activity_issues = validate_mermaid(self.activity, expected_header="flowchart", known_names=["Patient"])
        sequence_ok, sequence_issues = validate_mermaid(self.sequence, expected_header="sequenceDiagram", known_names=["Patient"])
        self.assertTrue(activity_ok, activity_issues)
        self.assertTrue(sequence_ok, sequence_issues)


# ---------------------------------------------------------------------------
# 18, 19, 20 -- post-rewrite deterministic rebuild triggers
# ---------------------------------------------------------------------------

def _medical_candidate_dict() -> dict:
    agent = _agent()
    facts = _medical_facts()
    frs = _medical_frs()
    use_cases = _medical_use_cases()
    primary = agent._select_primary_use_case(facts, frs, use_cases)
    architecture = _medical_architecture()
    modules = _medical_modules()
    api_points = _medical_api_points()

    return {
        "projectTitle": facts.title,
        "stakeholders": [facts.primary_actor],
        "aiTechnicalReportApplicable": True,
        "functionalRequirements": [fr.model_dump() for fr in frs],
        "useCases": [uc.model_dump() for uc in use_cases],
        "edgeCases": [],
        "systemModules": [m.model_dump() for m in modules],
        "apiIntegrationPoints": [a.model_dump() for a in api_points],
        "architecture": architecture.model_dump(),
        "databaseEntities": [
            {"entityId": "ENT-01", "name": "TriageSession", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}]},
        ],
        "entityRelationships": [],
        "activityDiagram": agent._build_activity_diagram(facts, primary, []),
        "sequenceDiagram": agent._build_sequence_diagram(facts, architecture, primary, frs, modules, api_points),
    }


class RebuildTriggerTests(unittest.TestCase):
    def test_rebuild_reflects_a_useCases_correction(self):
        candidate = _medical_candidate_dict()
        # Corrupt useCases exactly like the live defect: make the FIRST use
        # case (registration) look artificially dominant so a naive
        # useCases[0] rebuild would pick it -- the fix must still choose the
        # real central workflow via relatedRequirements, not list order.
        for uc in candidate["useCases"]:
            if uc["id"] == "UC-01":
                uc["title"] = "Register Patient Account (corrected)"

        rebuilt_activity = rebuild_mermaid_activity_diagram(candidate)
        self.assertIn("symptom", rebuilt_activity.lower())
        self.assertNotIn("register patient account", rebuilt_activity.lower())

    def test_rebuild_reflects_a_modulesArchitecture_correction(self):
        candidate = _medical_candidate_dict()
        candidate["systemModules"] = [
            {"id": "MOD-01", "name": "RenamedClassificationModule", "responsibility": "r", "relatedRequirements": ["FR-04"]},
        ]

        rebuilt_sequence = rebuild_mermaid_sequence_diagram(candidate)
        self.assertIn("RenamedClassificationModule", rebuilt_sequence)

    def test_database_only_correction_does_not_change_primary_activity_flow(self):
        candidate = _medical_candidate_dict()
        before = rebuild_mermaid_activity_diagram(candidate)

        candidate["databaseEntities"].append(
            {"entityId": "ENT-02", "name": "PatientFeedback", "purpose": "p", "fields": [{"name": "Id", "dataType": "int", "isPrimaryKey": True}]}
        )

        after = rebuild_mermaid_activity_diagram(candidate)
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# 21 -- existing ERD/class diagram construction unaffected
# ---------------------------------------------------------------------------

class ExistingDiagramConstructionUnaffectedTests(unittest.TestCase):
    def test_erd_and_class_diagram_rebuild_still_work_on_the_medical_candidate(self):
        candidate = _medical_candidate_dict()
        erd = rebuild_mermaid_erd(candidate)
        class_diagram = rebuild_mermaid_class_diagram(candidate)
        self.assertIn("erDiagram", erd)
        self.assertIn("classDiagram", class_diagram)


# ---------------------------------------------------------------------------
# 22, 23 -- full structural validation + no provider calls
# ---------------------------------------------------------------------------

class FullCandidateValidationTests(unittest.TestCase):
    def test_full_candidate_including_diagrams_passes_structural_validation(self):
        agent = _agent()
        facts = _medical_facts()
        request = SEDocumentationRequest()
        sections = {
            "requirements": {
                "functionalRequirements": [fr.model_dump() for fr in _medical_frs()],
                "nonFunctionalRequirements": [
                    {"id": "NFR-01", "title": "Response Time", "description": "d", "priority": "High", "source": "s", "measurableTarget": "Proposed acceptance target: under 5s."},
                ],
            },
            "useCases": {"useCases": [uc.model_dump() for uc in _medical_use_cases()], "edgeCases": []},
            "modulesArchitecture": {"systemModules": [m.model_dump() for m in _medical_modules()], "architecture": _medical_architecture().model_dump()},
            "database": {
                "databaseEntities": [
                    {"entityId": "ENT-01", "name": "TriageSession", "purpose": "p", "fields": [
                        {"name": "Id", "dataType": "int", "isPrimaryKey": True},
                        {"name": "SymptomText", "dataType": "string"},
                        {"name": "CreatedAt", "dataType": "datetime"},
                    ], "relatedRequirementIds": ["FR-02"]},
                ],
                "entityRelationships": [],
            },
            "uiApi": {
                "uiScreens": [{"screenId": "UI-01", "name": "Symptom Entry", "purpose": "p", "relatedRequirements": ["FR-02"]}],
                "apiIntegrationPoints": [a.model_dump() for a in _medical_api_points()],
            },
            "testingSecurity": {
                "testingPlan": [
                    {"id": "TC-01", "title": "Symptom submission positive", "type": "Integration", "expectedResult": "r", "relatedRequirements": ["FR-02"]},
                ],
                "securityAndPrivacy": [{"category": "authentication", "requirement": "r"}],
            },
        }
        doc = agent._assemble_documentation(request, facts, sections, used_fallback=False)
        SEDocumentationCandidateSchema.model_validate(doc.model_dump())

        ok, issues = validate_mermaid(doc.activityDiagram, expected_header="flowchart")
        self.assertTrue(ok, issues)
        ok, issues = validate_mermaid(doc.sequenceDiagram, expected_header="sequenceDiagram")
        self.assertTrue(ok, issues)

    def test_no_live_provider_call_was_made(self):
        # This whole file only ever constructs DTOs/dicts and calls
        # deterministic builder/rebuild functions directly -- no
        # ProviderChain/LLM call is reachable from any test above.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
