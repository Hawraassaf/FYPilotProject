"""
Integration tests for the AI-fallback search-intent wiring inside
FypMentorAgent.chat() (app/agents/fyp_mentor_agent.py) -- confirms:

- the deterministic classifier's "yes" is trusted as-is, and the AI fallback
  is never even attempted in that case (no added latency/cost for the
  common, unambiguous case);
- when the deterministic classifier says "no", the AI fallback IS
  consulted, and a "yes" from it makes chat() actually run a real search;
- last_search_classification_source correctly reports which classifier
  produced the decision, for observability.

No real network/LLM calls -- ProviderChain.generate_json and .search_web are
monkeypatched directly on the instance, matching this repo's existing
convention (see test_fyp_mentor_web_search_firewall.py).

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.fyp_mentor_agent import FypMentorAgent, FypMentorRequest  # noqa: E402
from app.services.llm_provider import LLMResult  # noqa: E402


def _mentor_answer_result(reply: str = "A mentoring answer.") -> LLMResult:
    return LLMResult(
        ok=True, provider="deepinfra", model="Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo", text="",
        data={
            "reply": reply, "intent": "general_fyp_help", "usedContext": [],
            "suggestedNextActions": [], "warning": "", "confidence": 80,
            "assumptions": [], "codeBlocks": [],
        },
    )


def _classification_result(requires_search: bool, intent: str | None = None) -> LLMResult:
    return LLMResult(
        ok=True, provider="deepinfra", model="m", text="",
        data={"requiresSearch": requires_search, "intent": intent, "reason": "test"},
    )


def _search_result(sources: list[dict]) -> LLMResult:
    return LLMResult(
        ok=True, provider="brave", model="brave-llm-context", text="",
        data=None, search_used=True, sources=sources,
    )


class DeterministicYesSkipsAiFallbackTests(unittest.TestCase):
    def test_deterministic_yes_never_calls_the_ai_fallback(self):
        """'What is the latest version of ASP.NET Core?' already matches the
        deterministic classifier -- the AI fallback classification call
        must never be made, so generate_json is called exactly ONCE (the
        real answer), never twice."""
        agent = FypMentorAgent()
        generate_json_calls: list[str] = []

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None, **kwargs):
            generate_json_calls.append(prompt)
            return _mentor_answer_result()

        agent.provider_chain.generate_json = fake_generate_json
        agent.provider_chain.search_web = lambda *_a, **_kw: _search_result(
            [{"title": "ASP.NET Core docs", "url": "https://learn.microsoft.com/aspnet/core", "snippet": "x"}]
        )

        agent.chat(FypMentorRequest(message="What is the latest version of ASP.NET Core?"))

        self.assertEqual(len(generate_json_calls), 1, "AI fallback must not run when the deterministic classifier already said yes")
        self.assertEqual(agent.last_search_classification_source, "deterministic")
        self.assertTrue(agent.last_search_used)


class AiFallbackTriggersRealSearchTests(unittest.TestCase):
    def test_ai_fallback_yes_makes_chat_run_a_real_search(self):
        """A message the deterministic classifier misses entirely (no
        trigger phrase anywhere) but the AI fallback correctly identifies as
        needing search must result in a REAL search_web() call and real
        sources reaching the final answer -- not just an internal flag."""
        agent = FypMentorAgent()

        call_log: list[str] = []

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None, **kwargs):
            call_log.append("generate_json")
            if "classifier deciding whether" in prompt:
                # This is the AI-fallback classification call.
                return _classification_result(True, intent="competitors")
            return _mentor_answer_result("Companies X and Y do this already.")

        search_calls: list[str] = []

        def fake_search_web(query, **kwargs):
            search_calls.append(query)
            return _search_result([{"title": "Competitor list", "url": "https://example.com/competitors", "snippet": "x"}])

        agent.provider_chain.generate_json = fake_generate_json
        agent.provider_chain.search_web = fake_search_web

        # Deliberately NOT matching any deterministic trigger phrase.
        message = "Who are the leading companies doing this already?"
        answer = agent.chat(FypMentorRequest(message=message))

        self.assertEqual(len(search_calls), 1, "the AI fallback's yes must actually trigger a real search")
        self.assertEqual(agent.last_search_classification_source, "ai_fallback")
        self.assertEqual(agent.last_search_intent, "competitors")
        self.assertTrue(agent.last_search_used)
        self.assertEqual(len(agent.last_sources), 1)

    def test_ai_fallback_no_leaves_search_disabled(self):
        agent = FypMentorAgent()

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None, **kwargs):
            if "classifier deciding whether" in prompt:
                return _classification_result(False)
            return _mentor_answer_result()

        def fail_if_called_search_web(*_a, **_kw):
            raise AssertionError("search_web must not be called when the AI fallback also says no")

        agent.provider_chain.generate_json = fake_generate_json
        agent.provider_chain.search_web = fail_if_called_search_web

        agent.chat(FypMentorRequest(message="What should I build first in my project?"))

        self.assertFalse(agent.last_search_used)
        self.assertIsNone(agent.last_search_classification_source)

    def test_ai_fallback_provider_failure_leaves_search_disabled_not_crashed(self):
        agent = FypMentorAgent()

        def fake_generate_json(prompt, *, use_search=False, max_tokens=None, **kwargs):
            if "classifier deciding whether" in prompt:
                return LLMResult(ok=False, provider="none", model=None, text="", data=None, error="down")
            return _mentor_answer_result()

        def fail_if_called_search_web(*_a, **_kw):
            raise AssertionError("search_web must not be called when the AI fallback itself failed")

        agent.provider_chain.generate_json = fake_generate_json
        agent.provider_chain.search_web = fail_if_called_search_web

        answer = agent.chat(FypMentorRequest(message="Is this approach still used in the industry today?"))

        self.assertFalse(agent.last_search_used)
        self.assertIsNotNone(answer)  # chat() must still produce a usable answer, not crash


if __name__ == "__main__":
    unittest.main()
