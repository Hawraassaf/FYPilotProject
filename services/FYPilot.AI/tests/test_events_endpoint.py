"""
Unit tests for app/routers/ai_jobs.py's GET /ai-jobs/{job_id}/events SSE
generator -- specifically that it never calls the blocking
queue.Queue.get() directly inside the async generator (which would freeze
FastAPI's whole event loop), and that it emits an initial snapshot, then
real events in order, then stops on a terminal event.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import asyncio
import json
import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.jobs.manager import AgentJobManager  # noqa: E402
from app.jobs.reporter import ProgressReporter  # noqa: E402
from app.routers import ai_jobs  # noqa: E402


class _FakeRequest:
    """Duck-types just the one Request method stream_job_events actually calls."""

    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


class EventsEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_manager = ai_jobs.job_manager
        self.manager = AgentJobManager()
        ai_jobs.job_manager = self.manager
        self.manager.create_job("job-1", "FakeAgent", ["prepare", "save"])
        self.reporter = ProgressReporter("job-1", self.manager)

    def tearDown(self):
        ai_jobs.job_manager = self._original_manager

    async def test_first_yielded_line_is_the_initial_snapshot(self):
        fake_request = _FakeRequest()
        response = await ai_jobs.stream_job_events("job-1", fake_request)

        first_line = await asyncio.wait_for(anext(response.body_iterator), timeout=2)
        self.assertTrue(first_line.startswith("data: "))
        payload = json.loads(first_line[len("data: "):])
        self.assertEqual(payload["type"], "snapshot")
        self.assertEqual(payload["stageStates"], {"prepare": "upcoming", "save": "upcoming"})

    async def test_real_events_are_yielded_in_order_and_generator_stops_on_terminal_event(self):
        fake_request = _FakeRequest()
        response = await ai_jobs.stream_job_events("job-1", fake_request)

        await asyncio.wait_for(anext(response.body_iterator), timeout=2)  # discard initial snapshot

        self.reporter.start_stage("prepare")
        self.reporter.complete_stage("prepare")
        self.reporter.mark_worker_done({"ok": True})

        line1 = await asyncio.wait_for(anext(response.body_iterator), timeout=2)
        self.assertIn('"type": "stage_started"', line1)

        line2 = await asyncio.wait_for(anext(response.body_iterator), timeout=2)
        self.assertIn('"type": "stage_completed"', line2)

        line3 = await asyncio.wait_for(anext(response.body_iterator), timeout=2)
        self.assertIn('"type": "worker_done"', line3)
        self.assertTrue(line3.startswith("id: "))  # sequence-carrying SSE id: field

        # The generator must terminate right after the terminal event --
        # confirmed by the async iterator raising StopAsyncIteration rather
        # than yielding another heartbeat/line.
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(anext(response.body_iterator), timeout=2)

    async def test_generator_never_blocks_the_event_loop_while_waiting_for_an_event(self):
        """
        The core regression guard for the 'do not call queue.Queue.get()
        directly inside an async generator' correction: while the events
        generator is awaiting its next queue item (nothing pushed yet), a
        concurrently scheduled coroutine on the SAME event loop must still
        get to run promptly -- proof the wait is happening on a worker
        thread (via asyncio.to_thread), not blocking the loop itself.
        """
        fake_request = _FakeRequest()
        response = await ai_jobs.stream_job_events("job-1", fake_request)
        await asyncio.wait_for(anext(response.body_iterator), timeout=2)  # discard initial snapshot

        other_task_ran = asyncio.Event()

        async def other_coroutine():
            await asyncio.sleep(0.05)
            other_task_ran.set()

        asyncio.create_task(other_coroutine())

        # Nothing is queued, so the generator is sitting inside its 0.5s
        # queue.Queue.get(timeout) wait. If that wait were a direct,
        # blocking call inside this async generator, the event loop itself
        # would be frozen and other_coroutine could never run in the
        # meantime.
        next_line_task = asyncio.create_task(anext(response.body_iterator))
        await asyncio.wait_for(other_task_ran.wait(), timeout=1)
        self.assertTrue(other_task_ran.is_set())

        next_line_task.cancel()
        with self.assertRaises((asyncio.CancelledError, StopAsyncIteration)):
            await next_line_task

    async def test_disconnect_stops_the_generator_without_a_terminal_event(self):
        fake_request = _FakeRequest()
        response = await ai_jobs.stream_job_events("job-1", fake_request)
        await asyncio.wait_for(anext(response.body_iterator), timeout=2)  # discard initial snapshot

        fake_request.disconnected = True

        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(anext(response.body_iterator), timeout=2)


if __name__ == "__main__":
    unittest.main()
