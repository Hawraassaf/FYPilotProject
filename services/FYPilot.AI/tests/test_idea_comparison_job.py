"""
Unit tests for app/jobs/workers/idea_comparison_worker.py.

IdeaComparisonJobWorkerTests runs the real worker function end-to-end (no
mocking) against a job/reporter pair backed by a real AgentJobManager. No
DEEPINFRA_API_KEY/GROQ_API_KEY are set in the test environment, so every
cloud provider is naturally disabled (see each provider's
`self.enabled = bool(self.api_key)`) and OllamaProvider is skipped too (no
local Ollama running in CI) -- this exercises the genuine "every provider
failed" honest-fallback path deterministically, with zero network calls, the
same "call the real objects" style as test_review_pipeline.py.

IdeaComparisonRewriteFlowTests exercises the rewrite-on-rejection mechanism
added for the job-based flow, using `patch.object(ProviderChain,
"generate_json", ...)` -- the same monkeypatched-ProviderChain convention
test_fyp_chat_sources.py already uses -- so the Writer, Reviewer, and
Rewrite calls (all real ProviderChain.generate_json call sites, dispatched
here by inspecting the prompt text) return canned LLMResults instead of
making a real LLM call.

IdeaComparisonPromptContentTests are pure string assertions against the
actual generator/rewrite/rubric text (no LLM, no fakes) -- they exist to
catch a regression in the specific wording the semantic reviewer keys off
of (forbidding score-proxy language, requiring concrete evidence
categories), not to judge real model output.

IdeaComparisonBlockingPolicyTests unit-tests _classify_idea_comparison_issues
directly (no LLM, no fakes, no full worker run) -- this job path's own
blocking policy, deliberately separate from the shared ReviewDecisionEngine
(still used unchanged by every other agent and the synchronous endpoint),
which only blocks a comparison for a validated high/critical issue on the
closed blocking-category allowlist; everything else becomes a warning.

The provider-fallback/cancellation MECHANICS themselves (provider_started,
fallback_started, provider_succeeded/failed sequencing) are already covered
generically against fake providers in test_provider_chain_reporter.py,
which exercises the exact same ProviderChain._run_cascade this worker's
IdeaComparisonAgent relies on -- not duplicated here.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import json
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

# Force every cloud provider off regardless of the real environment this
# suite happens to run in, so this test is deterministic everywhere.
os.environ.pop("DEEPINFRA_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)
os.environ["OLLAMA_FALLBACK_ENABLED"] = "false"

from app.agents.project_idea_comparison import IdeaComparisonAgent, IdeaComparisonInput, IdeaComparisonRequest  # noqa: E402
from app.jobs.manager import AgentJobManager  # noqa: E402
from app.jobs.plans import stage_keys_for  # noqa: E402
from app.jobs.reporter import ProgressReporter  # noqa: E402
from app.jobs.workers import idea_comparison_worker  # noqa: E402
from app.jobs.workers.idea_comparison_worker import run_idea_comparison_job  # noqa: E402
from app.review.models import ReviewerFindings, ReviewerIssue  # noqa: E402
from app.review.registry import AGENT_REGISTRY, get_agent_config  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


@contextmanager
def _idea_comparison_blocking_enabled():
    """
    Temporarily flips the shared IdeaComparisonAgent registry entry to
    review_mode="blocking" with a 1-rewrite budget, exercising the
    rewrite-on-rejection mechanism that ships OFF by default -- see
    app/review/registry.py's IdeaComparisonAgent entry for why.
    """
    config = AGENT_REGISTRY["IdeaComparisonAgent"]
    with patch.object(config, "review_mode", "blocking"), \
         patch.object(config, "max_semantic_rewrites", 1):
        yield


def _build_request() -> IdeaComparisonRequest:
    return IdeaComparisonRequest(
        studentMajor="Computer Science",
        experienceLevel="intermediate",
        teamSize=1,
        availableHoursPerWeek=10,
        studentSkills=["Python", "ASP.NET Core"],
        skillRatings={"Python": 4},
        ideas=[
            IdeaComparisonInput(id=1, title="Clinic Triage Assistant", domain="healthcare"),
            IdeaComparisonInput(id=2, title="AgriTech Price Forecaster", domain="agriculture"),
        ],
    )


class IdeaComparisonJobWorkerTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "IdeaComparisonAgent", stage_keys_for("IdeaComparisonAgent"))
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_worker_returns_the_expected_result_shape(self):
        result = run_idea_comparison_job(_build_request(), self.reporter)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["agent"], "IdeaComparisonAgent")
        self.assertIn("comparison", result)
        self.assertIn("review", result)
        self.assertIn("generatedAt", result)

    def test_no_providers_available_produces_the_honest_unavailable_response_not_a_crash(self):
        result = run_idea_comparison_job(_build_request(), self.reporter)

        self.assertFalse(result["llmUsed"])
        # Never a fabricated idea-specific-looking result when every provider
        # failed -- an unusable result carries no comparison content at all
        # (see PipelineResult's usable=False -> empty-output contract), not
        # an "available": False shell with an empty ideas list.
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})

    def test_the_generate_stage_is_marked_failed_not_silently_completed_when_every_provider_fails(self):
        run_idea_comparison_job(_build_request(), self.reporter)

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["generate"], "failed")
        # A stage after the failed one must never appear completed just
        # because the worker kept running past it.
        self.assertIn(stage_states["review"], ("upcoming", "skipped"))

    def test_prepare_context_stage_completes_normally_before_the_provider_attempt(self):
        run_idea_comparison_job(_build_request(), self.reporter)

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["prepare_context"], "completed")

    def test_cancellation_requested_before_the_worker_runs_stops_it_from_reaching_generate(self):
        self.manager.request_cancel("job-1")

        result = run_idea_comparison_job(_build_request(), self.reporter)

        self.assertIsNone(result)  # signals "cancelled, no result" to the job-router wrapper
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertNotEqual(stage_states["generate"], "completed")

    def test_no_stage_state_snapshot_contains_a_percent_like_field(self):
        """Regression guard: even this fully-wired agent's snapshot never exposes a percentage."""
        run_idea_comparison_job(_build_request(), self.reporter)
        snapshot = self.manager.snapshot("job-1")
        for key in snapshot:
            self.assertNotIn("percent", key.lower())

    def test_global_deadline_is_one_budget_for_the_whole_job_not_per_provider_call(self):
        """The job path's whole point (vs the 45s synchronous endpoint) is one larger budget for the ENTIRE job, not per call -- see the module docstring."""
        self.assertEqual(idea_comparison_worker._GLOBAL_DEADLINE_SECONDS, 180.0)


# =============================================================================
# Prompt/rubric content -- no LLM, no fakes, pure string assertions
# =============================================================================

_SAMPLE_COMPARISON = {
    "comparisonTitle": "Best Project Match",
    "totalIdeasCompared": 2,
    "bestIdeaId": 1,
    "bestIdeaTitle": "Clinic Triage Assistant",
    "summary": "Idea 1 fits the student's skillset better than Idea 2.",
    "ideas": [
        {
            "ideaId": 1, "rank": 1, "title": "Clinic Triage Assistant",
            "contextSummary": "Healthcare triage tool using patient-reported symptoms.",
            "whyThisRank": "It has higher feasibility than the AgriTech idea.",
            "mainStrength": "Matches the student's existing ASP.NET and Python skills.",
            "mainRisk": "Requires careful handling of patient data privacy.",
            "bestFor": "A solo student comfortable with healthcare workflows.",
            "comparisonAdvantage": "Simpler scope than the AgriTech forecaster.",
            "requiredValidation": "Validate triage logic with a clinician.",
            "riskLevel": "Medium",
            "recommendation": "Proceed with the clinic triage assistant.",
        },
        {
            "ideaId": 2, "rank": 2, "title": "AgriTech Price Forecaster",
            "contextSummary": "Forecasts crop prices from historical market data.",
            "whyThisRank": "It ranks below Idea 1 because it needs external market data access.",
            "mainStrength": "Clear dataset-driven forecasting approach.",
            "mainRisk": "Depends on external price data availability.",
            "bestFor": "A student with strong data-analysis interest.",
            "comparisonAdvantage": "Broader market relevance than the triage tool.",
            "requiredValidation": "Confirm price data source access.",
            "riskLevel": "Medium",
            "recommendation": "Consider after validating dataset access.",
        },
    ],
    "finalRecommendation": "Choose the Clinic Triage Assistant given the student's current skillset.",
}


class IdeaComparisonPromptContentTests(unittest.TestCase):
    def setUp(self):
        self.agent = IdeaComparisonAgent()
        self.request = _build_request()

    def test_writer_prompt_forbids_score_proxy_language_and_lists_concrete_evidence_categories(self):
        prompt = self.agent._build_prompt(self.request)

        self.assertIn("student skill fit", prompt)
        self.assertIn("external dependencies", prompt)
        self.assertIn("dataset availability", prompt)
        self.assertIn("Lebanese context", prompt)
        self.assertIn("exactly as\nforbidden as writing the number itself", prompt)

    def test_writer_prompt_includes_good_and_bad_comparative_examples(self):
        prompt = self.agent._build_prompt(self.request)

        self.assertIn("GOOD example", prompt)
        self.assertIn("BAD example", prompt)
        self.assertIn("municipal data", prompt)
        self.assertIn("stronger feasibility and", prompt)
        self.assertIn("innovation than the other ideas", prompt)

    def test_reviewer_rubric_distinguishes_fabricated_score_from_concrete_evidence(self):
        rubric = get_agent_config("IdeaComparisonAgent").extra_rubric

        self.assertIn("fabricated_score", rubric)
        self.assertIn("would the sentence still make", rubric)
        self.assertIn("sense if the saved percentages did not exist", rubric)
        self.assertIn("concrete evidence, not a fabricated score", rubric)
        # Regression guard for the original over-triggering bug: domain
        # terminology or a shared tech stack alone must never be flagged.
        self.assertIn("expected and not generic", rubric)

    def test_rewrite_prompt_includes_original_comparison_issues_and_revision_instructions(self):
        issue = ReviewerIssue(
            severity="critical",
            requiresCorrection=True,
            category="fabricated_score",
            affectedField="ideas[0].whyThisRank",
            description="Restates the saved feasibility score instead of concrete evidence.",
            revisionInstruction="Cite a concrete dependency or skill-fit factor instead of 'higher feasibility'.",
        )

        prompt = self.agent.build_rewrite_prompt(self.request, _SAMPLE_COMPARISON, [issue])

        self.assertIn("Cite a concrete dependency or skill-fit factor", prompt)
        self.assertIn(_SAMPLE_COMPARISON["comparisonTitle"], prompt)
        self.assertIn("Idea IDs (ideaId) must be EXACTLY the same set", prompt)
        self.assertIn("GOOD example", prompt)


# =============================================================================
# Rewrite-on-rejection flow -- fakes via patch.object(ProviderChain, ...),
# matching test_fyp_chat_sources.py's established convention.
# =============================================================================

def _llm_result(data, *, provider="deepinfra", model="fake-model", ok=True, error=None) -> LLMResult:
    return LLMResult(ok=ok, provider=provider, model=model, text="", data=data, error=error)


def _rejecting_findings(revision_instruction: str = "Cite a concrete dependency instead of 'higher feasibility'.") -> dict:
    return {
        "strengths": ["Clear structure."],
        "issues": [
            {
                "severity": "critical",
                "requiresCorrection": True,
                "category": "fabricated_score",
                "affectedField": "ideas[0].whyThisRank",
                "description": "whyThisRank restates the saved feasibility score instead of concrete evidence.",
                "revisionInstruction": revision_instruction,
            }
        ],
        "qualityScore": 40,
        "overallAssessment": "Uses score-proxy language instead of concrete evidence.",
    }


def _approving_findings() -> dict:
    return {
        "strengths": ["Concrete, comparative reasoning."],
        "issues": [],
        "qualityScore": 88,
        "overallAssessment": "Grounded in concrete evidence.",
    }


def _make_fake_generate_json(*, writer_data, reviewer_data_sequence, rewrite_data=None, rewrite_ok=True):
    """
    One fake dispatched by prompt content, since the Writer/Rewrite calls
    (IdeaComparisonAgent.provider_chain) and the Reviewer calls
    (ReviewerAgent.provider_chain) are separate ProviderChain INSTANCES but
    the same patched CLASS method -- see ReviewerAgent.build_prompt's fixed
    "independent quality reviewer" opening line and
    IdeaComparisonAgent.build_rewrite_prompt's fixed "A semantic reviewer
    already" opening, both stable markers of which stage is calling.
    """
    state = {"reviewer_calls": 0}

    def fake_generate_json(
        self, prompt, *, use_search=False, max_tokens=None, deadline=None, reporter=None,
        cap_timeout_to_deadline=False, **_ignored_kwargs,
    ):
        if "independent quality reviewer" in prompt:
            index = min(state["reviewer_calls"], len(reviewer_data_sequence) - 1)
            state["reviewer_calls"] += 1
            return _llm_result(reviewer_data_sequence[index], provider="groq", model="fake-reviewer-model")

        if "A semantic reviewer already" in prompt:
            if not rewrite_ok:
                return _llm_result(None, ok=False, error="rewrite provider failed")
            return _llm_result(rewrite_data if rewrite_data is not None else writer_data)

        return _llm_result(writer_data)

    return fake_generate_json, state


class IdeaComparisonRewriteFlowTests(unittest.TestCase):
    """
    Exercises the rewrite-on-rejection mechanism itself with the shared
    IdeaComparisonAgent registry entry patched to review_mode="blocking" --
    this mechanism is OFF by default in production right now (see that
    registry entry's own comment), but stays fully tested here so it's
    ready to re-enable once review strictness is re-tuned.
    IdeaComparisonSemanticReviewStopGapTests below proves the actual
    production default (advisory mode) never rejects, using the real
    registry state with nothing patched.
    """

    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "IdeaComparisonAgent", stage_keys_for("IdeaComparisonAgent"))
        self.reporter = ProgressReporter("job-1", self.manager)

    def _run(self, *, global_deadline_seconds=None, **kwargs):
        fake_generate_json, state = _make_fake_generate_json(**kwargs)

        with _idea_comparison_blocking_enabled():
            if global_deadline_seconds is not None:
                with patch.object(ProviderChain, "generate_json", new=fake_generate_json), \
                     patch.object(idea_comparison_worker, "_GLOBAL_DEADLINE_SECONDS", global_deadline_seconds):
                    result = run_idea_comparison_job(_build_request(), self.reporter)
            else:
                with patch.object(ProviderChain, "generate_json", new=fake_generate_json):
                    result = run_idea_comparison_job(_build_request(), self.reporter)

        return result, state

    def test_first_review_approval_never_attempts_rewrite(self):
        result, state = self._run(writer_data=_SAMPLE_COMPARISON, reviewer_data_sequence=[_approving_findings()])

        self.assertEqual(result["review"]["status"], "approved")
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["review"], "completed")
        self.assertEqual(stage_states["rewrite"], "skipped")
        self.assertEqual(stage_states["final_checks"], "skipped")

    def test_rejection_with_usable_feedback_triggers_one_rewrite_and_approval(self):
        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings(), _approving_findings()],
            rewrite_data=_SAMPLE_COMPARISON,
        )

        self.assertEqual(result["review"]["status"], "approved_after_revision")
        self.assertTrue(result["review"]["usable"])
        self.assertEqual(state["reviewer_calls"], 2)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["review"], "failed")
        self.assertEqual(stage_states["rewrite"], "completed")
        self.assertEqual(stage_states["final_checks"], "completed")

    def test_rejection_with_no_usable_revision_instructions_skips_rewrite_and_uses_safe_fallback(self):
        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings(revision_instruction="")],
        )

        self.assertEqual(result["review"]["status"], "review_rejected_safe_fallback")
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})
        # Never attempted a rewrite (and therefore never a second review)
        # when the reviewer gave no actionable feedback to work with.
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["rewrite"], "skipped")

    def test_second_rejection_after_rewrite_returns_safe_fallback_without_a_third_attempt(self):
        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings(), _rejecting_findings()],
            rewrite_data=_SAMPLE_COMPARISON,
        )

        self.assertEqual(result["review"]["status"], "review_rejected_safe_fallback")
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})
        # Exactly one rewrite attempt ever -- a second rejection must never
        # trigger a third writer/rewrite call or a third review.
        self.assertEqual(state["reviewer_calls"], 2)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["rewrite"], "completed")
        self.assertEqual(stage_states["final_checks"], "failed")

    def test_rewrite_provider_failure_returns_rewrite_provider_unavailable_status(self):
        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings()],
            rewrite_ok=False,
        )

        self.assertEqual(result["review"]["status"], "rewrite_provider_unavailable")
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})
        # The rewrite call itself failed before a second review could ever run.
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["rewrite"], "failed")
        self.assertEqual(stage_states["final_checks"], "skipped")

    def test_deadline_gated_skip_when_insufficient_time_remains_for_a_rewrite(self):
        """
        70s comfortably clears prepare_context's 15s and the first review's
        60s minimums (the fakes are instant, so real elapsed time is
        milliseconds), but is under the rewrite gate's 90s minimum -- forces
        rewrite_unavailable_deadline without needing to fake real elapsed
        time.
        """
        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings()],
            global_deadline_seconds=70.0,
        )

        self.assertEqual(result["review"]["status"], "rewrite_unavailable_deadline")
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["rewrite"], "skipped")

    def test_deterministic_schema_failure_blocks_regardless_of_reviewer_output(self):
        """
        Deterministic/security failures (here: schema validation) are hard
        blockers "regardless of semantic review" per the looser policy's own
        spec -- even a writer output the Reviewer would have approved
        cleanly must never be shown if it fails structural validation.
        whyThisRank exceeding the 32-word limit fails
        IdeaComparisonCandidateSchema before the Reviewer is ever called.
        """
        overlong_comparison = json.loads(json.dumps(_SAMPLE_COMPARISON))
        overlong_comparison["ideas"][0]["whyThisRank"] = " ".join(["word"] * 40)

        result, state = self._run(
            writer_data=overlong_comparison,
            reviewer_data_sequence=[_approving_findings()],
        )

        self.assertEqual(result["review"]["status"], "schema_invalid")
        self.assertFalse(result["displayable"])
        self.assertEqual(result["comparison"], {})
        # The Reviewer must never even be called -- the schema check runs
        # (and blocks) before the "review" stage starts.
        self.assertEqual(state["reviewer_calls"], 0)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["generate"], "failed")
        self.assertEqual(stage_states["review"], "skipped")


class IdeaComparisonSemanticReviewStopGapTests(unittest.TestCase):
    """
    Proves the ACTUAL production default -- the shared IdeaComparisonAgent
    registry entry (review_mode="advisory", max_semantic_rewrites=0) is NOT
    patched here at all, unlike every other fake-provider test class in this
    file, which explicitly flips it to "blocking" to exercise the
    (currently dormant) rewrite mechanism. This is the one place that tests
    reality as it ships today: the semantic Reviewer runs and its findings
    are recorded, but even a critical, on-allowlist issue can never reject a
    real comparison or trigger a rewrite.
    """

    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "IdeaComparisonAgent", stage_keys_for("IdeaComparisonAgent"))
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_a_critical_blocking_category_issue_never_rejects_by_default(self):
        fake_generate_json, state = _make_fake_generate_json(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings()],  # severity=critical, category=fabricated_score
        )

        with patch.object(ProviderChain, "generate_json", new=fake_generate_json):
            result = run_idea_comparison_job(_build_request(), self.reporter)

        self.assertIn(result["review"]["status"], ("approved", "approved_with_warnings"))
        self.assertTrue(result["review"]["usable"])
        self.assertTrue(result["comparison"]["available"])
        # Never attempted a rewrite -- there was nothing to recover from,
        # since nothing ever blocked in the first place.
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["review"], "completed")
        self.assertEqual(stage_states["rewrite"], "skipped")


def _findings_with_one_issue(*, severity: str, category: str, requires_correction: bool = True) -> ReviewerFindings:
    return ReviewerFindings(
        strengths=[],
        issues=[
            ReviewerIssue(
                severity=severity,
                requiresCorrection=requires_correction,
                category=category,
                affectedField="ideas[0].whyThisRank",
                description="test issue",
                revisionInstruction="test fix",
            )
        ],
        qualityScore=70,
        overallAssessment="test",
    )


class IdeaComparisonBlockingPolicyTests(unittest.TestCase):
    """
    Direct unit tests of idea_comparison_worker._classify_idea_comparison_issues
    -- no LLM, no fakes, no worker run. This is the job path's own blocking
    policy (deliberately separate from the shared ReviewDecisionEngine, which
    every other agent and the synchronous endpoint still use unchanged): an
    issue only blocks when requiresCorrection is true AND severity is high or
    critical AND its category is on the closed blocking allowlist. Everything
    else becomes a warning instead of rejecting a good comparison.

    review_mode="blocking" is passed explicitly to every call here -- these
    tests exist to prove the classification RULES themselves are correct,
    independent of the registry's actual review_mode default (currently
    "advisory" in production, see that registry entry's own comment).
    """

    def test_medium_severity_generic_wording_does_not_block(self):
        findings = _findings_with_one_issue(severity="medium", category="repetitive_or_generic_comparison")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(len(decision.blockingIssues), 0)
        self.assertEqual(len(decision.warningIssues), 1)

    def test_low_severity_repetition_does_not_block(self):
        findings = _findings_with_one_issue(severity="low", category="repetitive_or_generic_comparison")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(len(decision.blockingIssues), 0)
        self.assertEqual(len(decision.warningIssues), 1)

    def test_high_severity_wrong_idea_comparison_blocks(self):
        findings = _findings_with_one_issue(severity="high", category="wrong_idea_comparison")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertTrue(decision.requiresRewrite)
        self.assertEqual(len(decision.blockingIssues), 1)
        self.assertEqual(len(decision.warningIssues), 0)

    def test_critical_severity_factual_contradiction_blocks(self):
        findings = _findings_with_one_issue(severity="critical", category="critical_factual_contradiction")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertTrue(decision.requiresRewrite)
        self.assertEqual(len(decision.blockingIssues), 1)
        self.assertEqual(len(decision.warningIssues), 0)

    def test_high_severity_off_allowlist_category_does_not_block(self):
        """A high/critical severity issue with requiresCorrection=true still only becomes a warning when its category isn't on the closed blocking allowlist."""
        findings = _findings_with_one_issue(severity="high", category="weak_comparison_wording")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(len(decision.warningIssues), 1)

    def test_requires_correction_false_never_blocks_even_at_critical_severity(self):
        findings = _findings_with_one_issue(
            severity="critical", category="critical_factual_contradiction", requires_correction=False,
        )

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(len(decision.warningIssues), 1)

    def test_no_issues_at_all_produces_no_blockers_and_no_warnings(self):
        findings = ReviewerFindings(strengths=[], issues=[], qualityScore=95, overallAssessment="clean")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="blocking")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(decision.blockingIssues, [])
        self.assertEqual(decision.warningIssues, [])

    def test_advisory_mode_never_blocks_even_for_an_on_allowlist_critical_issue(self):
        findings = _findings_with_one_issue(severity="critical", category="critical_factual_contradiction")

        decision = idea_comparison_worker._classify_idea_comparison_issues(findings, review_mode="advisory")

        self.assertFalse(decision.requiresRewrite)
        self.assertEqual(decision.blockingIssues, [])
        self.assertEqual(len(decision.warningIssues), 1)


class IdeaComparisonWarningApprovalTests(unittest.TestCase):
    """
    Full-flow tests (fakes, per IdeaComparisonRewriteFlowTests' convention)
    proving warnings-only outcomes are approved rather than rejected --
    with the shared registry entry patched to review_mode="blocking" for
    the same reason as IdeaComparisonRewriteFlowTests (the rewrite
    mechanism these tests exercise only ever triggers when blocking is
    enabled).
    """

    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "IdeaComparisonAgent", stage_keys_for("IdeaComparisonAgent"))
        self.reporter = ProgressReporter("job-1", self.manager)

    def _run(self, **kwargs):
        fake_generate_json, state = _make_fake_generate_json(**kwargs)
        with _idea_comparison_blocking_enabled(), \
             patch.object(ProviderChain, "generate_json", new=fake_generate_json):
            result = run_idea_comparison_job(_build_request(), self.reporter)
        return result, state

    def test_first_review_with_only_warning_issues_is_approved_with_warnings(self):
        warning_only_findings = {
            "strengths": [],
            "issues": [{
                "severity": "medium",
                "requiresCorrection": True,
                "category": "repetitive_or_generic_comparison",
                "affectedField": "ideas[0].comparisonAdvantage",
                "description": "a bit generic",
                "revisionInstruction": "be more specific",
            }],
            "qualityScore": 75,
            "overallAssessment": "mostly fine",
        }

        result, state = self._run(writer_data=_SAMPLE_COMPARISON, reviewer_data_sequence=[warning_only_findings])

        self.assertEqual(result["review"]["status"], "approved_with_warnings")
        self.assertTrue(result["review"]["usable"])
        self.assertTrue(result["comparison"]["available"])
        self.assertEqual(state["reviewer_calls"], 1)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["review"], "completed")
        self.assertEqual(stage_states["rewrite"], "skipped")

    def test_warnings_after_a_successful_rewrite_produce_approved_after_revision_with_warnings(self):
        warning_only_findings = {
            "strengths": [],
            "issues": [{
                "severity": "low",
                "requiresCorrection": True,
                "category": "insufficient_detail",
                "affectedField": "summary",
                "description": "could say a bit more",
                "revisionInstruction": "add one more concrete detail",
            }],
            "qualityScore": 80,
            "overallAssessment": "good after revision",
        }

        result, state = self._run(
            writer_data=_SAMPLE_COMPARISON,
            reviewer_data_sequence=[_rejecting_findings(), warning_only_findings],
            rewrite_data=_SAMPLE_COMPARISON,
        )

        self.assertEqual(result["review"]["status"], "approved_after_revision_with_warnings")
        self.assertTrue(result["review"]["usable"])
        self.assertTrue(result["comparison"]["available"])
        self.assertEqual(state["reviewer_calls"], 2)
        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["review"], "failed")
        self.assertEqual(stage_states["rewrite"], "completed")
        self.assertEqual(stage_states["final_checks"], "completed")


if __name__ == "__main__":
    unittest.main()
