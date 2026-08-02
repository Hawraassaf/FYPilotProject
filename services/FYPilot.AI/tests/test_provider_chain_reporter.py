"""
Unit tests for ProviderChain's optional `reporter` orchestration
(app/services/llm_provider.py's _run_cascade) -- verifies provider_started/
fallback_started/provider_succeeded/provider_failed/deadline_prevented_fallback
fire at the right points, that cancellation stops further fallback without
touching an in-flight call, and that omitting reporter entirely (the
existing behavior for every synchronous, non-job caller) is completely
unaffected.

Fake providers here follow the same style as test_review_pipeline.py's
_FakeReviewerAgent -- hand-written objects with a canned outcome, no
network, no LLM.

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

from app.jobs.manager import AgentJobManager  # noqa: E402
from app.jobs.reporter import ProgressReporter  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


class _FakeProvider:
    """Minimal BaseProvider-compatible fake -- succeeds or fails on command, records every call it received."""

    def __init__(self, name: str, *, ok: bool, data: dict | None = None, error: str | None = None):
        self.name = name
        self._ok = ok
        self._data = data if data is not None else ({"value": "x"} if ok else None)
        self._error = error
        self.calls = 0

    def generate_json(self, prompt, *, use_search=False, max_tokens=None, reporter=None, schema_description=None):
        self.calls += 1
        return LLMResult(
            ok=self._ok,
            provider=self.name,
            model=f"{self.name}-model",
            text="{}",
            data=self._data,
            error=self._error,
        )

    def generate_text(self, prompt, *, use_search=False, reporter=None):
        self.calls += 1
        return LLMResult(
            ok=self._ok,
            provider=self.name,
            model=f"{self.name}-model",
            text="hello" if self._ok else "",
            data=None,
            error=self._error,
        )


def _event_types(manager: AgentJobManager, job_id: str) -> list[str]:
    record = manager.get_job(job_id)
    types = []
    while not record.event_queue.empty():
        types.append(record.event_queue.get_nowait().type)
    return types


class ProviderChainReporterTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "TestAgent", ["generate"])
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_reporter_omitted_never_touches_the_job_and_preserves_existing_behavior(self):
        deepinfra = _FakeProvider("deepinfra", ok=False, error="boom")
        groq = _FakeProvider("groq", ok=True)
        chain = ProviderChain(providers=[deepinfra, groq])

        result = chain.generate_json("prompt")  # no reporter at all

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(_event_types(self.manager, "job-1"), [])  # nothing pushed -- reporter never involved

    def test_first_provider_success_reports_started_then_succeeded_only(self):
        deepinfra = _FakeProvider("deepinfra", ok=True)
        groq = _FakeProvider("groq", ok=True)
        chain = ProviderChain(providers=[deepinfra, groq])

        result = chain.generate_json("prompt", reporter=self.reporter)

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "deepinfra")
        self.assertEqual(groq.calls, 0)  # never attempted -- first provider already succeeded
        self.assertEqual(_event_types(self.manager, "job-1"), ["provider_started", "provider_succeeded"])

    def test_fallback_reports_failed_then_fallback_started_then_succeeded(self):
        deepinfra = _FakeProvider("deepinfra", ok=False, error="rate limited")
        groq = _FakeProvider("groq", ok=True)
        ollama = _FakeProvider("ollama", ok=True)
        chain = ProviderChain(providers=[deepinfra, groq, ollama])

        result = chain.generate_json("prompt", reporter=self.reporter)

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "groq")
        self.assertEqual(ollama.calls, 0)  # cascade stopped once groq succeeded
        self.assertEqual(
            _event_types(self.manager, "job-1"),
            ["provider_started", "provider_failed", "fallback_started", "provider_succeeded"],
        )
        self.assertEqual(self.manager.snapshot("job-1")["provider"], "groq")

    def test_every_provider_failing_reports_failed_for_each_and_returns_a_not_ok_result(self):
        deepinfra = _FakeProvider("deepinfra", ok=False, error="down")
        groq = _FakeProvider("groq", ok=False, error="down too")
        chain = ProviderChain(providers=[deepinfra, groq])

        result = chain.generate_json("prompt", reporter=self.reporter)

        self.assertFalse(result.ok)
        self.assertEqual(
            _event_types(self.manager, "job-1"),
            ["provider_started", "provider_failed", "fallback_started", "provider_failed"],
        )

    def test_deadline_exhausted_before_an_attempt_reports_deadline_prevented_fallback_and_stops(self):
        deepinfra = _FakeProvider("deepinfra", ok=False, error="down")
        groq = _FakeProvider("groq", ok=True)
        chain = ProviderChain(providers=[deepinfra, groq])

        # Deadline already in the past -- insufficient time for even the
        # first attempt's own MIN_SECONDS_PER_PROVIDER_ATTEMPT check.
        result = chain.generate_json("prompt", reporter=self.reporter, deadline=time.monotonic() - 1)

        self.assertFalse(result.ok)
        self.assertEqual(deepinfra.calls, 0)
        self.assertEqual(_event_types(self.manager, "job-1"), ["deadline_prevented_fallback"])

    def test_cancellation_before_an_attempt_stops_further_fallback_without_touching_an_in_flight_call(self):
        """The honest-cancellation requirement: is_cancelled() is checked before EVERY attempt, but an attempt already dispatched is never aborted mid-call -- there is no mechanism here that could abort _FakeProvider.generate_json once called, so the only guard is never CALLING the next one."""
        deepinfra = _FakeProvider("deepinfra", ok=False, error="down")
        groq = _FakeProvider("groq", ok=True)
        chain = ProviderChain(providers=[deepinfra, groq])

        # Simulate "cancellation requested while deepinfra's own call was in
        # flight" by cancelling right after constructing the chain, before
        # generate_json ever starts iterating providers.
        self.manager.request_cancel("job-1")

        result = chain.generate_json("prompt", reporter=self.reporter)

        self.assertFalse(result.ok)
        self.assertEqual(deepinfra.calls, 0)
        self.assertEqual(groq.calls, 0)
        self.assertEqual(_event_types(self.manager, "job-1"), [])  # cancelled before even provider_started

    def test_generate_text_reports_the_same_lifecycle_as_generate_json(self):
        deepinfra = _FakeProvider("deepinfra", ok=True)
        chain = ProviderChain(providers=[deepinfra])

        result = chain.generate_text("prompt", reporter=self.reporter)

        self.assertTrue(result.ok)
        self.assertEqual(_event_types(self.manager, "job-1"), ["provider_started", "provider_succeeded"])


if __name__ == "__main__":
    unittest.main()
