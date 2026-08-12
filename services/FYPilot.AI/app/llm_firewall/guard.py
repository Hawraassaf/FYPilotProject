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
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel

from app.llm_firewall.firewall import LlmFirewall
from app.llm_firewall.models import FirewallVerdict
from app.review import schema_validation
from app.services.llm_provider import LLMResult

logger = logging.getLogger("fypilot-llm-firewall")

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
    # Kept separate (rather than a single "verdict") so callers can classify
    # findings by which side of the LLM call produced them -- input_verdict
    # covers the prompt-inspection pass (secrets in trusted/untrusted parts,
    # injection patterns in untrusted parts), output_verdict covers the
    # post-call inspection of the candidate itself (secrets, injection-echo,
    # URL policy). Either may be None when that check never ran (e.g.
    # output_verdict is None when blocked at the input stage, since the LLM
    # is never called).
    input_verdict: FirewallVerdict | None = None
    output_verdict: FirewallVerdict | None = None
    provider: str | None = None
    model: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    # Carried straight from LLMResult.parse_diagnostics (see llm_provider.py's
    # _parse_diagnostics) whenever a provider call actually reached JSON
    # parsing -- None when no LLMResult was returned at all, or the call
    # never got that far (e.g. a transport failure). Lets callers (e.g. the
    # SE Documentation per-section repair loop in pipeline.py) distinguish a
    # provider response that was truncated (isTruncated=True, stop_reason
    # max_tokens) from any other provider_failed cause, without re-deriving
    # that from the free-text `error` string.
    parse_diagnostics: dict[str, Any] | None = None


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
        return GuardedResult(stage=request.stage, blocked=True, input_verdict=input_verdict)

    llm_result: LLMResult | None = request.call_fn()

    if llm_result is None or not llm_result.ok:
        return GuardedResult(
            stage=request.stage,
            provider_failed=True,
            error=(llm_result.error if llm_result else "No provider returned a result."),
            provider=(llm_result.provider if llm_result else None),
            model=(llm_result.model if llm_result else None),
            input_verdict=input_verdict,
            parse_diagnostics=(llm_result.parse_diagnostics if llm_result else None),
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
        # Previously silent past this point -- a caller only ever saw
        # `blocked=True` (e.g. surfaced downstream as the generic
        # "review_unavailable"/"reviewer_unavailable" status), with no way
        # to tell WHICH firewall rule fired without re-running the exact
        # same LLM call under a debugger. Logging the finding categories
        # (never the raw candidate/output text itself -- these are the
        # firewall's own short rule-violation labels, not model content)
        # is what makes a live "why did this fall back" investigation
        # actually possible after the fact.
        blocking_rules = sorted({
            finding.rule for finding in output_verdict.findings if finding.action == "block"
        })
        logger.warning(
            "llm_firewall.output_blocked stage=%s provider=%s model=%s rules=%s",
            request.stage, llm_result.provider, llm_result.model, blocking_rules,
        )
        return GuardedResult(
            stage=request.stage,
            blocked=True,
            input_verdict=input_verdict,
            output_verdict=output_verdict,
            provider=llm_result.provider,
            model=llm_result.model,
            sources=llm_result.sources,
        )

    if request.schema is not None:
        validation_result = schema_validation.validate_detailed(request.schema, candidate)
        schema_ok, validated = validation_result.valid, validation_result.data
        if not schema_ok:
            # Same rationale as the firewall-block log above: this is the
            # ONE place `guarded_call` ever discovers a Reviewer/Writer/
            # Rewrite response didn't match its required schema -- and
            # schema_validation.validate() (the wrapper this used to call)
            # discards the compact Pydantic error list entirely, so the
            # caller previously had no way to see WHY validation failed,
            # only THAT it failed. Logs the compact error locations/types
            # (schema_validation._compact_validation_errors' own output --
            # already stripped of raw field values), never the candidate
            # content itself.
            logger.warning(
                "llm_firewall.schema_invalid stage=%s provider=%s model=%s errors=%s",
                request.stage, llm_result.provider, llm_result.model, validation_result.errors,
            )
    else:
        schema_ok, validated = True, candidate

    return GuardedResult(
        stage=request.stage,
        output=validated,
        schema_valid=schema_ok,
        input_verdict=input_verdict,
        output_verdict=output_verdict,
        provider=llm_result.provider,
        model=llm_result.model,
        sources=llm_result.sources,
    )
