"""
Regression tests for Idea Generator's grounding-semantics fix (the
"Important semantic check" from the pre-freeze production-hardening pass).

Root cause this guards against: ProjectIdeaAgent's `last_llm_used`/
`last_search_used`/`last_sources` are set the moment generate_ideas() runs a
real search/generation attempt -- UNCONDITIONALLY, regardless of whether
ReviewPipeline later discards that candidate (e.g. a leaked URL tripping
url_mode="no_urls_allowed", a Reviewer rejection with no earlier verified
version, etc.). Before this fix, routers/ideas.py's _response_to_dict read
those fields directly even when the router had already fallen back to
agent.build_safe_fallback(profile)'s generic templates -- so the API
response could claim "llmUsed": true, "searchUsed": true,
"groundedInLiveData": true, with real source URLs, right next to hardcoded
template ideas that have nothing to do with them.

The fix gates the three fields that describe the RETURNED ideas (llmUsed,
source, groundedInLiveData) on `result.usable`, while leaving every raw
search-telemetry field (searchUsed, searchFailed, searchProvider, sources,
...) exactly as agent state reports -- legitimate diagnostic telemetry about
the attempt itself, never removed.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.project_idea_agent import ProjectIdea, ProjectIdeaAgent  # noqa: E402
from app.routers.ideas import _response_to_dict  # noqa: E402


def _sample_idea() -> ProjectIdea:
    return ProjectIdea(
        title="Real Grounded Idea",
        problemStatement="A real problem statement.",
        targetUsers="Students",
        whyUseful="Helps students choose realistic projects.",
        lebaneseMarketRelevance="Relevant to Lebanese universities.",
        requiredTechnologies="ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
        requiredSkills="Database design",
        missingSkills="No major missing skills for MVP",
        difficultyLevel=3,
        innovationScore=80.0,
        innovationScoreReason="Scored 80/100.",
        feasibilityScore=80.0,
        feasibilityScoreReason="Scored 80/100.",
        marketDemandScore=80.0,
        marketDemandScoreReason="Scored 80/100.",
        expectedDurationWeeks=12,
        supervisorCategory="Computer Science",
        datasetNeeded="No for MVP",
        finalDeliverables="Web app, report",
        domain="Healthcare",
        lebaneseSector="Education",
    )


def _agent_with_successful_attempt() -> ProjectIdeaAgent:
    """A ProjectIdeaAgent instance whose internal state reflects a real,
    successful search + generation attempt -- mirrors exactly what
    generate_ideas() leaves behind on `agent` even when ReviewPipeline goes
    on to discard that candidate."""
    agent = ProjectIdeaAgent()
    agent.last_llm_used = True
    agent.last_provider = "deepinfra"
    agent.last_model_used = "anthropic/claude-opus-4-8"
    agent.last_search_used = True
    agent.last_search_failed = False
    agent.last_search_provider = "brave"
    agent.last_search_model_used = "brave-llm-context"
    agent.last_sources = [
        {
            "title": "World Bank Lebanon SME Report",
            "url": "https://worldbank.org/lebanon-sme-report",
            "snippet": "Real retrieved evidence.",
        },
    ]
    return agent


class UsableTrueReflectsRealGroundingTests(unittest.TestCase):
    def test_usable_true_reports_llm_used(self):
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent, usable=True)
        self.assertTrue(response["llmUsed"])

    def test_usable_true_reports_grounded_in_live_data(self):
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent, usable=True)
        self.assertTrue(response["groundedInLiveData"])

    def test_usable_true_source_is_not_dynamic_fallback(self):
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent, usable=True)
        self.assertNotEqual(response["source"], "dynamic-fallback")


class UsableFalseNeverClaimsFallbackIdeasAreGroundedTests(unittest.TestCase):
    """
    The exact bug: a real search+generation attempt succeeded (agent state
    still reflects it), but the pipeline discarded that candidate and the
    router is now returning agent.build_safe_fallback()'s generic templates
    instead. The response must not imply those templates are grounded.
    """

    def test_usable_false_llm_used_is_false(self):
        agent = _agent_with_successful_attempt()
        generic_fallback_ideas = agent.build_safe_fallback(
            profile=_fallback_profile()
        )["ideas"]

        response = _response_to_dict(generic_fallback_ideas, agent, usable=False)

        self.assertFalse(
            response["llmUsed"],
            "Generic fallback template ideas must never be reported as LLM-generated.",
        )

    def test_usable_false_grounded_in_live_data_is_false(self):
        agent = _agent_with_successful_attempt()
        generic_fallback_ideas = agent.build_safe_fallback(
            profile=_fallback_profile()
        )["ideas"]

        response = _response_to_dict(generic_fallback_ideas, agent, usable=False)

        self.assertFalse(
            response["groundedInLiveData"],
            "Generic fallback template ideas must never be reported as search-grounded.",
        )

    def test_usable_false_source_is_dynamic_fallback(self):
        agent = _agent_with_successful_attempt()
        generic_fallback_ideas = agent.build_safe_fallback(
            profile=_fallback_profile()
        )["ideas"]

        response = _response_to_dict(generic_fallback_ideas, agent, usable=False)

        self.assertEqual(response["source"], "dynamic-fallback")


class UsableFalsePreservesLegitimateTelemetryTests(unittest.TestCase):
    """
    The fix must not remove real diagnostic telemetry -- someone debugging
    "why did I get generic ideas despite search succeeding" must still be
    able to see that the search step itself DID succeed.
    """

    def test_search_used_survives_when_ideas_fall_back(self):
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent, usable=False)
        self.assertTrue(response["searchUsed"])

    def test_real_sources_survive_when_ideas_fall_back(self):
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent, usable=False)
        self.assertEqual(len(response["sources"]), 1)
        self.assertEqual(
            response["sources"][0]["url"], "https://worldbank.org/lebanon-sme-report",
        )


class DefaultUsableBackwardCompatibilityTests(unittest.TestCase):
    def test_default_usable_true_preserves_prior_behavior(self):
        """`usable` defaults to True so a caller that doesn't pass it keeps
        exactly the same result as before this fix existed."""
        agent = _agent_with_successful_attempt()
        response = _response_to_dict([_sample_idea()], agent)

        self.assertTrue(response["llmUsed"])
        self.assertTrue(response["groundedInLiveData"])


def _fallback_profile():
    from app.agents.project_idea_agent import StudentProfile

    return StudentProfile(
        studentSkills=["Python"],
        skillRatings={"Python": 3},
        major="Computer Science",
        experienceLevel=3,
        preferredDomain="Healthcare",
        targetDifficulty=3,
        availableHoursPerWeek=10,
        teamSize=1,
        projectGoals=["Build a useful final year project"],
    )


if __name__ == "__main__":
    unittest.main()
