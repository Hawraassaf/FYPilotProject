"""
ProgressReporter -- the object threaded through ProviderChain, writer
agents, reviewers, and deterministic validation (see app/services/llm_provider.py
and each agent's *_worker.py, wired in Phase 5/7/8) so real backend
operations can report their own state honestly.

Every mutation this class makes to a JobRecord's stage_states is an EXPLICIT
call from the code that actually made that decision -- start_stage,
complete_stage, skip_stage, fail_stage. Nothing here (or anywhere on the
.NET side) ever infers "this stage must be done because a later one
started"; a skipped/failed/optional stage is only ever marked that way by
the specific code path that knows it was skipped/failed. This is what keeps
the loading UI honest: a circle becomes checked only because the operation
it represents actually confirmed finishing, never because time passed or
another stage began.

Chunk/token counters are scoped to the CURRENT provider attempt only --
fallback_started is the one place that resets them and bumps
provider_attempt_number, matching the "never combine chunk/token counts
across provider fallbacks" requirement.
"""

from __future__ import annotations

import time
from typing import Optional

from app.jobs.manager import AgentJobManager
from app.jobs.models import JobRecord, ProgressUpdate, StageState


class ProgressReporter:
    def __init__(self, job_id: str, manager: AgentJobManager) -> None:
        self._job_id = job_id
        self._manager = manager

    # ── Cancellation ─────────────────────────────────────────────────────────

    def is_cancelled(self) -> bool:
        return self._manager.is_cancelled(self._job_id)

    def cancellation_requested(self) -> None:
        """Pushed once, the moment a worker function observes is_cancelled()==True and is about to stop before starting a new stage -- distinct from the eventual terminal 'job_cancelled' event."""
        self._push_event("cancellation_requested")

    # ── Stage transitions (explicit only -- see module docstring) ──────────────

    def start_stage(self, stage_key: str, message: str = "") -> None:
        self._set_stage_state(stage_key, "current", message)
        self._push_event("stage_started", stage_key=stage_key)

    def complete_stage(self, stage_key: str, message: str = "") -> None:
        self._set_stage_state(stage_key, "completed", message)
        self._push_event("stage_completed", stage_key=stage_key)

    def skip_stage(self, stage_key: str, reason: str) -> None:
        self._set_stage_state(stage_key, "skipped", reason)
        self._push_event("stage_skipped", stage_key=stage_key)

    def fail_stage(self, stage_key: str, error: str) -> None:
        self._set_stage_state(stage_key, "failed", error)
        self._push_event("stage_failed", stage_key=stage_key)

    # ── Provider events (ProviderChain -- see app/services/llm_provider.py) ────

    def provider_started(self, provider: str, model: Optional[str] = None) -> None:
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.provider = provider
            record.model = model
            record.message = f"{provider} is generating a response"
        self._push_event("provider_started")

    def provider_chunk_received(self) -> None:
        """Called once per real streamed content chunk. Never carries a token count itself -- see provider_usage_received for real provider-reported usage metadata, which arrives separately (typically on a final, contentless chunk) and must not be conflated with a content chunk."""
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.current_attempt_chunk_count += 1
            chunk_count = record.current_attempt_chunk_count
            provider = record.provider
            message = f"{provider} is generating a response" if provider else "Generating a response"
            record.message = message
        self._push_event(
            "provider_chunk_received",
            message=f"{message} ({chunk_count} response chunks received)",
        )

    def provider_usage_received(self, token_count: int) -> None:
        """Sets the real generated-token count from a provider's own usage metadata (e.g. an OpenAI-compatible stream's final usage chunk, or Ollama's eval_count). Never estimated, never derived from generated/max_tokens -- see the streaming provider layer. Does NOT increment the chunk counter -- usage metadata is not itself a content chunk."""
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.current_attempt_token_count = token_count
        self._push_event("provider_usage_received")

    def provider_succeeded(self, provider: str, model: Optional[str] = None) -> None:
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.provider = provider
            if model is not None:
                record.model = model
        self._push_event("provider_succeeded")

    def provider_failed(self, provider: str, error: str) -> None:
        self._push_event("provider_failed", message=f"{provider} failed: {error}")

    def fallback_started(self, next_provider: str) -> None:
        """The one place current-attempt counters reset -- never combine chunk/token counts across providers."""
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.provider_attempt_number += 1
            record.current_attempt_chunk_count = 0
            record.current_attempt_token_count = None
            record.provider = next_provider
            record.message = f"Falling back to {next_provider}"
        self._push_event("fallback_started")

    def deadline_prevented_fallback(self, provider: str) -> None:
        self._push_event(
            "deadline_prevented_fallback",
            message=f"Not enough time remained to try {provider}.",
        )

    # ── Terminal transitions -- delegate the state change to AgentJobManager, then emit the corresponding terminal event ──

    def mark_worker_done(self, result: dict) -> None:
        self._manager.mark_worker_done(self._job_id, result)
        self._push_event("worker_done")

    def mark_worker_failed(self, error: str) -> None:
        self._manager.fail_job(self._job_id, error)
        self._push_event("job_failed", message=error)

    def mark_worker_cancelled(self) -> None:
        self._manager.mark_cancelled(self._job_id)
        self._push_event("job_cancelled", message="Cancelled.")

    # ── Internals ────────────────────────────────────────────────────────────

    def _record(self) -> Optional[JobRecord]:
        return self._manager.get_job(self._job_id)

    def _set_stage_state(self, stage_key: str, state: StageState, message: str) -> None:
        record = self._record()
        if record is None:
            return
        with record.lock:
            record.stage_states[stage_key] = state
            record.stage_key = stage_key
            if message:
                record.message = message
            if record.status == "queued":
                record.status = "running"

    def _push_event(self, event_type: str, *, stage_key: Optional[str] = None, message: Optional[str] = None) -> None:
        record = self._record()
        if record is None:
            return

        with record.lock:
            record.sequence += 1
            record.updated_at = time.time()

            update = ProgressUpdate(
                sequence=record.sequence,
                type=event_type,
                stage_key=stage_key if stage_key is not None else record.stage_key,
                stage_states=dict(record.stage_states),
                message=message if message is not None else record.message,
                provider=record.provider,
                model=record.model,
                current_attempt_chunk_count=record.current_attempt_chunk_count,
                current_attempt_token_count=record.current_attempt_token_count,
                provider_attempt_number=record.provider_attempt_number,
                timestamp=record.updated_at,
            )

        record.event_queue.put(update)
