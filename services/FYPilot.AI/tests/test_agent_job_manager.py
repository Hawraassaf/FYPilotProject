"""
Unit tests for the centralized AI Agent Loading System's Python-side job
tracking (app/jobs/manager.py, app/jobs/reporter.py).

Run from services/FYPilot.AI:
    python -m unittest discover tests

All deterministic, no network/API keys -- exercises AgentJobManager and
ProgressReporter directly, the same "no mocking library, just call the real
objects" style as test_review_pipeline.py.
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.jobs.manager import AgentJobManager  # noqa: E402
from app.jobs.reporter import ProgressReporter  # noqa: E402


class AgentJobManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "TestAgent", ["prepare", "generate", "save"])

    def test_create_job_initializes_every_stage_as_upcoming(self):
        snapshot = self.manager.snapshot("job-1")
        self.assertEqual(snapshot["stageStates"], {"prepare": "upcoming", "generate": "upcoming", "save": "upcoming"})
        self.assertEqual(snapshot["status"], "queued")

    def test_create_job_is_idempotent_per_job_id(self):
        first = self.manager.get_job("job-1")
        second = self.manager.create_job("job-1", "TestAgent", ["prepare", "generate", "save"])
        self.assertIs(first, second)

    def test_mark_worker_done_sets_result_and_status(self):
        self.manager.mark_worker_done("job-1", {"ok": True})
        self.assertEqual(self.manager.get_result("job-1"), {"ok": True})
        self.assertEqual(self.manager.get_job("job-1").status, "done")

    def test_get_result_returns_none_unless_status_is_done(self):
        self.assertIsNone(self.manager.get_result("job-1"))
        self.manager.fail_job("job-1", "boom")
        self.assertIsNone(self.manager.get_result("job-1"))

    def test_a_terminal_status_is_never_overwritten_by_a_later_call(self):
        """Cancellation always wins over a late success -- a worker result arriving after cancellation must be discarded, never finalized (per the honest-cancellation requirement)."""
        self.manager.mark_cancelled("job-1")
        self.manager.mark_worker_done("job-1", {"ok": True})  # late result, must be ignored
        self.assertEqual(self.manager.get_job("job-1").status, "cancelled")
        self.assertIsNone(self.manager.get_result("job-1"))

    def test_fail_job_does_not_overwrite_an_already_done_job(self):
        self.manager.mark_worker_done("job-1", {"ok": True})
        self.manager.fail_job("job-1", "too late")
        self.assertEqual(self.manager.get_job("job-1").status, "done")

    def test_request_cancel_sets_the_flag_and_is_cancelled_reflects_it(self):
        self.assertFalse(self.manager.is_cancelled("job-1"))
        self.assertTrue(self.manager.request_cancel("job-1"))
        self.assertTrue(self.manager.is_cancelled("job-1"))

    def test_request_cancel_returns_false_for_an_unknown_job(self):
        self.assertFalse(self.manager.request_cancel("does-not-exist"))

    def test_snapshot_has_no_percent_like_field(self):
        """Regression guard for the hard 'no percentages anywhere' requirement."""
        snapshot = self.manager.snapshot("job-1")
        for key in snapshot:
            self.assertNotIn("percent", key.lower())


class ProgressReporterStageTransitionTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "TestAgent", ["prepare", "generate", "save"])
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_a_stage_never_flips_to_completed_merely_because_a_later_stage_started(self):
        """The exact bug class the 'explicit transitions only, never inferred from order' correction guards against."""
        self.reporter.start_stage("prepare")
        self.reporter.start_stage("generate")  # "prepare" is NOT explicitly completed here

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["prepare"], "current")  # still current, not silently completed
        self.assertEqual(stage_states["generate"], "current")

    def test_complete_stage_requires_an_explicit_call(self):
        self.reporter.start_stage("prepare")
        self.reporter.complete_stage("prepare")
        self.reporter.start_stage("generate")

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["prepare"], "completed")
        self.assertEqual(stage_states["generate"], "current")
        self.assertEqual(stage_states["save"], "upcoming")

    def test_skip_stage_records_the_honest_reason_as_the_message(self):
        self.reporter.skip_stage("generate", "Insufficient time remaining before the deadline")
        snapshot = self.manager.snapshot("job-1")
        self.assertEqual(snapshot["stageStates"]["generate"], "skipped")
        self.assertEqual(snapshot["message"], "Insufficient time remaining before the deadline")

    def test_fail_stage_marks_only_that_stage_failed(self):
        self.reporter.start_stage("prepare")
        self.reporter.complete_stage("prepare")
        self.reporter.fail_stage("generate", "provider error")

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["generate"], "failed")
        self.assertEqual(stage_states["save"], "upcoming")

    def test_starting_a_stage_transitions_queued_to_running(self):
        self.assertEqual(self.manager.get_job("job-1").status, "queued")
        self.reporter.start_stage("prepare")
        self.assertEqual(self.manager.get_job("job-1").status, "running")


class ProgressReporterProviderEventTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "TestAgent", ["generate", "save"])
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_provider_started_sets_provider_and_model(self):
        self.reporter.provider_started("deepinfra", "google/gemma-3-12b-it")
        snapshot = self.manager.snapshot("job-1")
        self.assertEqual(snapshot["provider"], "deepinfra")
        self.assertEqual(snapshot["model"], "google/gemma-3-12b-it")

    def test_provider_chunk_received_increments_chunk_count_only_from_real_events(self):
        self.reporter.provider_started("deepinfra")
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptChunkCount"], 0)

        self.reporter.provider_chunk_received()
        self.reporter.provider_chunk_received()
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptChunkCount"], 2)

    def test_token_count_is_only_ever_set_from_real_provider_metadata_never_estimated(self):
        self.reporter.provider_started("deepinfra")
        self.reporter.provider_chunk_received()  # a plain content chunk never sets a token count
        self.assertIsNone(self.manager.snapshot("job-1")["currentAttemptTokenCount"])

        self.reporter.provider_usage_received(42)  # only real usage-metadata ever sets it
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptTokenCount"], 42)

    def test_fallback_started_resets_current_attempt_counters_and_never_combines_across_providers(self):
        self.reporter.provider_started("deepinfra")
        self.reporter.provider_chunk_received()
        self.reporter.provider_chunk_received()
        self.reporter.provider_usage_received(150)
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptChunkCount"], 2)

        self.reporter.fallback_started("groq")

        snapshot = self.manager.snapshot("job-1")
        self.assertEqual(snapshot["currentAttemptChunkCount"], 0)
        self.assertIsNone(snapshot["currentAttemptTokenCount"])
        self.assertEqual(snapshot["provider"], "groq")
        self.assertEqual(snapshot["providerAttemptNumber"], 1)

        # The new (second) provider's own chunks must not be combined with
        # the first provider's tally.
        self.reporter.provider_chunk_received()
        self.reporter.provider_usage_received(30)
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptChunkCount"], 1)
        self.assertEqual(self.manager.snapshot("job-1")["currentAttemptTokenCount"], 30)

    def test_provider_failure_does_not_advance_any_stage(self):
        self.reporter.start_stage("generate")
        self.reporter.provider_started("deepinfra")
        self.reporter.provider_failed("deepinfra", "rate limited")

        stage_states = self.manager.snapshot("job-1")["stageStates"]
        self.assertEqual(stage_states["generate"], "current")  # never silently completed by a failure

    def test_cancellation_before_a_fallback_attempt_prevents_that_attempt(self):
        """Mirrors ProviderChain's real loop: it must check is_cancelled() before starting the next attempt and skip it entirely."""
        self.reporter.provider_started("deepinfra")
        self.manager.request_cancel("job-1")

        started_second_attempt = False
        if not self.reporter.is_cancelled():
            self.reporter.fallback_started("groq")
            started_second_attempt = True

        self.assertFalse(started_second_attempt)
        self.assertEqual(self.manager.snapshot("job-1")["provider"], "deepinfra")

    def test_sequence_number_strictly_increases_per_event(self):
        self.reporter.provider_started("deepinfra")
        first_sequence = self.manager.get_job("job-1").sequence
        self.reporter.provider_chunk_received()
        second_sequence = self.manager.get_job("job-1").sequence
        self.reporter.provider_succeeded("deepinfra", "model-x")
        third_sequence = self.manager.get_job("job-1").sequence

        self.assertLess(first_sequence, second_sequence)
        self.assertLess(second_sequence, third_sequence)


class ProgressReporterTerminalTransitionTests(unittest.TestCase):
    def setUp(self):
        self.manager = AgentJobManager()
        self.manager.create_job("job-1", "TestAgent", ["generate", "save"])
        self.reporter = ProgressReporter("job-1", self.manager)

    def test_mark_worker_done_pushes_a_terminal_event(self):
        self.reporter.mark_worker_done({"ok": True})
        update = self.manager.get_job("job-1").event_queue.get_nowait()
        self.assertEqual(update.type, "worker_done")
        self.assertEqual(self.manager.get_result("job-1"), {"ok": True})

    def test_mark_worker_failed_pushes_a_terminal_event_with_the_exact_error(self):
        self.reporter.mark_worker_failed("provider exploded")
        update = self.manager.get_job("job-1").event_queue.get_nowait()
        self.assertEqual(update.type, "job_failed")
        self.assertEqual(update.message, "provider exploded")

    def test_mark_worker_cancelled_pushes_a_terminal_event(self):
        self.reporter.mark_worker_cancelled()
        update = self.manager.get_job("job-1").event_queue.get_nowait()
        self.assertEqual(update.type, "job_cancelled")
        self.assertEqual(self.manager.get_job("job-1").status, "cancelled")


if __name__ == "__main__":
    unittest.main()
