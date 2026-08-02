"""
Data model for the centralized AI Agent Loading System's Python-side job
tracking (app/jobs/manager.py, app/jobs/reporter.py, app/routers/ai_jobs.py).

Two distinct status vocabularies exist on purpose:
- JobRecord.status ("queued" | "running" | "done" | "failed" | "cancelled")
  is Python's OWN view of whether its worker function is still executing.
  Python never claims a job is "completed" -- that word is reserved for
  .NET's AiAgentJob.Status, set only after AiAgentJobCoordinator's
  IAiAgentJobFinalizer has actually persisted the output (see
  services/FYPilot.AI's contract with FYPilot.Web/Services/AiAgentJobs).
- Stage states ("upcoming" | "current" | "completed" | "skipped" | "failed"
  | "cancelled") describe each of an agent's real workflow steps
  individually. JobRecord.stage_states is the FULL, AUTHORITATIVE map for
  every stage key in that agent's StagePlan (see app/jobs/plans.py) --
  every stage a worker function touches must call an explicit
  ProgressReporter method (start_stage/complete_stage/skip_stage/fail_stage)
  to change its own entry. Nothing in this package or in .NET ever infers a
  stage's state from stage order or from another stage having started --
  see ProgressReporter's docstring for why.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

WorkerStatus = Literal["queued", "running", "done", "failed", "cancelled"]
StageState = Literal["upcoming", "current", "completed", "skipped", "failed", "cancelled"]

TERMINAL_WORKER_STATUSES: frozenset[WorkerStatus] = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True)
class ProgressUpdate:
    """One event published onto a JobRecord's event_queue -- see ProgressReporter.report()."""

    sequence: int
    type: str  # e.g. "provider_started", "provider_chunk_received", "worker_done", ...
    stage_key: str
    stage_states: dict[str, StageState]
    message: str
    provider: Optional[str]
    model: Optional[str]
    current_attempt_chunk_count: int
    current_attempt_token_count: Optional[int]
    provider_attempt_number: int
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "stageKey": self.stage_key,
            "stageStates": dict(self.stage_states),
            "message": self.message,
            "provider": self.provider,
            "model": self.model,
            "currentAttemptChunkCount": self.current_attempt_chunk_count,
            "currentAttemptTokenCount": self.current_attempt_token_count,
            "providerAttemptNumber": self.provider_attempt_number,
            "timestamp": self.timestamp,
        }


@dataclass
class JobRecord:
    """
    Mutable, in-process, in-memory job state. Guarded by `lock` for every
    field mutation -- worker functions run on a threadpool thread (via
    asyncio.to_thread) while FastAPI request handlers read/write from the
    event loop's thread concurrently. Explicitly NOT persisted beyond this
    process's lifetime (see the single-Uvicorn-worker requirement in the
    final report) -- AiAgentJob in Postgres is what actually survives a
    restart; this is purely the live execution tracker.
    """

    job_id: str
    agent_name: str
    stage_states: dict[str, StageState]

    status: WorkerStatus = "queued"
    stage_key: str = ""
    message: str = "Queued"
    provider: Optional[str] = None
    model: Optional[str] = None
    current_attempt_chunk_count: int = 0
    current_attempt_token_count: Optional[int] = None
    provider_attempt_number: int = 0

    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    sequence: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    cancel_event: threading.Event = field(default_factory=threading.Event)
    event_queue: "queue.Queue[ProgressUpdate]" = field(default_factory=queue.Queue)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # Reference kept per requirement #6 ("keep references to active tasks")
    # -- lets a future cancel also attempt task.cancel() for the case where
    # the worker hasn't yet entered its first blocking call. Typed loosely
    # (not asyncio.Task) to avoid this module needing to know about asyncio.
    active_task: Optional[Any] = None
