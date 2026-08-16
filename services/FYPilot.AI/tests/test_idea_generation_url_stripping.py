"""
Regression tests for Idea Generator's URL-leak safety net (Gap 1 of the
pre-freeze production-hardening pass).

Root cause this guards against: ProjectIdeaAgent embeds real search-evidence
URLs into its Writer prompt (see project_idea_agent.py's RETRIEVED WEB
EVIDENCE block) and relies on a prompt instruction ("do not place citations
or URLs inside the idea fields") to keep them out of the generated idea
text. registry.py's ProjectIdeaAgent entry uses url_mode="no_urls_allowed",
the strictest output-firewall URL policy -- a single leaked URL anywhere in
the 4-idea candidate blocks the WHOLE batch at guarded_call's output-firewall
check (app/llm_firewall/rules/url_policy.check), BEFORE the Reviewer ever
runs, discarding a real, live-search-grounded generation for the generic
_fallback_raw_ideas() templates. MarketNeedsAgent already hit this exact
failure mode live and fixed it with a deterministic `_strip_urls()` -- this
suite proves ProjectIdeaAgent now has the equivalent guarantee via the
shared app.llm_firewall.rules.url_policy.strip_urls() helper, applied inside
_clean_text (the single choke point every narrative ProjectIdea field passes
through).

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

from app.agents.project_idea_agent import ProjectIdeaAgent, StudentProfile  # noqa: E402
from app.llm_firewall.firewall import LlmFirewall  # noqa: E402
from app.llm_firewall.rules.url_policy import strip_urls  # noqa: E402


def _profile() -> StudentProfile:
    return StudentProfile(
        studentSkills=["Python", "C#"],
        skillRatings={"Python": 4, "C#": 3},
        major="Computer Science",
        experienceLevel=3,
        preferredDomain="Healthcare",
        targetDifficulty=3,
        availableHoursPerWeek=12,
        teamSize=2,
        projectGoals=["Build a useful final year project"],
    )


class StripUrlsHelperTests(unittest.TestCase):
    """The shared helper itself (app/llm_firewall/rules/url_policy.py)."""

    def test_strips_a_bare_url(self):
        self.assertEqual(
            strip_urls("See more at https://example.com/page for details."),
            "See more at for details.",
        )

    def test_strips_a_parenthetical_url_and_removes_empty_parens(self):
        self.assertEqual(
            strip_urls("Infermedica (https://infermedica.com) is a competitor."),
            "Infermedica is a competitor.",
        )

    def test_strips_multiple_urls_in_one_string(self):
        self.assertEqual(
            strip_urls(
                "See https://one.example.com/report and also "
                "https://two.example.com/data for background."
            ),
            "See and also for background.",
        )

    def test_strips_url_immediately_followed_by_punctuation(self):
        self.assertEqual(
            strip_urls("Read the source: https://example.com/report."),
            "Read the source:",
        )

    def test_leaves_url_free_text_unchanged(self):
        self.assertEqual(strip_urls("Just plain text."), "Just plain text.")

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(strip_urls(""), "")

    def test_http_and_https_both_stripped(self):
        self.assertNotIn("http://", strip_urls("Legacy link: http://old.example.com/page here."))
        self.assertNotIn("https://", strip_urls("New link: https://new.example.com/page here."))


class CleanTextUrlStrippingTests(unittest.TestCase):
    """
    Carrier 1: the Writer LLM directly writing a URL/citation into a
    generated field despite the prompt instruction not to.
    """

    def test_clean_text_strips_a_leaked_url(self):
        agent = ProjectIdeaAgent()
        result = agent._clean_text(
            "This targets SMEs. Source: https://worldbank.org/report for evidence.",
            fallback="fallback text",
        )
        self.assertNotIn("http", result)
        self.assertIn("This targets SMEs", result)

    def test_clean_text_falls_back_when_field_is_nothing_but_a_url(self):
        agent = ProjectIdeaAgent()
        result = agent._clean_text("https://example.com/only-a-link", fallback="fallback text")
        self.assertEqual(result, "fallback text")

    def test_clean_text_still_applies_blocked_terms_fallback(self):
        """Unrelated existing safety check (blocked tech terms) must be
        untouched by the URL-stripping addition."""
        agent = ProjectIdeaAgent()
        result = agent._clean_text("Built using React and Node.js", fallback="safe fallback")
        self.assertEqual(result, "safe fallback")

    def test_required_technologies_field_also_gets_url_stripped(self):
        """_sanitize_technologies wraps _clean_text, so it inherits the fix
        without needing its own separate stripping logic."""
        agent = ProjectIdeaAgent()
        cleaned = agent._sanitize_technologies(
            agent._clean_text(
                "ASP.NET Core, PostgreSQL (see https://docs.example.com/stack)",
                fallback=", ".join(agent.allowed_stack),
            )
        )
        self.assertNotIn("http", cleaned)


class CompleteAndScoreEndToEndUrlStrippingTests(unittest.TestCase):
    """
    Carrier 2: a full raw idea dict (as if returned by the Writer LLM),
    proving no field of the final ProjectIdea can carry a URL through to
    the candidate that reaches guarded_call's output firewall.
    """

    def _raw_idea_with_leaked_urls(self) -> dict:
        return {
            "title": "SME Inventory Assistant",
            "problemStatement": (
                "Lebanese SMEs struggle with inventory tracking, see "
                "https://worldbank.org/lebanon-sme-report for evidence."
            ),
            "targetUsers": "Small business owners",
            "whyUseful": "Reduces waste and stockouts (https://example.com/study).",
            "lebaneseMarketRelevance": (
                "Directly addresses a documented gap -- source: https://gov.lb/report "
                "and https://itu.int/stats."
            ),
            "requiredTechnologies": "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
            "requiredSkills": "Database design, Python basics",
            "missingSkills": "No major missing skills for MVP",
            "difficultyLevel": "medium",
            "datasetNeeded": "Yes, a small inventory dataset (see https://data.example.com).",
            "finalDeliverables": "Web app, dashboard, report. Reference: https://ref.example.com.",
            "domain": "Healthcare",
            "lebaneseSector": "SMEs / Business",
        }

    def test_completed_idea_contains_no_urls_anywhere(self):
        agent = ProjectIdeaAgent()
        profile = _profile()
        idea = agent._complete_and_score(profile, self._raw_idea_with_leaked_urls())

        dumped = idea.model_dump()
        for field_name, value in dumped.items():
            if isinstance(value, str):
                self.assertNotIn(
                    "http", value,
                    f"Field '{field_name}' still contains a URL: {value!r}",
                )

    def test_completed_idea_preserves_surrounding_human_readable_text(self):
        agent = ProjectIdeaAgent()
        profile = _profile()
        idea = agent._complete_and_score(profile, self._raw_idea_with_leaked_urls())

        self.assertIn("Lebanese SMEs struggle with inventory tracking", idea.problemStatement)
        self.assertIn("Reduces waste and stockouts", idea.whyUseful)

    def test_completed_idea_passes_no_urls_allowed_firewall_check(self):
        """The actual bug this closes: the full candidate must no longer
        trip url_mode="no_urls_allowed" and discard the batch."""
        agent = ProjectIdeaAgent()
        profile = _profile()
        idea = agent._complete_and_score(profile, self._raw_idea_with_leaked_urls())

        candidate = {"ideas": [idea.model_dump()]}
        verdict = LlmFirewall().inspect_output(
            candidate, {}, url_mode="no_urls_allowed", allowed_sources=[],
        )

        self.assertFalse(
            verdict.has_blocking_finding(),
            f"A leaked URL still reached the candidate and would discard the whole batch: {verdict.findings}",
        )

    def test_four_idea_batch_with_one_leaked_url_survives(self):
        """Proves the specific scenario from the freeze report: a Writer
        that emits one URL in an otherwise valid 4-idea batch must not
        cause the whole batch to be discarded."""
        agent = ProjectIdeaAgent()
        profile = _profile()

        clean_template = {
            "title": "Clean Idea {n}",
            "problemStatement": "A real problem statement for idea {n}.",
            "targetUsers": "Students",
            "whyUseful": "It helps students.",
            "lebaneseMarketRelevance": "Useful for Lebanese universities.",
            "requiredTechnologies": "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL",
            "requiredSkills": "Database design",
            "missingSkills": "No major missing skills for MVP",
            "difficultyLevel": "medium",
            "datasetNeeded": "No for MVP",
            "finalDeliverables": "Web app, report",
            "domain": "Healthcare",
            "lebaneseSector": "Education",
        }

        raw_ideas = []
        for n in range(1, 4):
            raw = dict(clean_template)
            raw["title"] = clean_template["title"].format(n=n)
            raw["problemStatement"] = clean_template["problemStatement"].format(n=n)
            raw_ideas.append(raw)

        # The 4th idea is the one with a leaked citation -- otherwise valid.
        raw_ideas.append(self._raw_idea_with_leaked_urls())

        ideas = [agent._complete_and_score(profile, raw) for raw in raw_ideas]
        candidate = {"ideas": [idea.model_dump() for idea in ideas]}

        verdict = LlmFirewall().inspect_output(
            candidate, {}, url_mode="no_urls_allowed", allowed_sources=[],
        )

        self.assertFalse(verdict.has_blocking_finding())
        self.assertEqual(len(candidate["ideas"]), 4)
        # The real, distinct titles must have survived -- not replaced by
        # generic fallback templates.
        self.assertEqual(candidate["ideas"][0]["title"], "Clean Idea 1")
        self.assertEqual(candidate["ideas"][3]["title"], "SME Inventory Assistant")


class SourcesRemainUntouchedTests(unittest.TestCase):
    """
    URL stripping must apply ONLY to generated idea fields -- never to
    last_sources, the separately-returned, legitimate search-result URLs
    the API exposes to the student.
    """

    def test_last_sources_urls_are_never_stripped(self):
        agent = ProjectIdeaAgent()
        agent.last_sources = [
            {
                "title": "World Bank Lebanon SME Report",
                "url": "https://worldbank.org/lebanon-sme-report",
                "snippet": "Full report available at https://worldbank.org/lebanon-sme-report/full.",
            }
        ]

        # _complete_and_score never mutates last_sources.
        agent._complete_and_score(_profile(), {
            "title": "Some Idea", "problemStatement": "Some problem.",
        })

        self.assertEqual(
            agent.last_sources[0]["url"],
            "https://worldbank.org/lebanon-sme-report",
        )
        self.assertIn("https://", agent.last_sources[0]["snippet"])


class UnrelatedUnsafeContentStillBlockedTests(unittest.TestCase):
    """
    Proves the URL-stripping fix is narrowly scoped: a candidate unsafe for
    a DIFFERENT reason (e.g. a genuine secret pattern) must still be
    rejected by the firewall exactly as before -- this fix never silently
    repairs unrelated unsafe content.
    """

    def test_secret_pattern_in_idea_field_is_still_blocked(self):
        agent = ProjectIdeaAgent()
        profile = _profile()

        # A Groq-style API key pattern (critical/block, see
        # app/llm_firewall/rules/secrets.py) -- deliberately avoids any word
        # from ProjectIdeaAgent's own pre-existing blocked_terms list (aws,
        # azure, react, ...), which would trigger a DIFFERENT, unrelated
        # fallback path and defeat the point of this test.
        raw = {
            "title": "Config Leak Idea",
            "problemStatement": (
                "A leaked token gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 was found in the sample repo."
            ),
        }
        idea = agent._complete_and_score(profile, raw)
        candidate = {"ideas": [idea.model_dump()]}

        verdict = LlmFirewall().inspect_output(
            candidate, {}, url_mode="no_urls_allowed", allowed_sources=[],
        )

        self.assertTrue(
            verdict.has_blocking_finding(),
            "A genuine secret pattern must still be blocked -- URL stripping "
            "must never mask unrelated unsafe content.",
        )


if __name__ == "__main__":
    unittest.main()
