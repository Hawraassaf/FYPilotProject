"""
Centralized job endpoints for the AI Agent Loading System.

POST /ai-jobs/{agent_name}      -- idempotent per jobId; 404 if agent_name isn't registered
GET  /ai-jobs/{job_id}          -- snapshot (also used by .NET's polling fallback / SSE bootstrap)
GET  /ai-jobs/{job_id}/events   -- SSE stream of ProgressUpdate events
POST /ai-jobs/{job_id}/cancel   -- sets the cancellation flag; does not abort an in-flight call
GET  /ai-jobs/{job_id}/result   -- raw worker result, only once the worker is done

Never called directly by a browser -- only FYPilot.Web's AiAgentJobCoordinator
(via AiJobsPythonClient.cs) talks to these endpoints; the browser only ever
talks to .NET's own /api/ai-agent-jobs/* endpoints.

Each agent's *_worker.py module (app/jobs/workers/) calls register_agent_job()
at import time, mirroring app/main.py's own per-router self-registration
style -- so an agent that hasn't been wired yet cleanly 404s here instead of
main.py needing to know about it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.jobs.manager import job_manager
from app.jobs.models import TERMINAL_WORKER_STATUSES
from app.jobs.plans import stage_keys_for
from app.jobs.reporter import ProgressReporter

logger = logging.getLogger("fypilot-ai-jobs")

router = APIRouter(tags=["AI Agent Jobs"], prefix="/ai-jobs")

# A worker function takes the agent's own parsed pydantic request plus a
# ProgressReporter, runs entirely synchronously (it's dispatched via
# asyncio.to_thread -- see _run_job), and returns the JSON-serializable
# result dict, or raises on failure.
WorkerFn = Callable[[BaseModel, ProgressReporter], Optional[dict[str, Any]]]


@dataclass(frozen=True)
class _AgentJobConfig:
    request_model: type[BaseModel]
    worker: WorkerFn


_REGISTRY: dict[str, _AgentJobConfig] = {}


def register_agent_job(agent_name: str, request_model: type[BaseModel], worker: WorkerFn) -> None:
    _REGISTRY[agent_name] = _AgentJobConfig(request_model=request_model, worker=worker)
    logger.info("Registered AI agent job worker for '%s'", agent_name)


class StartJobRequest(BaseModel):
    jobId: str
    request: dict[str, Any]


@router.post("/{agent_name}", status_code=202)
async def start_job(agent_name: str, body: StartJobRequest):
    config = _REGISTRY.get(agent_name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"No job worker registered for agent '{agent_name}'")

    existing = job_manager.get_job(body.jobId)
    if existing is not None:
        # Idempotent per jobId -- the coordinator's crash-recovery retry
        # (and, in the unlikely event its first response was lost, a page's
        # own Start handler) must never start a second worker for the same
        # job.
        return {"jobId": body.jobId, "status": existing.status}

    try:
        parsed_request = config.request_model.model_validate(body.request)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request payload for '{agent_name}': {exc}") from exc

    stage_keys = stage_keys_for(agent_name)
    job_manager.create_job(body.jobId, agent_name, stage_keys)
    reporter = ProgressReporter(body.jobId, job_manager)

    task = asyncio.create_task(_run_job(body.jobId, agent_name, config.worker, parsed_request, reporter))

    record = job_manager.get_job(body.jobId)
    if record is not None:
        # Kept per requirement #6 ("keep references to active tasks") --
        # lets a future cancel path also attempt task.cancel() for the case
        # where the worker hasn't yet entered its first blocking call.
        record.active_task = task

    return {"jobId": body.jobId, "status": "queued"}


async def _run_job(job_id: str, agent_name: str, worker: WorkerFn, request: BaseModel, reporter: ProgressReporter) -> None:
    job_manager.mark_running(job_id)

    try:
        result = await asyncio.to_thread(worker, request, reporter)
    except Exception as exc:
        logger.exception("Job %s (%s) worker raised.", job_id, agent_name)
        reporter.mark_worker_failed(str(exc))
        return

    if result is None:
        if reporter.is_cancelled():
            reporter.mark_worker_cancelled()
        else:
            reporter.mark_worker_failed("The worker finished without producing a result.")
    else:
        reporter.mark_worker_done(result)


@router.get("/{job_id}")
def get_job_snapshot(job_id: str):
    snapshot = job_manager.snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return snapshot


@router.post("/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str):
    if not job_manager.request_cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"jobId": job_id, "status": "cancel_requested"}


@router.get("/{job_id}/result")
def get_job_result(job_id: str):
    record = job_manager.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if record.status == "done":
        return job_manager.get_result(job_id)

    if record.status in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Job ended as '{record.status}': {record.error or ''}")

    raise HTTPException(status_code=404, detail="Result not ready yet")


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str, request: Request):
    record = job_manager.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # Immediate snapshot so a fresh/reconnecting subscriber (the
        # coordinator, after its own restart) is caught up without waiting
        # for the next real event.
        snapshot = job_manager.snapshot(job_id)
        if snapshot is not None:
            yield f"data: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"

        while True:
            if await request.is_disconnected():
                break

            try:
                # Handed to a worker thread via asyncio.to_thread rather
                # than calling record.event_queue.get() directly here --
                # queue.Queue.get() is a BLOCKING call, and calling it
                # straight from an async generator would block FastAPI's
                # entire event loop for up to `timeout` seconds each
                # iteration, starving every other request the service is
                # handling concurrently.
                update = await asyncio.to_thread(record.event_queue.get, True, 0.5)
            except queue.Empty:
                yield ": heartbeat\n\n"
                current = job_manager.get_job(job_id)
                if current is not None and current.status in TERMINAL_WORKER_STATUSES:
                    break
                continue

            yield f"id: {update.sequence}\ndata: {json.dumps(update.to_dict())}\n\n"

            if update.type in ("worker_done", "job_failed", "job_cancelled"):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")
