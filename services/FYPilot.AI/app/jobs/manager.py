"""
AgentJobManager -- thread-safe, in-process, in-memory tracker for active AI
agent jobs. See app/jobs/models.py's module docstring for why this is
explicitly NOT the durable source of truth (Postgres's AiAgentJob, owned by
FYPilot.Web, is) and why that's safe only under a single Uvicorn worker.

Access pattern: the outer dict (job_id -> JobRecord) is guarded by
`_dict_lock` only for insert/lookup; each JobRecord's own fields are guarded
by its own `record.lock`, so one job's writes never serialize against
another job's reads/writes.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from app.jobs.models import TERMINAL_WORKER_STATUSES, JobRecord, StageState


class AgentJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._dict_lock = threading.Lock()

    def create_job(self, job_id: str, agent_name: str, stage_keys: list[str]) -> JobRecord:
        """Idempotent per job_id -- a duplicate create_job call (the coordinator's start-retry path) returns the existing record unchanged rather than resetting progress."""
        with self._dict_lock:
            existing = self._jobs.get(job_id)
            if existing is not None:
                return existing

            now = time.time()
            stage_states: dict[str, StageState] = {key: "upcoming" for key in stage_keys}
            record = JobRecord(
                job_id=job_id,
                agent_name=agent_name,
                stage_states=stage_states,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job_id] = record
            return record

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._dict_lock:
            return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> None:
        record = self.get_job(job_id)
        if record is None:
            return

        with record.lock:
            if record.status == "queued":
                record.status = "running"
                record.updated_at = time.time()

    def mark_worker_done(self, job_id: str, result: dict[str, Any]) -> None:
        """Python's own terminal state -- 'the worker function finished with a usable result'. Never means the job is fully complete; .NET's finalizer still has to persist it. A result arriving after cancellation is silently dropped -- cancellation always wins."""
        record = self.get_job(job_id)
        if record is None:
            return

        with record.lock:
            if record.status in TERMINAL_WORKER_STATUSES:
                return
            record.status = "done"
            record.result = result
            record.updated_at = time.time()

    def fail_job(self, job_id: str, error: str) -> None:
        record = self.get_job(job_id)
        if record is None:
            return

        with record.lock:
            if record.status in TERMINAL_WORKER_STATUSES:
                return
            record.status = "failed"
            record.error = error
            record.updated_at = time.time()

    def mark_cancelled(self, job_id: str) -> None:
        """Called by the worker wrapper once a worker function returns early because is_cancelled() was observed true, without producing a result."""
        record = self.get_job(job_id)
        if record is None:
            return

        with record.lock:
            if record.status in TERMINAL_WORKER_STATUSES:
                return
            record.status = "cancelled"
            record.updated_at = time.time()

    def request_cancel(self, job_id: str) -> bool:
        """Sets the cancellation flag a running worker's ProviderChain calls check before starting each NEW stage/attempt -- never aborts an in-flight call. Returns False if the job is unknown."""
        record = self.get_job(job_id)
        if record is None:
            return False
        record.cancel_event.set()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        record = self.get_job(job_id)
        return record is not None and record.cancel_event.is_set()

    def get_result(self, job_id: str) -> Optional[dict[str, Any]]:
        record = self.get_job(job_id)
        if record is None or record.status != "done":
            return None
        with record.lock:
            return record.result

    def snapshot(self, job_id: str) -> Optional[dict[str, Any]]:
        """Shape consumed by .NET's AiJobsPythonClient -- keep in sync with PythonJobSnapshotDto (FYPilot.Infrastructure/Services/AiJobsPythonClient.cs)."""
        record = self.get_job(job_id)
        if record is None:
            return None

        with record.lock:
            return {
                "status": record.status,
                "stageStates": dict(record.stage_states),
                "stageKey": record.stage_key,
                "message": record.message,
                "provider": record.provider,
                "model": record.model,
                "currentAttemptChunkCount": record.current_attempt_chunk_count,
                "currentAttemptTokenCount": record.current_attempt_token_count,
                "providerAttemptNumber": record.provider_attempt_number,
                "errorMessage": record.error,
            }


# Process-wide singleton -- imported by app/routers/ai_jobs.py and by every
# job worker function / ProgressReporter construction. Deliberately not
# dependency-injected via FastAPI's Depends(): worker functions run on
# threadpool threads outside any request scope (see ai_jobs.py's
# _run_job), so a plain module-level instance is simpler and correct here,
# matching this service's existing module-level-singleton pattern (e.g.
# idea_comparison.py's `_reviewer_agent = ReviewerAgent(...)`).
job_manager = AgentJobManager()
