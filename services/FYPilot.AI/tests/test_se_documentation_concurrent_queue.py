"""
Stabilization task: bounded-concurrency ordered queue + reserved
semantic-review budget for SE Documentation.

Covers the 17 required proofs from the task spec:
 1. maximum active section calls never exceeds two
 2. the next queued task starts when either active task finishes
 3. launch order follows the canonical order
 4. output aggregation follows canonical order, not completion order
 5. two tasks completing in reverse order retain the correct provider/model
 6. shared self.last_* state is not used for section provenance
 7. configured DeepInfra timeout remains 180 seconds (already covered by
    test_se_documentation_timeout_correction.py; re-asserted here too)
 8. effective provider timeout is capped by remaining Writer time
 9. remaining time is recalculated before every fallback provider attempt
10. writer_deadline equals global_deadline minus 300 seconds
11. the original global deadline reaches ReviewPipeline unchanged
12. WriterBudgetExceeded uses a typed exception, not a mutable side-channel
13. pending tasks are never launched (and completed ones are awaited, not
    abandoned) when the Writer budget expires mid-queue
14. incomplete core output is rejected (router never assembles a partial
    candidate on WriterBudgetExceeded)
15. the previous accepted document is preserved -- see
    tests/FYPilot.Tests/Documentation/DocumentationGeneratorServiceTests.cs
    ::WriterBudgetExceeded_LeavesPreviousValidDocumentUnchanged (.NET side;
    Python has no notion of "the previously persisted document")
16. no other agent gains concurrency or changed timeout behavior
17. Python deadline 1200 remains below .NET timeout 1260 -- see
    tests/FYPilot.Tests/Documentation/AiServiceClientTimeoutTests.cs
    ::SeDocumentationTimeout_IsLongerThanPythonsTotalDeadline (.NET side)

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.se_documentation.project_facts import build_project_facts  # noqa: E402
from app.agents.se_documentation.se_documentation_orchestrator import (  # noqa: E402
    SEDocumentationOrchestratorAgent,
    SEDocumentationRequest,
    SectionCallResult,
    WriterBudgetExceededError,
    _CORE_SECTION_QUEUE,
    _MAX_CONCURRENT_SECTION_CALLS,
)
from app.review.registry import get_agent_config  # noqa: E402
from app.services.llm_provider import LLMResult, ProviderChain  # noqa: E402


def _result(key: str, launch_order: int, provider: str = "fake", model: str = "fake-model") -> SectionCallResult:
    return SectionCallResult(
        section_key=key, launch_order=launch_order, success=True, data={"k": key},
        provider=provider, model=model, provenance="provider",
        error_code=None, error_message=None,
        start_time=0.0, end_time=0.1, duration=0.1,
        configured_timeout=180.0, effective_timeout=180.0,
        remaining_writer_budget_at_start=900.0,
    )


# ---------------------------------------------------------------------------
# 1, 2, 3 -- bounded concurrency, immediate-next-on-completion, launch order
# ---------------------------------------------------------------------------

class BoundedQueueSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.facts = build_project_facts(SEDocumentationRequest())

    def test_maximum_two_concurrent_section_calls(self):
        """Instruments _call_section_concurrent_safe itself to record the
        peak number of calls in flight at once, using real threads (not
        mocked timing) so this is a genuine concurrency proof."""
        lock = threading.Lock()
        active = {"n": 0, "peak": 0}

        real_call = self.agent._call_section_concurrent_safe

        def instrumented_call(key, prompt, max_tokens, writer_deadline, launch_order):
            with lock:
                active["n"] += 1
                active["peak"] = max(active["peak"], active["n"])
            time.sleep(0.05)  # hold the "slot" briefly so overlaps are observable
            try:
                return _result(key, launch_order)
            finally:
                with lock:
                    active["n"] -= 1

        with patch.object(self.agent, "_call_section_concurrent_safe", side_effect=instrumented_call):
            self.agent._generate_llm_sections(
                SEDocumentationRequest(), self.facts, deadline=time.monotonic() + 60.0,
            )

        self.assertLessEqual(active["peak"], _MAX_CONCURRENT_SECTION_CALLS)
        self.assertEqual(active["peak"], 2)  # actually reaches the cap, not just never exceeds it
        self.assertEqual(_MAX_CONCURRENT_SECTION_CALLS, 2)

    def test_next_task_starts_when_either_active_task_finishes_not_both(self):
        """
        Section 1 (requirements) takes a long time; section 2 (useCases)
        finishes fast. Section 3 (modulesArchitecture) must start as soon
        as section 2 frees a slot -- it must NOT wait for section 1 (the
        OTHER member of the first pair) to also finish.
        """
        start_times: dict[str, float] = {}
        end_times: dict[str, float] = {}
        lock = threading.Lock()

        def instrumented_call(key, prompt, max_tokens, writer_deadline, launch_order):
            with lock:
                start_times[key] = time.monotonic()
            if key == "requirements":
                time.sleep(0.3)  # deliberately slow
            else:
                time.sleep(0.02)  # fast
            with lock:
                end_times[key] = time.monotonic()
            return _result(key, launch_order)

        with patch.object(self.agent, "_call_section_concurrent_safe", side_effect=instrumented_call):
            self.agent._generate_llm_sections(
                SEDocumentationRequest(), self.facts, deadline=time.monotonic() + 60.0,
            )

        # modulesArchitecture (3rd in queue) must have STARTED before
        # requirements (1st, the slow one) FINISHED -- proof the queue
        # didn't wait for the whole first pair to complete.
        self.assertLess(start_times["modulesArchitecture"], end_times["requirements"])

    def test_launch_order_follows_canonical_queue_order(self):
        launch_sequence: list[str] = []
        lock = threading.Lock()

        def instrumented_call(key, prompt, max_tokens, writer_deadline, launch_order):
            with lock:
                launch_sequence.append(key)
            return _result(key, launch_order)

        with patch.object(self.agent, "_call_section_concurrent_safe", side_effect=instrumented_call):
            self.agent._generate_llm_sections(
                SEDocumentationRequest(), self.facts, deadline=time.monotonic() + 60.0,
            )

        # The first 2 launched must be the first 2 in canonical order --
        # exact global ordering beyond that depends on completion timing
        # (by design), but the FIRST wave is deterministic.
        self.assertEqual(launch_sequence[0], _CORE_SECTION_QUEUE[0])
        self.assertEqual(launch_sequence[1], _CORE_SECTION_QUEUE[1])
        self.assertEqual(set(launch_sequence), set(_CORE_SECTION_QUEUE))


# ---------------------------------------------------------------------------
# 4, 5 -- aggregation follows canonical order, not completion order
# ---------------------------------------------------------------------------

class AggregationOrderTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.facts = build_project_facts(SEDocumentationRequest())

    def test_output_dict_key_order_follows_canonical_order_not_completion_order(self):
        """Make LATER-queued sections finish FIRST (reverse completion
        order) and confirm the returned sections dict's key order still
        matches _CORE_SECTION_QUEUE, not the order they actually completed."""
        def instrumented_call(key, prompt, max_tokens, writer_deadline, launch_order):
            # Sections later in the queue sleep LESS, so they tend to
            # finish before earlier ones when both are in flight.
            index = list(_CORE_SECTION_QUEUE).index(key)
            time.sleep(0.02 * (len(_CORE_SECTION_QUEUE) - index))
            return _result(key, launch_order)

        with patch.object(self.agent, "_call_section_concurrent_safe", side_effect=instrumented_call):
            sections = self.agent._generate_llm_sections(
                SEDocumentationRequest(), self.facts, deadline=time.monotonic() + 60.0,
            )

        self.assertEqual(list(sections.keys()), list(_CORE_SECTION_QUEUE))

    def test_last_fields_reflect_last_in_canonical_order_not_last_completed(self):
        """
        _update_last_fields_from_results must pick the result matching the
        LAST key in canonical order among successes -- even when a
        DIFFERENT (earlier-in-queue) section actually finished last in wall
        -clock time.
        """
        results = {
            "requirements": _result("requirements", 1, provider="provider-A", model="model-A"),
            "useCases": _result("useCases", 2, provider="provider-B", model="model-B"),
            "modulesArchitecture": _result("modulesArchitecture", 3, provider="provider-C", model="model-C"),
            "database": _result("database", 4, provider="provider-D", model="model-D"),
            "uiApi": _result("uiApi", 5, provider="provider-E", model="model-E"),
            "testingSecurity": _result("testingSecurity", 6, provider="provider-F", model="model-F"),
        }

        self.agent._update_last_fields_from_results(results, list(_CORE_SECTION_QUEUE))

        self.assertEqual(self.agent.last_provider, "provider-F")  # testingSecurity is LAST in canonical order
        self.assertEqual(self.agent.last_model_used, "model-F")

    def test_last_fields_skip_a_failed_last_section_and_use_the_previous_success(self):
        results = {
            "requirements": _result("requirements", 1, provider="provider-A", model="model-A"),
            "useCases": _result("useCases", 2, provider="provider-B", model="model-B"),
        }
        results["useCases"] = SectionCallResult(
            section_key="useCases", launch_order=2, success=False, data=None,
            provider=None, model=None, provenance="fallback",
            error_code="transport_failure", error_message="boom",
            start_time=0.0, end_time=0.1, duration=0.1,
            configured_timeout=180.0, effective_timeout=180.0,
            remaining_writer_budget_at_start=900.0,
        )

        self.agent._update_last_fields_from_results(results, ["requirements", "useCases"])

        self.assertEqual(self.agent.last_provider, "provider-A")  # useCases failed -- requirements wins


# ---------------------------------------------------------------------------
# 6 -- shared self.last_* state is never touched by a concurrent worker
# ---------------------------------------------------------------------------

class ThreadSafetyTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()

    def test_worker_never_mutates_shared_last_fields(self):
        sentinel_provider = object()
        sentinel_model = object()
        sentinel_raw = object()
        self.agent.last_provider = sentinel_provider
        self.agent.last_model_used = sentinel_model
        self.agent.last_raw_llm_response = sentinel_raw

        class _FakeProvider:
            name = "fake"
            timeout_seconds = 180.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                return LLMResult(ok=True, provider="fake", model="fake-model", text="", data={"x": 1})

        self.agent.provider_chain.providers = [_FakeProvider()]

        result = self.agent._call_section_concurrent_safe(
            "requirements", "prompt", 1000, time.monotonic() + 60.0, 1,
        )

        self.assertTrue(result.success)
        # The worker must NEVER have touched these -- only
        # _update_last_fields_from_results (called on the main thread,
        # after every worker has already returned) may do that.
        self.assertIs(self.agent.last_provider, sentinel_provider)
        self.assertIs(self.agent.last_model_used, sentinel_model)
        self.assertIs(self.agent.last_raw_llm_response, sentinel_raw)


# ---------------------------------------------------------------------------
# 7, 8, 9 -- configured/effective timeout capping
# ---------------------------------------------------------------------------

class EffectiveTimeoutTests(unittest.TestCase):
    def test_se_documentation_configured_deepinfra_timeout_is_still_180_seconds(self):
        # index 1, not 0 -- AnthropicProvider (direct API) is now prepended
        # ahead of DeepInfra for this tier, see ProviderChain's docstring.
        chain = ProviderChain(tier="se_documentation")
        self.assertEqual(type(chain.providers[1]).__name__, "DeepInfraProvider")
        self.assertEqual(chain.providers[1].timeout_seconds, 180.0)

    def test_effective_timeout_is_capped_by_remaining_writer_budget(self):
        """
        With only ~10s of Writer budget left, the provider must be asked
        for AT MOST ~10s -- never the full configured 180s.
        """
        captured = {}

        class _CapturingProvider:
            name = "fake"
            timeout_seconds = 180.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                captured["writer_budget_seconds"] = kwargs.get("writer_budget_seconds")
                return LLMResult(ok=True, provider="fake", model="fake-model", text="", data={"x": 1})

        chain = ProviderChain(providers=[_CapturingProvider()])
        near_deadline = time.monotonic() + 10.0

        chain.generate_json("prompt", deadline=near_deadline, cap_timeout_to_deadline=True)

        self.assertIsNotNone(captured["writer_budget_seconds"])
        self.assertLess(captured["writer_budget_seconds"], 180.0)
        self.assertLessEqual(captured["writer_budget_seconds"], 10.5)

    def test_cap_timeout_to_deadline_false_by_default_leaves_other_callers_unaffected(self):
        """Every OTHER agent's call (no cap_timeout_to_deadline flag) must
        never receive writer_budget_seconds at all -- proving this is a
        genuinely opt-in mechanism."""
        captured = {"kwargs": None}

        class _CapturingProvider:
            name = "fake"
            timeout_seconds = 60.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                captured["kwargs"] = kwargs
                return LLMResult(ok=True, provider="fake", model="fake-model", text="", data={"x": 1})

        chain = ProviderChain(providers=[_CapturingProvider()])
        chain.generate_json("prompt", deadline=time.monotonic() + 10.0)  # no cap_timeout_to_deadline

        self.assertNotIn("writer_budget_seconds", captured["kwargs"])

    def test_remaining_time_recalculated_before_every_fallback_provider(self):
        """
        Two providers in the cascade; the first one is slow (consumes real
        wall-clock time), so the SECOND provider must observe a SMALLER
        writer_budget_seconds than the first -- proving it's recomputed
        fresh per provider, not passed down as one static value.
        """
        captured: list[float] = []

        class _SlowFailingProvider:
            name = "first"
            timeout_seconds = 180.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                captured.append(kwargs["writer_budget_seconds"])
                time.sleep(0.3)
                return LLMResult(ok=False, provider="first", model="m", text="", data=None, error="fails")

        class _SecondProvider:
            name = "second"
            timeout_seconds = 180.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                captured.append(kwargs["writer_budget_seconds"])
                return LLMResult(ok=True, provider="second", model="m", text="", data={"x": 1})

        chain = ProviderChain(providers=[_SlowFailingProvider(), _SecondProvider()])
        deadline = time.monotonic() + 30.0

        result = chain.generate_json("prompt", deadline=deadline, cap_timeout_to_deadline=True)

        self.assertTrue(result.ok)
        self.assertEqual(len(captured), 2)
        self.assertLess(captured[1], captured[0])  # second provider saw LESS remaining budget


# ---------------------------------------------------------------------------
# 10, 11 -- writer_deadline / global_deadline split reaching the right places
# ---------------------------------------------------------------------------

class DeadlineSplitTests(unittest.TestCase):
    def test_writer_deadline_equals_global_deadline_minus_300(self):
        from app.routers.se_documentation import _SEMANTIC_REVIEW_RESERVE_SECONDS

        self.assertEqual(_SEMANTIC_REVIEW_RESERVE_SECONDS, 300.0)

        captured = {}

        class _FakePipeline:
            def __init__(self, *a, **k):
                from app.review.registry import get_agent_config
                self.config = get_agent_config("SEDocumentationAgent")

            def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
                captured["global_deadline_seen_by_pipeline"] = deadline
                writer_call_fn()  # triggers generate_candidate, capturing its own deadline too
                from app.review.models import PipelineResult
                return PipelineResult(status="provider_unavailable", usable=False, output={})

        class _FakeAgent:
            last_llm_used = False
            last_provider = None
            last_model_used = None
            last_error = None
            last_raw_llm_response = None

            def generate_candidate(self, request, *, deadline=None):
                captured["writer_deadline_seen_by_agent"] = deadline
                return None

            def build_safe_fallback(self, request):
                from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationRequest as R
                from app.agents.se_documentation.project_facts import build_project_facts
                from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationOrchestratorAgent
                return SEDocumentationOrchestratorAgent().build_safe_fallback(request)

        with patch("app.routers.se_documentation.ReviewPipeline", _FakePipeline), \
             patch("app.routers.se_documentation.SEDocumentationAgent", _FakeAgent):
            from app.routers.se_documentation import generate_se_documentation
            from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationRequest
            generate_se_documentation(SEDocumentationRequest())

        global_deadline = captured["global_deadline_seen_by_pipeline"]
        writer_deadline = captured["writer_deadline_seen_by_agent"]
        self.assertAlmostEqual(writer_deadline, global_deadline - 300.0, places=3)

    def test_original_global_deadline_reaches_reviewpipeline_unchanged(self):
        """Same capture as above, phrased as its own explicit assertion:
        the value ReviewPipeline.run receives must be exactly
        time.monotonic() + registry's max_total_seconds (1200s) -- never
        the shorter writer_deadline."""
        captured = {}

        class _FakePipeline:
            def __init__(self, *a, **k):
                self.config = get_agent_config("SEDocumentationAgent")

            def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
                captured["deadline"] = deadline
                from app.review.models import PipelineResult
                return PipelineResult(status="provider_unavailable", usable=False, output={})

        class _FakeAgent:
            last_llm_used = False
            last_provider = None
            last_model_used = None
            last_error = None
            last_raw_llm_response = None

            def generate_candidate(self, request, *, deadline=None):
                return None

            def build_safe_fallback(self, request):
                from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationOrchestratorAgent
                return SEDocumentationOrchestratorAgent().build_safe_fallback(request)

        before = time.monotonic()
        with patch("app.routers.se_documentation.ReviewPipeline", _FakePipeline), \
             patch("app.routers.se_documentation.SEDocumentationAgent", _FakeAgent):
            from app.routers.se_documentation import generate_se_documentation
            from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationRequest
            generate_se_documentation(SEDocumentationRequest())
        after = time.monotonic()

        expected_min = before + 1200.0
        expected_max = after + 1200.0
        self.assertGreaterEqual(captured["deadline"], expected_min)
        self.assertLessEqual(captured["deadline"], expected_max)


# ---------------------------------------------------------------------------
# 12 -- WriterBudgetExceeded is a typed exception, not a mutable side-channel
# ---------------------------------------------------------------------------

class WriterBudgetExceededRepresentationTests(unittest.TestCase):
    def test_is_a_real_exception_type(self):
        self.assertTrue(issubclass(WriterBudgetExceededError, Exception))
        err = WriterBudgetExceededError(missing_sections=["database"], completed_sections=["requirements"])
        self.assertEqual(err.missing_sections, ["database"])
        self.assertEqual(err.completed_sections, ["requirements"])

    def test_agent_has_no_mutable_writer_budget_exceeded_instance_flag(self):
        agent = SEDocumentationOrchestratorAgent()
        self.assertFalse(hasattr(agent, "writer_budget_exceeded"))


# ---------------------------------------------------------------------------
# 13 -- pending tasks are never launched once the Writer budget expires
# ---------------------------------------------------------------------------

class BudgetExpiryMidQueueTests(unittest.TestCase):
    def setUp(self):
        self.agent = SEDocumentationOrchestratorAgent()
        self.facts = build_project_facts(SEDocumentationRequest())

    def test_sections_still_pending_when_deadline_hits_are_never_launched(self):
        """
        The first 2 sections consume the ENTIRE remaining budget; by the
        time they finish, writer_deadline has passed. The remaining 4 core
        sections must never be launched (never even attempted) -- proven by
        a call counter that must equal exactly 2, and by the exception's
        completed/missing lists.
        """
        call_count = {"n": 0}
        # Enough remaining budget to clear the launch threshold
        # (_MIN_SECONDS_PER_SECTION_ATTEMPT=4.0s) for the FIRST pair, but
        # the first pair's own 0.5s duration deliberately eats enough of it
        # that the SECOND pair's launch check then falls back below 4.0s.
        writer_deadline = time.monotonic() + 4.3

        def instrumented_call(key, prompt, max_tokens, deadline, launch_order):
            call_count["n"] += 1
            time.sleep(0.5)
            return _result(key, launch_order)

        with patch.object(self.agent, "_call_section_concurrent_safe", side_effect=instrumented_call):
            with self.assertRaises(WriterBudgetExceededError) as ctx:
                self.agent._generate_llm_sections(
                    SEDocumentationRequest(), self.facts, deadline=writer_deadline,
                )

        self.assertEqual(call_count["n"], 2)  # only the first pair was ever launched
        self.assertEqual(len(ctx.exception.completed_sections), 2)  # both finished (awaited), just too late
        self.assertEqual(len(ctx.exception.missing_sections), 4)  # never even attempted


# ---------------------------------------------------------------------------
# 14 -- incomplete core output is rejected at the router (no partial candidate)
# ---------------------------------------------------------------------------

class RouterWriterBudgetExceededResponseTests(unittest.TestCase):
    def test_router_never_assembles_a_partial_document_on_writer_budget_exceeded(self):
        from app.agents.se_documentation.se_documentation_orchestrator import WriterBudgetExceededError as WBE

        class _FakePipeline:
            def __init__(self, *a, **k):
                self.config = get_agent_config("SEDocumentationAgent")

            def run(self, writer_call_fn, context, *, writer_trusted_parts, writer_untrusted_parts, deadline=None):
                raise WBE(missing_sections=["testingSecurity", "aiReport"], completed_sections=["requirements"])

        class _FakeAgent:
            def generate_candidate(self, request, *, deadline=None):
                raise AssertionError("should not be reached directly by the test")

            def build_safe_fallback(self, request):
                raise AssertionError("must never build a fallback candidate on WriterBudgetExceeded")

        with patch("app.routers.se_documentation.ReviewPipeline", _FakePipeline), \
             patch("app.routers.se_documentation.SEDocumentationAgent", _FakeAgent):
            from app.routers.se_documentation import generate_se_documentation
            from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationRequest
            response = generate_se_documentation(SEDocumentationRequest())

        self.assertIsNone(response["documentation"])
        self.assertTrue(response["writerBudgetExceeded"])
        self.assertEqual(response["missingSections"], ["testingSecurity", "aiReport"])
        self.assertEqual(response["completedSections"], ["requirements"])
        self.assertFalse(response["llmUsed"])
        self.assertFalse(response["coreSectionFallback"])


# ---------------------------------------------------------------------------
# 16 -- no other agent gains concurrency or changed timeout behavior
# ---------------------------------------------------------------------------

class OtherAgentsUnaffectedTests(unittest.TestCase):
    def test_other_agents_still_call_generate_json_without_the_new_flag(self):
        """Structural proof: cap_timeout_to_deadline defaults to False, so
        any EXISTING call site that doesn't explicitly opt in (every agent
        except SE Documentation's Writer stage) is provably unaffected --
        no writer_budget_seconds ever reaches its provider."""
        captured = {"kwargs": None}

        class _CapturingProvider:
            name = "fake"
            timeout_seconds = 90.0

            def generate_json(self, prompt, use_search=False, max_tokens=None, **kwargs):
                captured["kwargs"] = kwargs
                return LLMResult(ok=True, provider="fake", model="m", text="", data={"x": 1})

        chain = ProviderChain(providers=[_CapturingProvider()])
        # Exactly how every other agent already calls this (deadline
        # optionally set, cap_timeout_to_deadline never mentioned).
        chain.generate_json("prompt", deadline=time.monotonic() + 90.0)

        self.assertNotIn("writer_budget_seconds", captured["kwargs"])

    def test_other_agents_registry_deadlines_unaffected(self):
        # Mirrors test_se_documentation_timeout_correction.py's own check --
        # duplicated narrowly here as a direct "16" proof for this specific
        # test file's completeness.
        self.assertEqual(get_agent_config("FypMentorAgent").max_total_seconds, 90.0)
        # ProjectRoadmapAgent is intentionally 240.0, not 90.0 -- the
        # Roadmap-only timeout adjustment (see registry.py's
        # ProjectRoadmapAgent.max_total_seconds and
        # tests/test_roadmap_timeout_adjustment.py) raised it from 90s so
        # DeepInfra's own 120s single-attempt cap can fit inside the
        # Writer's budget alongside a Groq/Ollama fallback attempt.
        self.assertEqual(get_agent_config("ProjectRoadmapAgent").max_total_seconds, 360.0)
        self.assertEqual(get_agent_config("ProjectDNAAgent").max_total_seconds, 90.0)
        self.assertEqual(get_agent_config("IdeaComparisonAgent").max_total_seconds, 45.0)


if __name__ == "__main__":
    unittest.main()
