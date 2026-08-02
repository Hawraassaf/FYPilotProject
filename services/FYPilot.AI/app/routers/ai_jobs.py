"""
Centralized job endpoints for the AI Agent Loading System.

The browser never calls these endpoints directly. FYPilot.Web's
AiAgentJobCoordinator is the only lifecycle client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.jobs.manager import job_manager
from app.jobs.models import TERMINAL_WORKER_STATUSES
from app.jobs.plans import stage_keys_for
from app.jobs.reporter import ProgressReporter

logger = logging.getLogger("fypilot-ai-jobs")

router = APIRouter(tags=["AI Agent Jobs"], prefix="/ai-jobs")

WorkerFn = Callable[[BaseModel, ProgressReporter], Optional[dict[str, Any]]]


@dataclass(frozen=True)
class _AgentJobConfig:
    request_model: type[BaseModel]
    worker: WorkerFn


_REGISTRY: dict[str, _AgentJobConfig] = {}


def register_agent_job(
    agent_name: str,
    request_model: type[BaseModel],
    worker: WorkerFn,
) -> None:
    _REGISTRY[agent_name] = _AgentJobConfig(
        request_model=request_model,
        worker=worker,
    )
    logger.info("Registered AI agent job worker for '%s'", agent_name)


class StartJobRequest(BaseModel):
    jobId: str
    request: dict[str, Any]


@router.post("/{agent_name}", status_code=202)
async def start_job(agent_name: str, body: StartJobRequest):
    config = _REGISTRY.get(agent_name)
    if config is None:
        raise HTTPException(
            status_code=404,
            detail=f"No job worker registered for agent '{agent_name}'",
        )

    existing = job_manager.get_job(body.jobId)
    if existing is not None:
        return {"jobId": body.jobId, "status": existing.status}

    try:
        parsed_request = config.request_model.model_validate(body.request)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid request payload for '{agent_name}': {exc}",
        ) from exc

    stage_keys = stage_keys_for(agent_name)
    job_manager.create_job(body.jobId, agent_name, stage_keys)
    reporter = ProgressReporter(body.jobId, job_manager)

    task = asyncio.create_task(
        _run_job(
            body.jobId,
            agent_name,
            config.worker,
            parsed_request,
            reporter,
        )
    )

    record = job_manager.get_job(body.jobId)
    if record is not None:
        record.active_task = task

    return {"jobId": body.jobId, "status": "queued"}


async def _run_job(
    job_id: str,
    agent_name: str,
    worker: WorkerFn,
    request: BaseModel,
    reporter: ProgressReporter,
) -> None:
    job_manager.mark_running(job_id)

    try:
        result = await asyncio.to_thread(worker, request, reporter)

        if result is None:
            if reporter.is_cancelled():
                reporter.mark_worker_cancelled()
            else:
                reporter.mark_worker_failed(
                    "The worker finished without producing a result."
                )
            return

        # Keep this inside the try. Serialization/state errors during the
        # terminal transition must not leave the job stuck in running.
        reporter.mark_worker_done(result)

    except Exception as exc:
        logger.exception(
            "Job %s (%s) failed during execution or result finalization.",
            job_id,
            agent_name,
        )

        try:
            reporter.mark_worker_failed(str(exc))
        except Exception:
            logger.exception("Job %s could not be marked failed.", job_id)

    finally:
        record = job_manager.get_job(job_id)
        if record is not None:
            record.active_task = None


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
        result = job_manager.get_result(job_id)
        if not isinstance(result, dict) or not result:
            raise HTTPException(
                status_code=500,
                detail="Job is done but its result is missing or invalid.",
            )
        return result

    if record.status in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail={
                "jobId": job_id,
                "status": record.status,
                "error": record.error or "",
            },
        )

    # Existing-but-not-ready is not a missing resource. Returning 202 lets
    # the .NET client retry without confusing pending with a lost job.
    return JSONResponse(
        status_code=202,
        content={
            "jobId": job_id,
            "status": record.status,
            "detail": "Result is not ready yet.",
        },
    )


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str, request: Request):
    record = job_manager.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        snapshot = job_manager.snapshot(job_id)
        if snapshot is not None:
            yield f"data: {json.dumps({'type': 'snapshot', **snapshot})}\n\n"

            if snapshot.get("status") in TERMINAL_WORKER_STATUSES:
                return

        while True:
            if await request.is_disconnected():
                break

            try:
                update = await asyncio.to_thread(
                    record.event_queue.get,
                    True,
                    0.5,
                )
            except queue.Empty:
                yield ": heartbeat\n\n"
                current = job_manager.get_job(job_id)
                if (
                    current is not None
                    and current.status in TERMINAL_WORKER_STATUSES
                ):
                    break
                continue

            yield (
                f"id: {update.sequence}\n"
                f"data: {json.dumps(update.to_dict())}\n\n"
            )

            if update.type in (
                "worker_done",
                "job_failed",
                "job_cancelled",
            ):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )