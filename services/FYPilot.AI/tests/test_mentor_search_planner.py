"""
Unit tests for app/agents/mentor_search_planner.py -- deterministic search-
intent classification, query planning, and result-quality scoring.

Covers the acceptance criteria from the Mentor Chat production-rebuild task:
- academic-source requests must not be hijacked by the selected project's
  title/domain (the exact live-observed bug this module fixes);
- technical/dataset requests DO benefit from project context;
- generic advice questions never trigger search at all;
- source authority/relevance scoring favors scholarly evidence for academic
  requests and never treats a commercial source-code marketplace as academic
  evidence;
- weak/no evidence is honestly reported, never silently upgraded.

No network calls, no LLM calls -- purely deterministic, so no mocking of
ProviderChain is needed here (see test_fyp_mentor_web_search_firewall.py /
test_mentor_chat_writer_deadline.py for the agent-level integration tests).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import time
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.mentor_search_planner import (  # noqa: E402
    assess_quality,
    build_refined_query,
    build_search_query,
    classify_search_intent,
    classify_search_intent_via_ai,
    score_sources,
    select_sources,
)
from app.services.llm_provider import LLMResult  # noqa: E402


class _FakeProviderChain:
    """Stands in for a real ProviderChain -- records what generate_json was
    called with, without any network call."""

    def __init__(self, result: LLMResult | Exception):
        self._result = result
        self.calls: list[dict] = []

    def generate_json(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _ai_result(requires_search: bool, intent: str | None = None, reason: str = "because") -> LLMResult:
    return LLMResult(
        ok=True, provider="deepinfra", model="m", text="",
        data={"requiresSearch": requires_search, "intent": intent, "reason": reason},
    )


class SearchIntentClassificationTests(unittest.TestCase):
    def test_academic_supervision_request_is_academic_research(self):
        decision = classify_search_intent(
            "Find academic sources on final year project supervision best practices."
        )
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "academic_research")

    def test_dataset_request_is_datasets_intent(self):
        decision = classify_search_intent("Find datasets for my project.")
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "datasets")

    def test_technical_library_question_is_tools_libraries(self):
        decision = classify_search_intent(
            "Which NLP libraries are best for Arabic symptom classification?"
        )
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "tools_libraries")

    def test_generic_advice_question_requires_no_search(self):
        for message in [
            "What should I work on next?",
            "Explain my roadmap.",
            "Is my current scope too large?",
            "How can I divide tasks with my teammate?",
            "Explain what functional requirements are.",
            "How should I prepare for my supervisor meeting?",
        ]:
            with self.subTest(message=message):
                decision = classify_search_intent(message)
                self.assertFalse(decision.requires_search, message)
                self.assertIsNone(decision.intent)

    def test_current_version_question_is_current_technology(self):
        decision = classify_search_intent("What is the latest version of ASP.NET Core?")
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "current_technology")

    def test_market_demand_question_requires_search(self):
        decision = classify_search_intent("What is the market demand for this idea?")
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "market_demand")

    def test_explicit_search_online_imperative_not_covered_by_any_category_still_triggers_search(self):
        """Live-caught gap: 'Search online for Lebanon-based supplier
        directories' matched none of the 13 specific categories, so
        requires_search stayed False, no search evidence was ever added to
        the prompt, and the Writer told the student it couldn't search and
        to go look it up themselves -- exactly the anti-pattern the system
        prompt exists to prevent. An explicit search imperative must never
        be silently dropped just because it doesn't fit a specific bucket."""
        decision = classify_search_intent("Search online for Lebanon-based supplier directories")
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "general_web")

    def test_other_explicit_search_phrasings_trigger_search(self):
        for message in [
            "Can you google this for me?",
            "Please look this up for me.",
            "Search the web for competing platforms in this space.",
        ]:
            with self.subTest(message=message):
                decision = classify_search_intent(message)
                self.assertTrue(decision.requires_search, message)

    def test_current_as_plain_adjective_before_technology_noun_requires_search(self):
        """Live-caught gap: 'current' used as a plain adjective ('current
        NLP libraries') is distinct phrasing from 'current version' and was
        previously missed entirely, silently skipping search for a question
        that explicitly asks about CURRENT/live technology choices."""
        decision = classify_search_intent(
            "What current NLP libraries would be suitable for Arabic medical symptom classification?"
        )
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "current_technology")


class QueryPlanningTests(unittest.TestCase):
    def test_academic_request_does_not_use_project_title_or_domain(self):
        """The exact live-observed bug: a technical project's title/domain
        must never dominate an academic-research query."""
        decision = classify_search_intent(
            "Find academic sources on final year project supervision best practices."
        )
        plan = build_search_query(
            decision,
            "Find academic sources on final year project supervision best practices.",
            idea_title="Arabic Medical Symptom Triage Assistant",
            idea_domain="Healthcare AI",
            idea_technologies="Python, spaCy, FastAPI",
        )

        self.assertFalse(plan.project_title_used)
        self.assertFalse(plan.project_domain_used)
        self.assertNotIn("Arabic Medical Symptom Triage Assistant", plan.primary_query)
        self.assertNotIn("Healthcare AI", plan.primary_query)
        self.assertIn("supervision", plan.primary_query.lower())

    def test_thin_dataset_request_pulls_in_project_context(self):
        """'Find datasets for my project' has no queryable content on its
        own -- project context is essential here, unlike academic_research."""
        decision = classify_search_intent("Find datasets for my project.")
        plan = build_search_query(
            decision,
            "Find datasets for my project.",
            idea_title="Arabic Medical Symptom Triage Assistant",
            idea_domain="Healthcare AI",
            idea_technologies="",
        )

        self.assertTrue(plan.project_domain_used or plan.project_title_used)
        self.assertTrue(
            "Healthcare AI" in plan.primary_query
            or "Arabic Medical Symptom Triage Assistant" in plan.primary_query
        )

    def test_thin_dataset_request_with_naturally_phrased_project_reference(self):
        """Live-caught gap: 'that could support this project' is not the
        literal 'for my project' pattern the stripper originally looked for,
        and the leftover connector words (could/support/project) inflated
        the raw word count past the old thinness threshold, so project
        context was never blended in -- the search returned generic 'how to
        find datasets' tutorials instead of anything about this project's
        domain."""
        decision = classify_search_intent("Find datasets that could support this project.")
        plan = build_search_query(
            decision,
            "Find datasets that could support this project.",
            idea_title="Arabic Medical Symptom Triage Assistant",
            idea_domain="Healthcare AI",
        )

        self.assertTrue(plan.project_domain_used or plan.project_title_used)

    def test_self_sufficient_technical_question_does_not_need_project_title(self):
        """The student's own wording already names the domain (Arabic
        symptom classification) -- the project TITLE should not need to be
        force-appended on top of that."""
        decision = classify_search_intent(
            "Which NLP libraries are best for Arabic symptom classification?"
        )
        plan = build_search_query(
            decision,
            "Which NLP libraries are best for Arabic symptom classification?",
            idea_title="Arabic Medical Symptom Triage Assistant",
            idea_domain="Healthcare AI",
            idea_technologies="",
        )

        self.assertIn("arabic", plan.primary_query.lower())
        self.assertIn("symptom", plan.primary_query.lower())
        self.assertFalse(plan.project_title_used)

    def test_student_question_always_leads_the_query(self):
        decision = classify_search_intent("Find datasets for my project.")
        plan = build_search_query(
            decision, "Find datasets for my project.",
            idea_title="Some Project", idea_domain="Some Domain",
        )
        # The cleaned student wording ("Find datasets") must appear before
        # any appended project context -- Brave truncates from the END, so
        # whatever is appended is the first thing ever lost.
        query_lower = plan.primary_query.lower()
        self.assertLess(query_lower.index("dataset"), query_lower.index("some domain"))

    def test_refined_academic_query_differs_from_primary_and_drops_project_reference(self):
        decision = classify_search_intent("Find academic sources on FYP supervision.")
        plan = build_search_query(decision, "Find academic sources on FYP supervision for my project.")
        refined = build_refined_query(plan)
        self.assertIsNotNone(refined)
        self.assertNotEqual(refined, plan.primary_query)
        self.assertNotIn("for my project", refined.lower())


class SourceQualityScoringTests(unittest.TestCase):
    def _academic_decision_and_plan(self):
        decision = classify_search_intent(
            "Find academic sources on final year project supervision best practices."
        )
        plan = build_search_query(
            decision, "Find academic sources on final year project supervision best practices.",
        )
        return decision, plan

    def test_commercial_marketplace_never_counted_as_academic_evidence(self):
        decision, plan = self._academic_decision_and_plan()
        sources = [
            {
                "title": "Student Attendance Management System Project in C# .NET with Source Code",
                "url": "https://www.kashipara.com/project/c-net/5020/student-attendance-management-system",
                "snippet": "Download the complete source code for a student project.",
            },
            {
                "title": "Techprofree source code project",
                "url": "https://www.techprofree.com/some-project-with-source-code",
                "snippet": "Free download project source code.",
            },
        ]

        scored = score_sources(sources, decision, plan)
        selected = select_sources(scored)

        self.assertEqual(selected, [])
        self.assertEqual(assess_quality(scored, decision), "weak")

    def test_scholarly_and_university_sources_rank_above_commercial_marketplace(self):
        decision, plan = self._academic_decision_and_plan()
        sources = [
            {
                "title": "Student project source code download",
                "url": "https://www.kashipara.com/project/download",
                "snippet": "with source code free download",
            },
            {
                "title": "Supervision practices in undergraduate capstone projects",
                "url": "https://dl.acm.org/doi/10.1145/some-paper",
                "snippet": "A peer-reviewed study of capstone project supervision best practices.",
            },
            {
                "title": "FYP Supervision Guidelines",
                "url": "https://www.some-university.edu/fyp-supervision-guidelines",
                "snippet": "University guide to final year project supervision.",
            },
        ]

        scored = score_sources(sources, decision, plan)
        selected = select_sources(scored)

        self.assertGreaterEqual(len(selected), 2)
        top_domains = [s.domain for s in selected]
        self.assertNotIn("www.kashipara.com", top_domains)
        self.assertEqual(
            {s.source_type for s in selected}, {"scholarly"},
        )
        self.assertEqual(assess_quality(scored, decision), "strong")

    def test_weak_evidence_is_reported_as_weak_not_strong(self):
        decision, plan = self._academic_decision_and_plan()
        sources = [
            {
                "title": "Random unrelated blog post",
                "url": "https://randomblog.example.com/post-1",
                "snippet": "Some unrelated musings about productivity.",
            },
        ]
        scored = score_sources(sources, decision, plan)
        self.assertIn(assess_quality(scored, decision), ("weak", "partial"))
        self.assertNotEqual(assess_quality(scored, decision), "strong")

    def test_no_sources_is_none_quality(self):
        decision, plan = self._academic_decision_and_plan()
        self.assertEqual(assess_quality([], decision), "none")

    def test_dedup_keeps_distinct_results_from_the_same_domain(self):
        """Same-domain must never be treated as duplicate on its own -- a
        domain like researchgate.net or arxiv.org legitimately hosts many
        distinct results for one query."""
        decision, plan = self._academic_decision_and_plan()
        sources = [
            {
                "title": "First distinct paper on FYP supervision",
                "url": "https://www.researchgate.net/publication/1111",
                "snippet": "Academic study of supervision approaches.",
            },
            {
                "title": "Second distinct paper on capstone mentoring",
                "url": "https://www.researchgate.net/publication/2222",
                "snippet": "Academic study of capstone mentoring practices.",
            },
        ]
        scored = score_sources(sources, decision, plan)
        selected = select_sources(scored)
        self.assertEqual(len(selected), 2)

    def test_source_count_capped(self):
        decision = classify_search_intent("What is the latest version of ASP.NET Core?")
        plan = build_search_query(decision, "What is the latest version of ASP.NET Core?")
        sources = [
            {"title": f"Result {i}", "url": f"https://docs.example.com/page-{i}", "snippet": "asp net core version"}
            for i in range(10)
        ]
        scored = score_sources(sources, decision, plan)
        selected = select_sources(scored, max_results=6)
        self.assertEqual(len(selected), 6)


class AiFallbackClassifierTests(unittest.TestCase):
    """
    classify_search_intent_via_ai() is ONLY ever called by FypMentorAgent.
    chat() after the deterministic classifier already said "no" -- these
    tests exercise the function in isolation (fake provider, no real LLM
    call) against every failure mode it must fail SAFE on.
    """

    def test_ai_says_yes_returns_a_requires_search_decision(self):
        chain = _FakeProviderChain(_ai_result(True, intent="competitors", reason="asks about existing companies"))
        decision = classify_search_intent_via_ai("Who are the leading companies doing this already?", chain)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "competitors")

    def test_ai_says_no_returns_a_no_search_decision_not_none(self):
        chain = _FakeProviderChain(_ai_result(False, reason="pure project planning question"))
        decision = classify_search_intent_via_ai("What should I build first?", chain)

        self.assertIsNotNone(decision)
        self.assertFalse(decision.requires_search)

    def test_unrecognized_intent_value_falls_back_to_general_web(self):
        chain = _FakeProviderChain(_ai_result(True, intent="something_the_model_made_up"))
        decision = classify_search_intent_via_ai("some message", chain)

        self.assertTrue(decision.requires_search)
        self.assertEqual(decision.intent, "general_web")

    def test_provider_failure_returns_none_not_a_crash(self):
        chain = _FakeProviderChain(LLMResult(ok=False, provider="none", model=None, text="", data=None, error="down"))
        decision = classify_search_intent_via_ai("some message", chain)
        self.assertIsNone(decision)

    def test_provider_exception_returns_none_not_a_crash(self):
        chain = _FakeProviderChain(RuntimeError("network exploded"))
        decision = classify_search_intent_via_ai("some message", chain)
        self.assertIsNone(decision)

    def test_malformed_response_shape_returns_none(self):
        chain = _FakeProviderChain(LLMResult(ok=True, provider="deepinfra", model="m", text="", data={"unexpected": "shape"}))
        decision = classify_search_intent_via_ai("some message", chain)
        self.assertIsNone(decision)

    def test_non_dict_data_returns_none(self):
        chain = _FakeProviderChain(LLMResult(ok=True, provider="deepinfra", model="m", text="", data=None))
        decision = classify_search_intent_via_ai("some message", chain)
        self.assertIsNone(decision)

    def test_skipped_entirely_when_insufficient_deadline_remains(self):
        chain = _FakeProviderChain(_ai_result(True, intent="general_web"))
        near_expired_deadline = time.monotonic() + 2.0  # well under the 8.0s floor

        decision = classify_search_intent_via_ai("some message", chain, deadline=near_expired_deadline)

        self.assertIsNone(decision)
        self.assertEqual(chain.calls, [], "must not even attempt the call when too little deadline remains")

    def test_attempted_when_deadline_comfortably_sufficient(self):
        chain = _FakeProviderChain(_ai_result(True, intent="general_web"))
        healthy_deadline = time.monotonic() + 60.0

        decision = classify_search_intent_via_ai("some message", chain, deadline=healthy_deadline)

        self.assertIsNotNone(decision)
        self.assertEqual(len(chain.calls), 1)
        self.assertIn("deadline", chain.calls[0])
        self.assertTrue(chain.calls[0].get("cap_timeout_to_deadline"))

    def test_never_uses_web_search_itself(self):
        """The classification call itself must never trigger a live search
        -- it is a plain generation call judging INTENT, not a search."""
        chain = _FakeProviderChain(_ai_result(True, intent="general_web"))
        classify_search_intent_via_ai("some message", chain)
        self.assertFalse(chain.calls[0]["use_search"])


if __name__ == "__main__":
    unittest.main()
