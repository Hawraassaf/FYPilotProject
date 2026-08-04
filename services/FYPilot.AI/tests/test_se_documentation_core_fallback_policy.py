"""
Stabilization task Step C: strict core-section fallback rejection.

Core sections (must match SEDocumentationOrchestratorAgent._assemble_documentation's
own `section_keys` list and app/routers/se_documentation.py's _CORE_SECTION_KEYS):
requirements, useCases, modulesArchitecture, database, uiApi, testingSecurity.

Policy: ANY core section using deterministic fallback content -- or an
applicable-but-fallback AI technical report -- means this candidate must
never be presented as real, complete AI output, even when llmUsed=true and
the pipeline's own status is usable. No partial acceptance / tiering.

Two layers tested here:
1. `_has_core_section_fallback` (pure function, no mocking) -- the
   normalized decision itself.
2. `generate_se_documentation` (the router) -- proves the decision actually
   reaches the response's llmUsed/source/coreSectionFallback fields, via a
   monkeypatched ReviewPipeline.run so no network/provider call happens.

Run from services/FYPilot.AI:
    python -m unittest discover tests
    (or: python -m pytest tests/test_se_documentation_core_fallback_policy.py)
"""

import os
import sys
import unittest

import pytest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    SEDocumentationOrchestratorAgent,
)
from app.review.pipeline import ReviewPipeline  # noqa: E402
from app.routers.se_documentation import (  # noqa: E402
    SEDocumentationRequest,
    _has_core_section_fallback,
    generate_se_documentation,
)


def _provenance(**overrides) -> dict:
    """All 6 core sections real by default; override specific keys per test."""
    base = {
        "requirements": "provider",
        "useCases": "provider",
        "modulesArchitecture": "provider",
        "database": "provider",
        "uiApi": "provider",
        "testingSecurity": "provider",
    }
    base.update(overrides)
    return base


def _documentation(*, provenance=None, ai_applicable=False, ai_provenance=None) -> dict:
    doc = {"sectionProvenance": provenance if provenance is not None else _provenance()}
    doc["aiTechnicalReportApplicable"] = ai_applicable
    if ai_provenance is not None:
        doc["sectionProvenance"]["aiReport"] = ai_provenance
    return doc


class HasCoreSectionFallbackTests(unittest.TestCase):
    def test_zero_fallback_core_sections_is_accepted(self):
        self.assertFalse(_has_core_section_fallback(_documentation()))

    def test_requirements_fallback_is_rejected(self):
        self.assertTrue(_has_core_section_fallback(
            _documentation(provenance=_provenance(requirements="fallback"))
        ))

    def test_testing_security_fallback_is_rejected(self):
        self.assertTrue(_has_core_section_fallback(
            _documentation(provenance=_provenance(testingSecurity="fallback"))
        ))

    def test_two_fallback_core_sections_is_rejected(self):
        self.assertTrue(_has_core_section_fallback(
            _documentation(provenance=_provenance(database="fallback", uiApi="fallback"))
        ))

    def test_fallback_in_a_non_core_non_applicable_ai_report_does_not_reject(self):
        """aiReport is not a core section; when aiTechnicalReportApplicable
        is False, its provenance (if present at all) must not affect this
        decision -- explicitly tested per Step C's requirement."""
        self.assertFalse(_has_core_section_fallback(
            _documentation(ai_applicable=False, ai_provenance="fallback")
        ))

    def test_fallback_in_an_applicable_ai_report_is_rejected(self):
        """Documented decision: aiReport fallback while
        aiTechnicalReportApplicable=true is treated as an ADDITIONAL
        core-fallback condition -- a document must never describe its AI
        technical report as confirmed/complete when it is actually generic
        fallback text."""
        self.assertTrue(_has_core_section_fallback(
            _documentation(ai_applicable=True, ai_provenance="fallback")
        ))

    def test_applicable_ai_report_with_real_provenance_does_not_reject(self):
        self.assertFalse(_has_core_section_fallback(
            _documentation(ai_applicable=True, ai_provenance="provider")
        ))

    def test_missing_section_provenance_defaults_to_not_rejected(self):
        # No sectionProvenance at all (e.g. an older persisted shape) must
        # not crash and must not be treated as a fallback by default -- the
        # explicit-key check only rejects on an ACTUAL "fallback" value.
        self.assertFalse(_has_core_section_fallback({"aiTechnicalReportApplicable": False}))


# ---------------------------------------------------------------------------
# Router-level integration: proves the decision reaches the response's
# top-level fields, via a monkeypatched ReviewPipeline.run (no network call).
# ---------------------------------------------------------------------------

def _fake_review_result(documentation: dict):
    from app.review.models import PipelineResult

    return PipelineResult(
        status="approved",
        usable=True,
        output=documentation,
        outputOrigin="writer",
        outputReviewLevel="approved",
    )


class RouterCoreFallbackIntegrationTests(unittest.TestCase):
    def _run_with_documentation(self, monkeypatch, documentation: dict, *, llm_used: bool = True):
        """
        Simulates a genuinely successful Writer run (last_llm_used/
        last_provider set as generate_candidate() would set them for real)
        while never making a network/provider call: ReviewPipeline.run is
        replaced outright (so the writer_call_fn lambda it would normally
        invoke is never called), and SEDocumentationOrchestratorAgent's
        constructor is wrapped to set the same last_* attributes a real
        successful call would leave behind.
        """
        original_init = SEDocumentationOrchestratorAgent.__init__

        def patched_init(agent_self, *args, **kwargs):
            original_init(agent_self, *args, **kwargs)
            agent_self.last_llm_used = llm_used
            agent_self.last_provider = "deepinfra" if llm_used else None
            agent_self.last_model_used = "test-model" if llm_used else None

        monkeypatch.setattr(SEDocumentationOrchestratorAgent, "__init__", patched_init)

        def fake_run(self, writer_call_fn, context, **kwargs):
            return _fake_review_result(documentation)

        monkeypatch.setattr(ReviewPipeline, "run", fake_run)

        request = SEDocumentationRequest()
        return generate_se_documentation(request)

    def test_clean_candidate_reports_llm_used_true_and_no_core_fallback(self):
        mp = pytest.MonkeyPatch()
        try:
            documentation = _documentation()
            documentation["projectTitle"] = "Test"
            response = self._run_with_documentation(mp, documentation, llm_used=True)

            self.assertFalse(response["coreSectionFallback"])
            self.assertTrue(response["llmUsed"])
            self.assertNotIn("fallback", (response["source"] or "").lower())
        finally:
            mp.undo()

    def test_core_fallback_candidate_forces_llm_used_false_and_fallback_source_even_though_the_writer_really_succeeded(self):
        """The exact gap this guards against: a genuinely successful Writer
        call (last_llm_used=True) must NOT bypass rejection just because the
        top-level flag looks fine -- per-section fallback is checked
        independently of it."""
        mp = pytest.MonkeyPatch()
        try:
            documentation = _documentation(provenance=_provenance(requirements="fallback"))
            documentation["projectTitle"] = "Test"
            response = self._run_with_documentation(mp, documentation, llm_used=True)

            self.assertTrue(response["coreSectionFallback"])
            self.assertFalse(response["llmUsed"])
            self.assertIn("fallback", response["source"].lower())
        finally:
            mp.undo()


if __name__ == "__main__":
    unittest.main()
