"""
guarded_call — the ONE wrapper used around every LLM invocation in the review
pipeline (Writer, Reviewer, Rewrite). Ensures every stage gets identical
treatment: input firewall -> real ProviderChain call -> output firewall ->
schema validation. No firewall/schema logic is duplicated per stage.

Deliberately works with the REAL app.services.llm_provider.LLMResult shape —
the actual candidate content is llm_result.data (already-parsed JSON) or a
parsed form of llm_result.text, never the LLMResult wrapper itself. Provider/
model/source metadata from that real result is preserved on GuardedResult so
callers never have to re-derive it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel

from app.llm_firewall.firewall import LlmFirewall
from app.llm_firewall.models import FirewallVerdict
from app.review import schema_validation
from app.services.llm_provider import LLMResult

Stage = Literal["writer", "reviewer", "rewrite"]


@dataclass
class GuardedCallRequest:
    stage: Stage
    trusted_parts: dict[str, str]
    untrusted_parts: dict[str, str]
    call_fn: Callable[[], LLMResult | None]
    schema: type[BaseModel] | None = None
    url_mode: str = "no_urls_allowed"
    allowed_sources: list[dict[str, Any]] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)


@dataclass
class GuardedResult:
    stage: Stage
    blocked: bool = False
    provider_failed: bool = False
    error: str | None = None
    output: dict | None = None
    schema_valid: bool = False
    verdict: FirewallVerdict | None = None
    provider: str | None = None
    model: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)


def output_hash(output: Any) -> str:
    try:
        serialized = json.dumps(output, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        serialized = str(output)

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_text(text: str | None) -> Any:
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return {"_text": text}


def guarded_call(request: GuardedCallRequest, firewall: LlmFirewall) -> GuardedResult:
    input_verdict = firewall.inspect_prompt(request.trusted_parts, request.untrusted_parts)

    if input_verdict.has_blocking_finding():
        return GuardedResult(stage=request.stage, blocked=True, verdict=input_verdict)

    llm_result: LLMResult | None = request.call_fn()

    if llm_result is None or not llm_result.ok:
        return GuardedResult(
            stage=request.stage,
            provider_failed=True,
            error=(llm_result.error if llm_result else "No provider returned a result."),
            provider=(llm_result.provider if llm_result else None),
            model=(llm_result.model if llm_result else None),
        )

    candidate = llm_result.data if llm_result.data is not None else _parse_text(llm_result.text)

    output_verdict = firewall.inspect_output(
        candidate,
        request.untrusted_parts,
        url_mode=request.url_mode,
        allowed_sources=request.allowed_sources,
        allowed_domains=request.allowed_domains,
    )

    if output_verdict.has_blocking_finding():
        return GuardedResult(
            stage=request.stage,
            blocked=True,
            verdict=output_verdict,
            provider=llm_result.provider,
            model=llm_result.model,
            sources=llm_result.sources,
        )

    if request.schema is not None:
        schema_ok, validated = schema_validation.validate(request.schema, candidate)
    else:
        schema_ok, validated = True, candidate

    return GuardedResult(
        stage=request.stage,
        output=validated,
        schema_valid=schema_ok,
        verdict=output_verdict,
        provider=llm_result.provider,
        model=llm_result.model,
        sources=llm_result.sources,
    )
