"""
Unit tests for app/routers/ai_jobs.py -- calls the route handler functions
directly (no HTTP client/TestClient) with a fake registered worker, the same
"exercise the real objects directly" style as test_review_pipeline.py and
test_agent_job_manager.py, just using unittest.IsolatedAsyncioTestCase for
the async handlers.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import asyncio
import json
import os
import sys
import threading
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.jobs.manager import AgentJobManager  # noqa: E402
from app.jobs.reporter import ProgressReporter  # noqa: E402
from app.routers import ai_jobs  # noqa: E402


class _FakeRequest(BaseModel):
    value: str = "default"


class AiJobsRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Isolate every test from the process-wide singleton and from each
        # other's registrations/state.
        self._original_manager = ai_jobs.job_manager
        self._original_registry = dict(ai_jobs._REGISTRY)

        self.manager = AgentJobManager()
        ai_jobs.job_manager = self.manager
        ai_jobs._REGISTRY.clear()

        self.call_count = 0
        self.last_reporter: ProgressReporter | None = None

        def fake_worker(request: _FakeRequest, reporter: ProgressReporter):
            self.call_count += 1
            self.last_reporter = reporter
            reporter.start_stage("prepare")
            reporter.complete_stage("prepare")
            return {"echo": request.value}

        self.fake_worker = fake_worker

        ai_jobs.register_agent_job("FakeAgent", _FakeRequest, fake_worker)

        # app/jobs/plans.stage_keys_for looks up AGENT_STAGE_PLANS, which
        # ai_jobs.start_job calls -- register a plan for FakeAgent too.
        from app.jobs import plans

        self._original_plans = dict(plans.AGENT_STAGE_PLANS)
        plans.AGENT_STAGE_PLANS["FakeAgent"] = [("prepare", "Preparing"), ("save", "Saving")]

    def tearDown(self):
        ai_jobs.job_manager = self._original_manager
        ai_jobs._REGISTRY.clear()
        ai_jobs._REGISTRY.update(self._original_registry)

        from app.jobs import plans

        plans.AGENT_STAGE_PLANS.clear()
        plans.AGENT_STAGE_PLANS.update(self._original_plans)

    async def test_start_job_for_an_unregistered_agent_returns_404(self):
        body = ai_jobs.StartJobRequest(jobId="job-x", request={})

        with self.assertRaises(HTTPException) as ctx:
            await ai_jobs.start_job("NoSuchAgent", body)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_start_job_creates_and_runs_the_worker_exactly_once(self):
        body = ai_jobs.StartJobRequest(jobId="job-1", request={"value": "hello"})

        response = await ai_jobs.start_job("FakeAgent", body)
        self.assertEqual(response["jobId"], "job-1")

        record = self.manager.get_job("job-1")
        self.assertIsNotNone(record)
        await record.active_task  # let the spawned worker task finish deterministically

        self.assertEqual(self.call_count, 1)
        self.assertEqual(self.manager.get_result("job-1"), {"echo": "hello"})
        self.assertEqual(self.manager.get_job("job-1").status, "done")

    async def test_duplicate_start_for_the_same_job_id_never_starts_a_second_worker(self):
        """The exact requirement: a duplicate POST /ai-jobs/{agent_name} for the same job_id must not start a second worker (idempotent start)."""
        body = ai_jobs.StartJobRequest(jobId="job-1", request={"value": "first"})

        await ai_jobs.start_job("FakeAgent", body)
        first_record = self.manager.get_job("job-1")
        await first_record.active_task

        # Same jobId, even with a different request payload -- Python's
        # start endpoint keys purely on jobId (the coordinator's
        # crash-recovery retry always resends the exact original
        # RequestJson anyway; a differing payload here would only happen if
        # something were badly wrong upstream, and even then must never
        # start a second worker).
        duplicate_body = ai_jobs.StartJobRequest(jobId="job-1", request={"value": "second"})
        response = await ai_jobs.start_job("FakeAgent", duplicate_body)

        self.assertEqual(response["status"], "done")  # reflects the already-finished job, not a fresh "queued"
        self.assertEqual(self.call_count, 1)  # worker was never invoked a second time

    async def test_worker_exception_fails_the_job_with_the_exact_message(self):
        def raising_worker(request: _FakeRequest, reporter: ProgressReporter):
            raise RuntimeError("provider exploded")

        ai_jobs.register_agent_job("FailingAgent", _FakeRequest, raising_worker)
        from app.jobs import plans

        plans.AGENT_STAGE_PLANS["FailingAgent"] = [("generate", "Generating")]

        body = ai_jobs.StartJobRequest(jobId="job-fail", request={})
        await ai_jobs.start_job("FailingAgent", body)

        record = self.manager.get_job("job-fail")
        await record.active_task

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error, "provider exploded")

    async def test_worker_returning_none_while_cancelled_marks_the_job_cancelled_not_failed(self):
        def cooperative_worker(request: _FakeRequest, reporter: ProgressReporter):
            if reporter.is_cancelled():
                return None
            return {"ok": True}

        ai_jobs.register_agent_job("CancellableAgent", _FakeRequest, cooperative_worker)
        from app.jobs import plans

        plans.AGENT_STAGE_PLANS["CancellableAgent"] = [("generate", "Generating")]

        body = ai_jobs.StartJobRequest(jobId="job-cancel", request={})
        await ai_jobs.start_job("CancellableAgent", body)

        self.manager.request_cancel("job-cancel")

        record = self.manager.get_job("job-cancel")
        await record.active_task

        self.assertEqual(record.status, "cancelled")

    def test_get_job_snapshot_404s_for_an_unknown_job(self):
        with self.assertRaises(HTTPException) as ctx:
            ai_jobs.get_job_snapshot("does-not-exist")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_get_job_result_is_pending_202_until_done_then_returns_the_result(self):
        """
        Stale-test fix (not a race): get_job_result's "not ready yet" case
        was changed from raising a 404 HTTPException to returning a 202
        JSONResponse (see the "not a missing resource" comment right above
        that return in ai_jobs.py, committed in 258ba68) -- this test still
        asserted the old 404 contract. Made genuinely deterministic with a
        controllable worker instead of relying on asyncio scheduling
        timing: a threading.Event (not asyncio.Event) is required here
        because the worker body runs on a REAL thread via
        asyncio.to_thread, not as a coroutine.
        """
        worker_started = threading.Event()
        worker_may_finish = threading.Event()

        def controllable_worker(request: _FakeRequest, reporter: ProgressReporter):
            worker_started.set()
            if not worker_may_finish.wait(timeout=5):
                raise AssertionError("test never released the worker")
            return {"echo": request.value}

        ai_jobs.register_agent_job("ControllableAgent", _FakeRequest, controllable_worker)
        from app.jobs import plans
        plans.AGENT_STAGE_PLANS["ControllableAgent"] = [("prepare", "Preparing")]

        body = ai_jobs.StartJobRequest(jobId="job-1", request={"value": "x"})
        await ai_jobs.start_job("ControllableAgent", body)
        record = self.manager.get_job("job-1")

        # Block on the real OS thread's event, not the event loop, so this
        # deterministically waits for the worker to actually start running
        # rather than relying on it happening to still be scheduled.
        started_in_time = await asyncio.to_thread(worker_started.wait, 5)
        self.assertTrue(started_in_time, "worker never signaled it started")

        pending_response = ai_jobs.get_job_result("job-1")
        self.assertEqual(pending_response.status_code, 202)
        pending_payload = json.loads(pending_response.body)
        self.assertEqual(pending_payload["jobId"], "job-1")
        self.assertIn(pending_payload["status"], ("queued", "running"))

        worker_may_finish.set()
        await record.active_task

        result = ai_jobs.get_job_result("job-1")
        self.assertEqual(result, {"echo": "x"})

    def test_get_job_result_returns_409_for_a_failed_job(self):
        self.manager.create_job("job-2", "FakeAgent", ["generate"])
        self.manager.fail_job("job-2", "boom")

        with self.assertRaises(HTTPException) as ctx:
            ai_jobs.get_job_result("job-2")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_cancel_job_404s_for_an_unknown_job_and_202s_for_a_known_one(self):
        with self.assertRaises(HTTPException) as ctx:
            ai_jobs.cancel_job("does-not-exist")
        self.assertEqual(ctx.exception.status_code, 404)

        self.manager.create_job("job-3", "FakeAgent", ["generate"])
        response = ai_jobs.cancel_job("job-3")
        self.assertEqual(response["status"], "cancel_requested")
        self.assertTrue(self.manager.is_cancelled("job-3"))


if __name__ == "__main__":
    unittest.main()
