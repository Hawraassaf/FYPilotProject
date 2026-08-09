"""
FYPilot LLM Provider Layer

INF-1 / RTA-1 mini implementation.

There are TWO completely separate provider chains. They never substitute
for each other -- a search provider never generates the final agent
answer, and a generation provider never performs live web search.

GENERATION chain (ProviderChain.providers, used by generate_json/generate_text):
1. DeepInfraProvider
   - Paid, pay-per-use OpenAI-compatible endpoint (no free-tier rate limiting)
   - Default model: meta-llama/Llama-3.3-70B-Instruct

2. GroqProvider
   - Normal mode: llama-3.3-70b-versatile (this is the generation fallback --
     NOT groq/compound-mini, which is search-only, see below)

3. OllamaProvider
   - Local fallback using qwen2.5-coder:7b by default

WEB-SEARCH chain (ProviderChain.search_providers, used only by search_web()):
1. BraveSearchProvider
   - Brave's LLM Context API -- primary web-search provider

2. GroqProvider (search_web mode only)
   - Search mode: groq/compound-mini -- secondary/fallback web search only,
     never used for normal generation

3. (no further fallback -- callers proceed with no live evidence)

GeminiProvider still exists below (unused in the default chain) in case it
needs to be re-added later -- see git history for when/why it was dropped.

This file lets agents switch providers without rewriting every agent.
"""

import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.services import json_reliability

logger = logging.getLogger("fypilot-llm-provider")

import requests

if TYPE_CHECKING:
    # Only for type hints -- avoids this module structurally depending on
    # app.jobs at runtime. ProgressReporter is duck-typed here: every
    # provider that accepts one simply calls whichever of its methods
    # apply, and every existing caller that omits it (reporter=None, the
    # default everywhere) gets today's exact non-streaming behavior,
    # unchanged.
    from app.jobs.reporter import ProgressReporter


@dataclass
class LLMResult:
    ok: bool
    provider: str
    model: str | None
    text: str
    data: dict[str, Any] | None
    error: str | None = None
    search_used: bool = False
    search_failed: bool = False

    # Sources are extracted only from provider tool metadata, never invented
    # from model-generated JSON.
    sources: list[dict[str, str]] = field(default_factory=list)
    executed_tools: list[dict[str, Any]] = field(default_factory=list)

    # WHY ok=False, when it is -- one of json_reliability's categories
    # (transport_failure/timeout/provider_http_error/empty_response/
    # invalid_json_syntax) or None on success. Purely internal diagnostics:
    # never serialized to .NET (LLMResult itself never crosses that
    # boundary), always defaults to None so no existing construction site
    # anywhere in the codebase needs to change. See json_reliability.py's
    # module docstring for why HTTP-200-but-malformed-JSON must never be
    # reported the same way as a genuine transport failure.
    error_category: str | None = None
    # Diagnostics from json_reliability.parse_json_response, when this
    # result went through JSON parsing -- repair method/success, whether
    # truncation was detected, bounded+redacted error context. None when no
    # parsing was attempted (e.g. transport failure) or not applicable
    # (generate_text). Internal only, same as error_category.
    parse_diagnostics: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────
# Shared JSON-repair-request plumbing -- used by every provider whose SDK
# exposes an OpenAI-compatible chat.completions.create surface (DeepInfra,
# Groq) for the ONE-extra-request "fix only JSON syntax" step (see
# json_reliability.py's module docstring for the full pipeline this plugs
# into). Kept provider-agnostic so it isn't duplicated per adapter.
# ─────────────────────────────────────────────────────────────────────────

_JSON_REPAIR_SYSTEM_PROMPT = (
    "You are a JSON syntax repair engine. Preserve all semantic values exactly. "
    "Do not add, remove, summarize, improve, or rewrite roadmap content. Correct "
    "only JSON syntax so it conforms to the supplied schema. Return JSON only."
)


def _build_repair_prompt(malformed_text: str, parse_error: str, schema_description: str) -> str:
    schema_block = f"EXPECTED SCHEMA (compact description):\n{schema_description}\n\n" if schema_description else ""
    return (
        f"{schema_block}"
        f"PARSER ERROR:\n{parse_error}\n\n"
        "MALFORMED RESPONSE TO FIX (return the corrected JSON only, no prose, no code fences):\n"
        f"{malformed_text}"
    )


def _classify_sdk_exception(ex: Exception) -> str:
    """
    Maps an exception raised by the `openai` or `groq` SDKs (structurally
    identical exception hierarchies -- both APITimeoutError/
    APIConnectionError/APIStatusError-with-status_code) to a
    json_reliability error category. Falls back to transport_failure for
    anything unrecognized. Never returns invalid_json_syntax -- that
    category only ever applies to a response that WAS received (HTTP 200)
    but failed to parse, classified separately by json_reliability itself,
    never here.
    """
    name = type(ex).__name__

    if "Timeout" in name:
        return json_reliability.TIMEOUT

    if "Connection" in name:
        return json_reliability.TRANSPORT_FAILURE

    if getattr(ex, "status_code", None) is not None:
        return json_reliability.PROVIDER_HTTP_ERROR

    return json_reliability.TRANSPORT_FAILURE


def _classify_requests_exception(ex: Exception) -> str:
    """Same purpose as _classify_sdk_exception, for OllamaProvider's raw
    `requests`-based calls instead of an SDK exception hierarchy."""
    if isinstance(ex, requests.exceptions.Timeout):
        return json_reliability.TIMEOUT

    if isinstance(ex, requests.exceptions.ConnectionError):
        return json_reliability.TRANSPORT_FAILURE

    if isinstance(ex, requests.exceptions.HTTPError):
        return json_reliability.PROVIDER_HTTP_ERROR

    return json_reliability.TRANSPORT_FAILURE


def _extract_retry_after_seconds(ex: Exception) -> float | None:
    """
    Best-effort extraction of a Retry-After value (seconds) from an openai/
    groq SDK exception's underlying HTTP response, when present. Returns
    None when unavailable or unparsable -- callers must treat that as "no
    safe retry-after known" and fall through to the normal cascade-to-next-
    provider behavior, never inventing a wait time.
    """
    response = getattr(ex, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None

    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None

    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _is_client_side_rejection(ex: Exception) -> bool:
    """True for a 4xx-style API status error -- the signal used to decide
    whether a response_format={"type": "json_object"} request was
    rejected by this provider/model combination and is worth retrying
    once without it, versus a timeout/connection/5xx failure that
    wouldn't be fixed by dropping the parameter."""
    status_code = getattr(ex, "status_code", None)
    return isinstance(status_code, int) and 400 <= status_code < 500


# A truncated candidate handed to the provider-repair step already consumed
# (close to) its ORIGINAL request's full max_tokens budget -- the repair
# prompt asks the model to echo the entire malformed text back corrected
# (see _build_repair_prompt), which needs AT LEAST that many tokens just to
# reproduce what's already there, plus more to finish the truncated
# structure and close it out. Reusing the original call's own max_tokens as
# the repair budget therefore reproduces the exact same truncation on the
# repair attempt itself -- observed live 2026-08-06: a database section
# truncated at max_tokens=14000, and its provider-repair call ALSO hit
# max_tokens (11193) and failed (repair_success=False), because 14000 was
# never enough headroom over an input that already used all 14000. 1.5x
# gives the repair call real room to both reproduce and complete the
# content; uncapped beyond that since every provider here already accepts
# max_tokens values far above the ~2200 default without error.
def _repair_max_tokens(original_max_tokens: int) -> int:
    return max(int(original_max_tokens * 1.5), 2200)


def _parse_diagnostics(outcome: "json_reliability.ParseOutcome") -> dict[str, Any]:
    return {
        "initialJsonValid": outcome.initial_json_valid,
        "isTruncated": outcome.is_truncated,
        "repairAttempted": outcome.repair_attempted,
        "repairMethod": outcome.repair_method,
        "repairSuccess": outcome.repair_success,
        "repairedCharCount": outcome.repaired_char_count,
        "errorContext": outcome.error_context,
    }


def _log_json_parse_result(
    *, provider: str, model: str | None, outcome: "json_reliability.ParseOutcome",
) -> None:
    """
    One structured log line per generate_json() attempt, distinguishing
    "provider responded but output JSON was malformed" from every other
    outcome -- see json_reliability.py's module docstring for the full
    category list. Never logs the full raw response/prompt, only the
    bounded+redacted error_context (already redacted by build_error_context).
    """
    if outcome.success and outcome.initial_json_valid:
        logger.info(
            "llm_provider.json_parse provider=%s model=%s initial_json_valid=true",
            provider, model,
        )
        return

    logger.info(
        "llm_provider.json_parse provider=%s model=%s initial_json_valid=false "
        "is_truncated=%s repair_method=%s repair_success=%s repaired_chars=%s "
        "line=%s column=%s position=%s context=%r",
        provider, model, outcome.is_truncated, outcome.repair_method,
        outcome.repair_success, outcome.repaired_char_count,
        (outcome.error_context or {}).get("line"),
        (outcome.error_context or {}).get("column"),
        (outcome.error_context or {}).get("position"),
        (outcome.error_context or {}).get("context"),
    )


def _clean_url(value: str) -> str:
    """Return a clean http/https URL or an empty string."""
    url = str(value or "").strip().strip('"\'<>')
    url = url.rstrip('.,;:!?)]}')

    if not url.lower().startswith(("http://", "https://")):
        return ""

    return url


def _fallback_title_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.") or "Web source"
    except Exception:
        return "Web source"


def _normalize_tool_data(value: Any) -> Any:
    """Convert SDK objects into normal Python dictionaries/lists."""
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool, dict, list)):
        return value

    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass

    return str(value)


def _source_from_mapping(value: dict[str, Any]) -> dict[str, str] | None:
    url = _clean_url(
        value.get("url")
        or value.get("link")
        or value.get("href")
        or value.get("source_url")
        or value.get("sourceUrl")
        or ""
    )

    if not url:
        return None

    title = str(
        value.get("title")
        or value.get("name")
        or value.get("source")
        or _fallback_title_from_url(url)
    ).strip()

    snippet = str(
        value.get("snippet")
        or value.get("description")
        or value.get("content")
        or value.get("text")
        or ""
    ).strip()

    return {
        "title": title[:240],
        "url": url,
        "snippet": snippet[:500],
    }


def _extract_sources_from_text(text: str) -> list[dict[str, str]]:
    """Extract URLs and nearby titles from actual tool-output text."""
    if not text:
        return []

    found: list[dict[str, str]] = []

    # Structured JSON tool output is common, so inspect it first.
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            found.extend(_extract_sources_from_value(parsed))
        except Exception:
            pass

    # Markdown links: [Article title](https://example.com/page)
    markdown_pattern = re.compile(r"\[([^\]]{1,240})\]\((https?://[^)\s]+)\)")
    for match in markdown_pattern.finditer(text):
        url = _clean_url(match.group(2))
        if url:
            found.append({
                "title": match.group(1).strip(),
                "url": url,
                "snippet": "",
            })

    # Plain URLs. Use the closest preceding Title: line when present.
    url_pattern = re.compile(r"https?://[^\s<>\"']+")
    for match in url_pattern.finditer(text):
        url = _clean_url(match.group(0))
        if not url:
            continue

        prefix = text[max(0, match.start() - 500):match.start()]
        title_matches = re.findall(
            r"(?:^|\n)\s*(?:title|source|name)\s*:\s*([^\n|]{1,240})",
            prefix,
            flags=re.IGNORECASE,
        )
        title = (
            title_matches[-1].strip()
            if title_matches
            else _fallback_title_from_url(url)
        )

        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end == -1:
            line_end = len(text)
        snippet = text[line_start:line_end].strip()

        found.append({
            "title": title[:240],
            "url": url,
            "snippet": snippet[:500],
        })

    return found


def _extract_sources_from_value(value: Any) -> list[dict[str, str]]:
    value = _normalize_tool_data(value)
    found: list[dict[str, str]] = []

    if isinstance(value, dict):
        direct = _source_from_mapping(value)
        if direct:
            found.append(direct)

        for nested in value.values():
            found.extend(_extract_sources_from_value(nested))

    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_sources_from_value(item))

    elif isinstance(value, str):
        found.extend(_extract_sources_from_text(value))

    return found


def _deduplicate_sources(
    sources: list[dict[str, str]],
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[str] = set()

    for source in sources:
        url = _clean_url(source.get("url", ""))
        if not url or url in seen:
            continue

        seen.add(url)
        unique.append({
            "title": str(source.get("title") or _fallback_title_from_url(url))[:240],
            "url": url,
            "snippet": str(source.get("snippet") or "")[:500],
        })

        if len(unique) >= limit:
            break

    return unique


def _normalize_executed_tools(value: Any) -> list[dict[str, Any]]:
    normalized = _normalize_tool_data(value)

    if not isinstance(normalized, list):
        return []

    tools: list[dict[str, Any]] = []
    for item in normalized:
        item = _normalize_tool_data(item)
        if isinstance(item, dict):
            tools.append(item)

    return tools


def _extract_sources_from_executed_tools(
    executed_tools: list[dict[str, Any]],
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    for tool in executed_tools:
        # Search results are returned inside the actual executed tool output.
        found.extend(_extract_sources_from_value(tool.get("output")))

        # Some SDK/API versions expose results under other keys.
        found.extend(_extract_sources_from_value(tool.get("results")))
        found.extend(_extract_sources_from_value(tool.get("search_results")))
        found.extend(_extract_sources_from_value(tool.get("citations")))

    return _deduplicate_sources(found)


def _used_web_tool(executed_tools: list[dict[str, Any]]) -> bool:
    for tool in executed_tools:
        tool_type = str(
            tool.get("type")
            or tool.get("name")
            or tool.get("tool_name")
            or ""
        ).lower()

        if "search" in tool_type or "visit" in tool_type or "browser" in tool_type:
            return True

    return False


def _accepts_keyword(callable_obj: Any, keyword: str) -> bool:
    """
    Compatibility check mirroring app.review.pipeline._accepts_keyword /
    app.agents.project_idea_agent._accepts_keyword (duplicated here, not
    imported, to keep this low-level module free of any dependency on
    either) -- lets an older search-provider test double that doesn't yet
    accept `writer_budget_seconds`/`deadline` keep working completely
    unchanged, while a real provider (or an updated fake) gets the deadline
    threaded through.
    """
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return True

    return any(
        parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _basic_secret_scan_ok(result: "LLMResult") -> bool:
    """
    Universal, context-free backstop applied to EVERY provider response,
    regardless of which agent called ProviderChain -- including the agents
    not (yet) wired into the full review pipeline. Only checks for hard,
    high-confidence secret patterns via app.llm_firewall.rules.secrets; the
    rich, context-aware LlmFirewall used by ReviewPipeline is a separate,
    more thorough check that only protects agents actually wired into it
    (see app/review/pipeline.py and the honest-scope note there).

    Returns False only when a "block"-severity secret pattern is found, in
    which case ProviderChain treats this provider's response as unusable and
    falls through to the next provider in the cascade -- the same behavior
    as any other provider failure.
    """
    from app.llm_firewall.rules import secrets as secret_rules

    text_to_scan = result.text or ""

    if result.data is not None:
        try:
            text_to_scan += json.dumps(result.data, ensure_ascii=False, default=str)
        except Exception:
            text_to_scan += str(result.data)

    if not text_to_scan:
        return True

    findings = secret_rules.scan({"provider_response": text_to_scan})
    return not any(finding.action == "block" for finding in findings)


class BaseProvider:
    name: str = "base"

    # Capability flags -- inspected before opting into a provider's native
    # structured-output mode (see json_reliability.py / each provider's
    # generate_json). Default False here so a not-yet-audited provider
    # never silently gets a parameter it hasn't been confirmed to accept;
    # each concrete class below overrides what it actually supports.
    supports_json_object: bool = False
    supports_json_schema: bool = False

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
    ) -> LLMResult:
        raise NotImplementedError

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        raise NotImplementedError

    def search_web(self, query: str) -> LLMResult:
        return LLMResult(
            ok=False,
            provider=self.name,
            model=None,
            text="",
            data=None,
            error=f"{self.name} does not implement direct web search",
            search_used=False,
            search_failed=True,
        )


class DeepInfraProvider(BaseProvider):
    """
    Primary cloud provider.

    Paid, pay-per-use inference with no free-tier rate limiting -- unlike
    Groq's free tier, which the on-demand/dev-tier gating made unreliable
    during time-sensitive use (e.g. a live presentation).

    OpenAI-compatible endpoint (https://api.deepinfra.com/v1/openai), so it
    reuses the `openai` SDK already in requirements.txt instead of a new
    dependency. Does not implement search_web -- DeepInfra has no built-in
    web-search tool equivalent to Groq Compound.

    Model defaults to DEEPINFRA_MODEL (or the tier-specific env var resolved
    by _deepinfra_model_for_tier), but callers needing a specific model for
    their task's accuracy/cost tradeoff can pass one explicitly -- see
    ProviderChain(tier=...).
    """

    name = "deepinfra"
    # OpenAI-compatible endpoint -- response_format={"type": "json_object"}
    # is a standard chat.completions.create parameter; whether the specific
    # backend model actually honors it varies (DeepInfra proxies many model
    # families), so _chat_completion_text always falls back to a plain
    # request on a 4xx-style rejection rather than assuming support blindly.
    supports_json_object = True

    # A 429 retry-after is only honored when it leaves at least this much
    # slack beyond the wait itself -- avoids sleeping right up to the edge
    # of the remaining Writer budget only to have zero time left to
    # actually make the retried request.
    _MIN_RETRY_MARGIN_SECONDS = 5.0

    def __init__(
        self,
        model: str | None = None,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = os.getenv("DEEPINFRA_API_KEY")

        self.model = model or os.getenv(
            "DEEPINFRA_MODEL",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        )

        # Mirrors GroqProvider's SEC-3 per-call timeout rationale: a hung
        # request must not block a review-pipeline attempt indefinitely.
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("DEEPINFRA_TIMEOUT_SECONDS", "60"),
        )

        # The openai SDK's own default (max_retries=2) means a single call
        # can silently take up to ~3x timeout_seconds before this provider
        # gives up and ProviderChain moves to Groq -- fine for most agents,
        # but too slow for a large-batch, latency-sensitive caller (e.g.
        # Idea Comparison's 12-idea prompt during a live demo). Callers with
        # that need can pass max_retries explicitly; every other agent
        # keeps today's default (None -> SDK default) unchanged.
        self.max_retries = max_retries

        self.enabled = bool(self.api_key)

    def _client(self, timeout_override: float | None = None):
        from openai import OpenAI

        client_kwargs = {
            "api_key": self.api_key,
            "base_url": "https://api.deepinfra.com/v1/openai",
            "timeout": timeout_override if timeout_override is not None else self.timeout_seconds,
        }

        if self.max_retries is not None:
            client_kwargs["max_retries"] = self.max_retries

        return OpenAI(**client_kwargs)

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
        writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        """
        `writer_budget_seconds`, when supplied, is a fresh "how much time is
        actually left" figure computed by the CALLER right before this
        specific attempt (see ProviderChain._run_cascade's
        cap_timeout_to_deadline path) -- never stored on self, so this stays
        safe when the same DeepInfraProvider instance is shared across
        concurrent calls (e.g. SE Documentation's bounded section queue).
        The actual HTTP timeout used is min(self.timeout_seconds,
        writer_budget_seconds) -- this only ever SHORTENS a single attempt
        when little Writer time remains; it never lengthens or globally
        changes this provider's configured timeout for any other caller,
        which never passes this parameter.
        """
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="DEEPINFRA_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
                error_category=json_reliability.TRANSPORT_FAILURE,
            )

        effective_timeout = (
            self.timeout_seconds if writer_budget_seconds is None
            else max(0.0, min(self.timeout_seconds, writer_budget_seconds))
        )

        system_message = (
            "You are a precise JSON-only AI engine. "
            "Return valid JSON only. "
            "Do not use markdown. "
            "Do not wrap the response in code fences."
        )

        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt},
        ]

        effective_max_tokens = max_tokens or 2200

        try:
            if reporter is not None:
                text = self._stream_completion(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=effective_max_tokens,
                    reporter=reporter,
                )
                finish_reason = None
            else:
                text, finish_reason = self._chat_completion_text(
                    messages=messages,
                    temperature=0.2,
                    # Same rationale as GroqProvider: SE Documentation's
                    # richer sections pass an explicit higher budget so
                    # responses aren't silently truncated into invalid JSON.
                    max_tokens=effective_max_tokens,
                    use_json_mode=True,
                    timeout_override=effective_timeout if writer_budget_seconds is not None else None,
                )
        except Exception as ex:
            category = _classify_sdk_exception(ex)
            status_code = getattr(ex, "status_code", None)
            logger.warning(
                "llm_provider.transport_failure provider=%s model=%s category=%s status_code=%s error=%s",
                self.name, self.model, category, status_code, ex,
            )

            # HTTP 429 (rate limited): honor Retry-After with a single retry
            # of this SAME provider only when it fits inside the remaining
            # Writer budget -- otherwise this falls straight through to the
            # normal failure return below, and ProviderChain moves on to the
            # next provider exactly as it already does for any other
            # failure. Never a loop -- at most one extra attempt.
            if (
                status_code == 429
                and writer_budget_seconds is not None
                and self.max_retries == 0  # only when the caller has already opted into single-attempt semantics
            ):
                retry_after = _extract_retry_after_seconds(ex)
                if retry_after is not None and retry_after < (writer_budget_seconds - self._MIN_RETRY_MARGIN_SECONDS):
                    logger.info(
                        "llm_provider.rate_limited_retry provider=%s model=%s retry_after=%.1fs remaining_budget=%.1fs",
                        self.name, self.model, retry_after, writer_budget_seconds,
                    )
                    time.sleep(retry_after)
                    remaining_after_wait = writer_budget_seconds - retry_after
                    try:
                        text, finish_reason = self._chat_completion_text(
                            messages=messages,
                            temperature=0.2,
                            max_tokens=effective_max_tokens,
                            use_json_mode=True,
                            timeout_override=max(0.0, min(self.timeout_seconds, remaining_after_wait)),
                        )
                    except Exception as retry_ex:
                        retry_category = _classify_sdk_exception(retry_ex)
                        logger.warning(
                            "llm_provider.transport_failure provider=%s model=%s category=%s error=%s (after one 429 retry)",
                            self.name, self.model, retry_category, retry_ex,
                        )
                        return LLMResult(
                            ok=False,
                            provider=self.name,
                            model=self.model,
                            text="",
                            data=None,
                            error=str(retry_ex),
                            search_used=False,
                            search_failed=use_search,
                            error_category=retry_category,
                        )
                    outcome = json_reliability.parse_json_response(
                        text,
                        repair_fn=(
                            (lambda candidate, error: self._repair_json_with_provider(
                                candidate, error, schema_description, max_tokens=_repair_max_tokens(effective_max_tokens),
                            ))
                            if schema_description else None
                        ),
                        schema_description=schema_description or "",
                        finish_reason=finish_reason,
                    )
                    _log_json_parse_result(provider=self.name, model=self.model, outcome=outcome)
                    return LLMResult(
                        ok=outcome.success,
                        provider=self.name,
                        model=self.model,
                        text=text,
                        data=outcome.data,
                        error=None if outcome.success else (outcome.error or "DeepInfra returned invalid JSON."),
                        search_used=False,
                        search_failed=use_search,
                        error_category=None if outcome.success else outcome.category,
                        parse_diagnostics=_parse_diagnostics(outcome),
                    )

            # HTTP 402 (payment required) and every other status: fail fast,
            # never retry the same provider -- the caller's cascade moves to
            # the next provider on its own.
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
                error_category=category,
            )

        outcome = json_reliability.parse_json_response(
            text,
            repair_fn=(
                (lambda candidate, error: self._repair_json_with_provider(
                    candidate, error, schema_description, max_tokens=_repair_max_tokens(effective_max_tokens),
                ))
                if schema_description else None
            ),
            schema_description=schema_description or "",
            finish_reason=finish_reason,
        )
        _log_json_parse_result(provider=self.name, model=self.model, outcome=outcome)

        return LLMResult(
            ok=outcome.success,
            provider=self.name,
            model=self.model,
            text=text,
            data=outcome.data,
            error=None if outcome.success else (outcome.error or "DeepInfra returned invalid JSON."),
            search_used=False,
            search_failed=use_search,
            error_category=None if outcome.success else outcome.category,
            parse_diagnostics=_parse_diagnostics(outcome),
        )

    def _chat_completion_text(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        use_json_mode: bool,
        timeout_override: float | None = None,
    ) -> tuple[str, str | None]:
        """
        Returns (text, finish_reason). Raises the underlying SDK exception
        on failure -- the caller classifies it via _classify_sdk_exception.
        Tries response_format={"type": "json_object"} first when
        use_json_mode is True and this provider/model combination is
        expected to support it; on a 4xx-style rejection specifically
        (never a timeout/connection/5xx, which a retry without the
        parameter would not fix), retries once without it so an
        unsupported model never breaks the whole call.

        `timeout_override` is a plain parameter, never stored on self --
        _client() builds a fresh SDK client per call already, so this stays
        safe when this same provider instance is shared across concurrent
        calls.
        """
        request_kwargs: dict[str, Any] = dict(
            model=self.model, temperature=temperature, max_tokens=max_tokens, messages=messages,
        )

        if use_json_mode and self.supports_json_object:
            try:
                response = self._client(timeout_override).chat.completions.create(
                    response_format={"type": "json_object"}, **request_kwargs,
                )
                choice = response.choices[0]
                return str(choice.message.content or ""), getattr(choice, "finish_reason", None)
            except Exception as ex:
                if not _is_client_side_rejection(ex):
                    raise

        response = self._client(timeout_override).chat.completions.create(**request_kwargs)
        choice = response.choices[0]
        return str(choice.message.content or ""), getattr(choice, "finish_reason", None)

    def _repair_json_with_provider(
        self, malformed_text: str, parse_error: str, schema_description: str | None,
        max_tokens: int = 2200,
    ) -> str | None:
        """
        ONE additional low-temperature request asking this SAME provider to
        fix ONLY JSON syntax -- never re-runs the full generation prompt.
        json_reliability.parse_json_response only ever calls this once per
        generate_json() attempt. Returns None (never raises) on any
        failure, which the caller treats as "repair unavailable".

        `max_tokens` must be AT LEAST as large as the original request's
        budget: a response that was truncated because the schema is bigger
        than the original budget would just be truncated again by a repair
        call capped at a smaller budget -- observed live against a large
        roadmap phase plan before this parameter existed.
        """
        try:
            response = self._client().chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=max(max_tokens, 2200),
                messages=[
                    {"role": "system", "content": _JSON_REPAIR_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_repair_prompt(
                        malformed_text, parse_error, schema_description or "",
                    )},
                ],
            )
            return str(response.choices[0].message.content or "") or None
        except Exception as ex:
            logger.warning("llm_provider.provider_repair_failed provider=%s error=%s", self.name, ex)
            return None

    def _stream_completion(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        reporter: "ProgressReporter",
    ) -> str:
        """
        Real token streaming (OpenAI-compatible SDK, stream=True) -- called
        only when a reporter is supplied, so every existing non-job caller
        keeps today's exact non-streaming request unchanged. Marks the
        provider succeeded only after the FULL response has been consumed
        (see the loop below never returning early on is_cancelled() -- an
        already-started stream is allowed to finish naturally, matching the
        honest-cancellation requirement: only the NEXT attempt is skipped,
        never an in-flight one aborted mid-stream). The one and only source
        of the reported token count is the stream's own final usage chunk
        (stream_options include_usage) -- never estimated from max_tokens.
        """
        stream = self._client().chat.completions.create(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},
        )

        text_parts: list[str] = []

        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if choices:
                delta = choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    text_parts.append(piece)
                    reporter.provider_chunk_received()

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                completion_tokens = getattr(usage, "completion_tokens", None)
                if isinstance(completion_tokens, int):
                    reporter.provider_usage_received(completion_tokens)

        return "".join(text_parts)

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="DEEPINFRA_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            messages = [
                {"role": "system", "content": "You are a helpful AI assistant."},
                {"role": "user", "content": prompt},
            ]

            if reporter is not None:
                text = self._stream_completion(
                    messages=messages,
                    temperature=0.3,
                    max_tokens=1800,
                    reporter=reporter,
                )
            else:
                response = self._client().chat.completions.create(
                    model=self.model,
                    temperature=0.3,
                    max_tokens=1800,
                    messages=messages,
                )
                text = str(response.choices[0].message.content or "")

            return LLMResult(
                ok=True,
                provider=self.name,
                model=self.model,
                text=text,
                data=None,
                error=None,
                search_used=False,
                search_failed=use_search,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class BraveSearchProvider(BaseProvider):
    """
    Primary WEB-SEARCH provider -- NOT a generation provider.

    This class deliberately does not override generate_json/generate_text
    (they raise NotImplementedError via BaseProvider), and this instance is
    NEVER placed in ProviderChain.providers (the generation chain) -- only
    in ProviderChain.search_providers. This keeps web search and answer
    generation as two genuinely separate chains, per design: Brave must
    never generate the final agent answer.

    Uses Brave's LLM Context API (POST /res/v1/llm/context), which returns
    pre-extracted snippets directly -- no separate page-fetch step is
    needed or performed.
    """

    name = "brave"

    def __init__(self):
        self.api_key = os.getenv("BRAVE_SEARCH_API_KEY")

        self.enabled = (
            os.getenv("BRAVE_SEARCH_ENABLED", "true").strip().lower() == "true"
            and bool(self.api_key)
        )

        self.timeout_seconds = float(os.getenv("BRAVE_SEARCH_TIMEOUT_SECONDS", "30"))
        self.max_urls = int(os.getenv("BRAVE_SEARCH_MAX_URLS", "8"))
        self.max_tokens = int(os.getenv("BRAVE_SEARCH_MAX_TOKENS", "4096"))
        self.country = os.getenv("BRAVE_SEARCH_COUNTRY", "").strip()
        self.language = os.getenv("BRAVE_SEARCH_LANGUAGE", "en").strip()

        self.endpoint = "https://api.search.brave.com/res/v1/llm/context"
        self.model_name = "brave-llm-context"

    # Confirmed empirically against the live Brave LLM Context API: queries
    # of 360 chars succeeded, 373 chars returned HTTP 422 (Unprocessable
    # Entity) -- the real boundary sits between the two. 320 is a safe
    # margin below it. Existing agent queries (e.g. FypMentorAgent's
    # instructional wrapper around the student's question, up to ~373
    # chars) previously exceeded Brave's real limit although comfortably
    # under this class's old 400-char cap -- this is the fix for that.
    _MAX_QUERY_LENGTH = 320

    @staticmethod
    def _clean_query(query: str) -> str:
        """
        Send only the intended search query to Brave -- never the full agent
        prompt. Strips control characters and collapses whitespace without
        altering the query's meaning; truncated to _MAX_QUERY_LENGTH to
        respect Brave's real query-length restriction (see comment above).
        """
        text = query or ""
        cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned[:BraveSearchProvider._MAX_QUERY_LENGTH]

    def search_web(
        self, query: str, *, writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        """
        `writer_budget_seconds`, when supplied, is a fresh "how much time is
        actually left" figure computed by the CALLER right before this
        specific attempt (see ProviderChain.search_web's deadline handling)
        -- same opt-in, never-stored-on-self contract as
        DeepInfraProvider/GroqProvider.generate_json's identical parameter.
        The actual HTTP timeout used is min(self.timeout_seconds,
        writer_budget_seconds) -- this only ever SHORTENS this one attempt
        when little Writer time remains; it never lengthens or globally
        changes this provider's configured timeout for any other caller,
        which never passes this parameter.
        """
        if not self.enabled:
            reason = (
                "BRAVE_SEARCH_API_KEY is missing"
                if not self.api_key
                else "Brave search is disabled by configuration"
            )
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model_name,
                text="",
                data=None,
                error=reason,
                search_used=False,
                search_failed=True,
            )

        effective_timeout = (
            self.timeout_seconds if writer_budget_seconds is None
            else max(0.0, min(self.timeout_seconds, writer_budget_seconds))
        )

        clean_query = self._clean_query(query)

        if not clean_query:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model_name,
                text="",
                data=None,
                error="Empty search query after sanitization",
                search_used=False,
                search_failed=True,
            )

        request_body: dict[str, Any] = {
            "q": clean_query,
            "count": 10,
            "maximum_number_of_urls": self.max_urls,
            "maximum_number_of_tokens": self.max_tokens,
            "maximum_number_of_snippets": 24,
            "maximum_number_of_tokens_per_url": 1024,
            "maximum_number_of_snippets_per_url": 3,
            "context_threshold_mode": "balanced",
        }

        # Do not send empty optional fields.
        if self.country:
            request_body["country"] = self.country
        if self.language:
            request_body["language"] = self.language

        try:
            # Exactly one request per search_web() call -- no retries, no
            # parallel Brave+Groq calls. On any failure below, ProviderChain
            # (not this class) moves on to Groq.
            response = requests.post(
                self.endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    # Never logged -- see _request_error(), which only ever
                    # records the exception TYPE, never headers or the key.
                    "X-Subscription-Token": self.api_key,
                },
                json=request_body,
                timeout=effective_timeout,
            )
        except requests.exceptions.Timeout:
            return self._search_error("Brave search request timed out")
        except requests.exceptions.RequestException as ex:
            return self._search_error(f"Brave search connection error: {type(ex).__name__}")

        if response.status_code != 200:
            # Covers 400, 401/403, 429, and 5xx alike -- all are simply
            # "Brave unsuccessful", never distinguished further to callers,
            # and never expose response body content (which could echo the
            # request or account details).
            return self._search_error(f"Brave search returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return self._search_error("Brave search returned invalid JSON")

        sources = self._parse_response(payload)

        if not sources:
            return self._search_error("Brave search returned no usable safe sources")

        return LLMResult(
            ok=True,
            provider=self.name,
            model=self.model_name,
            text="",
            data=None,
            error=None,
            search_used=True,
            search_failed=False,
            sources=sources,
        )

    def _search_error(self, message: str) -> LLMResult:
        return LLMResult(
            ok=False,
            provider=self.name,
            model=self.model_name,
            text="",
            data=None,
            error=message,
            search_used=False,
            search_failed=True,
        )

    def _parse_response(self, payload: Any) -> list[dict[str, str]]:
        """
        Parses the documented Brave LLM Context response shape:
        grounding.generic[] (url, title, snippets[]) plus an optional
        sources{} map keyed by URL for extra metadata.

        Maps into the project's EXISTING SearchSource contract (see
        _source_from_mapping/_deduplicate_sources above: {title, url,
        snippet}) rather than a new, incompatible model -- every downstream
        consumer (agents' _format_sources_for_prompt, the .NET
        IdeaEvidenceSourceDto mapping) already expects exactly these three
        keys. Hostname/date fields Brave may provide are deliberately not
        added to this shared dict: nothing downstream reads them today, and
        inventing new keys here would make Brave sources incompatible with
        every existing consumer of Groq-produced sources.
        """
        if not isinstance(payload, dict):
            return []

        grounding = payload.get("grounding")
        if not isinstance(grounding, dict):
            return []

        generic = grounding.get("generic")
        if not isinstance(generic, list):
            return []

        source_metadata = payload.get("sources")
        metadata_by_url: dict[str, dict[str, Any]] = {}
        if isinstance(source_metadata, dict):
            for key, value in source_metadata.items():
                if isinstance(value, dict):
                    metadata_by_url[_clean_url(str(key))] = value

        found: list[dict[str, str]] = []

        for item in generic:
            if not isinstance(item, dict):
                continue

            url = _clean_url(str(item.get("url") or ""))
            if not url:
                continue

            title = str(item.get("title") or "").strip()

            snippets = item.get("snippets")
            snippet_text = ""
            if isinstance(snippets, list):
                snippet_text = " ".join(
                    str(snippet).strip()
                    for snippet in snippets[:3]
                    if str(snippet).strip()
                )[:500]

            if not title:
                meta = metadata_by_url.get(url, {})
                title = str(meta.get("title") or "").strip()

            found.append({
                "title": (title or _fallback_title_from_url(url))[:240],
                "url": url,
                "snippet": snippet_text,
            })

        return _deduplicate_sources(found, limit=self.max_urls)


class GroqProvider(BaseProvider):
    """
    Primary cloud provider.

    use_search=False:
        Uses GROQ_MODEL, default llama-3.3-70b-versatile.

    use_search=True:
        Uses GROQ_SEARCH_MODEL, default groq/compound-mini.

    Groq Compound returns its server-side tool calls in message.executed_tools.
    This provider reads those raw tool results and extracts only real URLs that
    were returned by the web-search tool.
    """

    name = "groq"
    # Groq's API documents response_format={"type": "json_object"} support
    # for its own hosted models; _request always falls back to a plain
    # request on a 4xx-style rejection regardless, so this stays safe even
    # if a specific model/mode combination doesn't honor it.
    supports_json_object = True

    def __init__(
        self,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = os.getenv("GROQ_API_KEY")

        self.model = os.getenv(
            "GROQ_MODEL",
            "llama-3.3-70b-versatile",
        )

        self.search_model = os.getenv(
            "GROQ_SEARCH_MODEL",
            "groq/compound-mini",
        )

        # SEC-3: an explicit per-call timeout is required so a hung Groq
        # request cannot block a review-pipeline attempt indefinitely --
        # this is a prerequisite for (and separate from) the pipeline's own
        # aggregate wall-clock budget, which only checks time BETWEEN
        # iterations and cannot interrupt a single in-flight call.
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("GROQ_TIMEOUT_SECONDS", "60"),
        )

        # Same rationale as DeepInfraProvider.max_retries: the groq SDK's own
        # default (max_retries=2) means a single fallback attempt can
        # silently take up to ~3x timeout_seconds before this provider gives
        # up -- fine for most agents, but a latency-sensitive caller (e.g.
        # Idea Comparison, which fell back to Groq and then sat through a
        # 429-triggered retry loop well past its 45s end-to-end deadline)
        # needs a single fast attempt instead. None preserves every other
        # agent's existing behavior unchanged (SDK default).
        self.max_retries = max_retries

        self.enabled = bool(self.api_key)
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def _client(
        self, timeout_override: float | None = None, *, max_retries_override: int | None = None,
    ):
        """
        `max_retries_override`, when supplied, wins over `self.max_retries`
        for this ONE client -- used by search_web's deadline-aware path to
        force single-attempt semantics (max_retries=0) once a Writer
        deadline is involved, so the SDK's own retry-with-backoff can never
        multiply `timeout_override` past the remaining Writer budget (up to
        ~3x with the SDK's default max_retries=2 otherwise). None (the
        default) preserves `self.max_retries`-based behavior exactly for
        every other caller (generate_json, _repair_json_with_provider, and
        search_web when no deadline was supplied).
        """
        from groq import Groq

        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": timeout_override if timeout_override is not None else self.timeout_seconds,
        }

        if max_retries_override is not None:
            client_kwargs["max_retries"] = max_retries_override
        elif self.max_retries is not None:
            client_kwargs["max_retries"] = self.max_retries

        return Groq(**client_kwargs)

    def _request(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
        use_json_mode: bool = False,
        timeout_override: float | None = None,
    ) -> tuple[str, str | None, list[dict[str, Any]], list[dict[str, str]]]:
        """
        Normal Groq chat request used for structured generation. Returns
        (text, finish_reason, executed_tools, sources). When use_json_mode
        is set and this provider supports it, tries
        response_format={"type": "json_object"} first and falls back to a
        plain request on a 4xx-style rejection (see
        _is_client_side_rejection) -- never on a timeout/connection/5xx,
        which dropping the parameter wouldn't fix anyway. `timeout_override`
        is a plain parameter, never stored on self -- safe when this
        provider instance is shared across concurrent calls.
        """
        client = self._client(timeout_override)
        request_kwargs: dict[str, Any] = dict(
            model=model, temperature=temperature, max_tokens=max_tokens, messages=messages,
        )

        response = None
        if use_json_mode and self.supports_json_object:
            try:
                response = client.chat.completions.create(
                    response_format={"type": "json_object"}, **request_kwargs,
                )
            except Exception as ex:
                if not _is_client_side_rejection(ex):
                    raise

        if response is None:
            response = client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        message = choice.message
        text = str(message.content or "")
        finish_reason = getattr(choice, "finish_reason", None)
        executed_tools = _normalize_executed_tools(
            getattr(message, "executed_tools", None) or []
        )
        sources = _extract_sources_from_executed_tools(executed_tools)

        return text, finish_reason, executed_tools, sources

    def _repair_json_with_provider(
        self, malformed_text: str, parse_error: str, schema_description: str | None,
        max_tokens: int = 2200,
    ) -> str | None:
        """Same contract as DeepInfraProvider._repair_json_with_provider --
        ONE low-temperature JSON-syntax-only repair request, never raises,
        never re-runs the full generation prompt. `max_tokens` must be at
        least the original request's budget (see DeepInfraProvider's own
        docstring for why)."""
        try:
            client = self._client()
            response = client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=max(max_tokens, 2200),
                messages=[
                    {"role": "system", "content": _JSON_REPAIR_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_repair_prompt(
                        malformed_text, parse_error, schema_description or "",
                    )},
                ],
            )
            return str(response.choices[0].message.content or "") or None
        except Exception as ex:
            logger.warning("llm_provider.provider_repair_failed provider=%s error=%s", self.name, ex)
            return None

    def search_web(
        self, query: str, *, writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        """
        Run a small dedicated Groq Compound Mini web-search request.

        Search is separated from the large structured idea-generation prompt.
        This keeps the Compound request small and prevents 413 errors caused by
        combining web search, a long schema, and four complete idea objects.

        `writer_budget_seconds`: see BraveSearchProvider.search_web's
        identical, opt-in, never-stored-on-self contract -- the actual
        request timeout used is min(self.timeout_seconds,
        writer_budget_seconds), only ever shortening this one attempt.

        When `writer_budget_seconds` is supplied, this ALSO forces a
        single-attempt request (max_retries=0 for this one client),
        overriding self.max_retries -- otherwise the groq SDK's own
        retry-with-backoff (default max_retries=2) could re-attempt this
        same clamped timeout up to 3 times, multiplying the effective
        request time to roughly 3x the remaining Writer budget instead of
        bounding it. self.max_retries is completely unaffected, and every
        other caller of this provider (and this same method when no
        deadline is involved) keeps today's exact retry behavior.
        """
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=True,
            )

        effective_timeout = (
            self.timeout_seconds if writer_budget_seconds is None
            else max(0.0, min(self.timeout_seconds, writer_budget_seconds))
        )

        try:
            client = self._client(
                effective_timeout if writer_budget_seconds is not None else None,
                max_retries_override=0 if writer_budget_seconds is not None else None,
            )

            response = client.chat.completions.create(
                model=self.search_model,
                messages=[
                    {
                        "role": "user",
                        "content": query,
                    }
                ],
            )

            message = response.choices[0].message
            text = str(message.content or "")
            executed_tools = _normalize_executed_tools(
                getattr(message, "executed_tools", None) or []
            )
            sources = _extract_sources_from_executed_tools(executed_tools)
            web_tool_used = _used_web_tool(executed_tools)

            return LLMResult(
                ok=bool(web_tool_used and sources),
                provider=self.name,
                model=self.search_model,
                text=text,
                data=None,
                error=(
                    None
                    if web_tool_used and sources
                    else "Groq Compound returned no usable web-search sources."
                ),
                search_used=web_tool_used,
                search_failed=not web_tool_used or not sources,
                sources=sources,
                executed_tools=executed_tools,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.search_model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=True,
            )

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
        writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        # writer_budget_seconds: see DeepInfraProvider.generate_json's
        # docstring -- same opt-in, never-stored-on-self contract.
        # NOTE: reporter is accepted (for a uniform ProviderChain call
        # signature across every provider) but not yet used for token-level
        # streaming here, unlike DeepInfraProvider/OllamaProvider. Groq's
        # _request() extracts executed_tools/sources from the SDK's
        # non-streaming response shape, which streaming would meaningfully
        # complicate for this provider specifically (tool-call metadata
        # timing differs under stream=True) -- deferred rather than risking
        # that extraction, and called out as a known limitation in the
        # final report. ProviderChain still reports provider_started/
        # succeeded/failed/fallback_started around this call either way, so
        # the loading UI is never silent while Groq is in use, just without
        # a live chunk/token counter for this one provider.
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
                error_category=json_reliability.TRANSPORT_FAILURE,
            )

        model_to_use = self.search_model if use_search else self.model

        system_message = (
            "You are a precise JSON-only AI engine. "
            "Return valid JSON only. "
            "Do not use markdown. "
            "Do not wrap the response in code fences."
        )

        if use_search:
            system_message += (
                " You must use the live web-search tool before answering. "
                "Use current evidence for market, trend, competitor, and adoption claims. "
                "Do not invent citations or URLs. "
                "The application will read real sources directly from tool metadata, "
                "so do not place a sources list inside the JSON."
            )

        effective_max_tokens = max_tokens or 2200
        effective_timeout = (
            self.timeout_seconds if writer_budget_seconds is None
            else max(0.0, min(self.timeout_seconds, writer_budget_seconds))
        )

        try:
            text, finish_reason, executed_tools, sources = self._request(
                model=model_to_use,
                temperature=0.2,
                # Default (2200) fits every other agent's existing JSON shape.
                # SE Documentation's richer, higher-count sections (12-20
                # functional requirements, 10-18 test cases, ...) pass an
                # explicit higher budget -- without it, those responses were
                # silently truncated into invalid JSON, which looked like a
                # provider failure and collapsed the whole section to a
                # generic deterministic fallback.
                max_tokens=effective_max_tokens,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                # Native JSON mode conflicts with Compound's tool-calling
                # search flow (and that response never places sources
                # inside the JSON body anyway) -- only requested for plain,
                # non-search generation.
                use_json_mode=not use_search,
                timeout_override=effective_timeout if writer_budget_seconds is not None else None,
            )
        except Exception as ex:
            category = _classify_sdk_exception(ex)
            logger.warning(
                "llm_provider.transport_failure provider=%s model=%s category=%s error=%s",
                self.name, model_to_use, category, ex,
            )
            return LLMResult(
                ok=False,
                provider=self.name,
                model=model_to_use,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
                error_category=category,
            )

        web_tool_used = _used_web_tool(executed_tools)

        outcome = json_reliability.parse_json_response(
            text,
            repair_fn=(
                (lambda candidate, error: self._repair_json_with_provider(
                    candidate, error, schema_description, max_tokens=_repair_max_tokens(effective_max_tokens),
                ))
                if schema_description and not use_search else None
            ),
            schema_description=schema_description or "",
            finish_reason=finish_reason,
        )
        _log_json_parse_result(provider=self.name, model=model_to_use, outcome=outcome)

        return LLMResult(
            ok=outcome.success,
            provider=self.name,
            model=model_to_use,
            text=text,
            data=outcome.data,
            error=None if outcome.success else (outcome.error or "Groq returned invalid JSON."),
            search_used=web_tool_used,
            search_failed=bool(use_search and not web_tool_used),
            sources=sources,
            executed_tools=executed_tools,
            error_category=None if outcome.success else outcome.category,
            parse_diagnostics=_parse_diagnostics(outcome),
        )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        # See generate_json's note -- reporter accepted for signature
        # uniformity, streaming not yet implemented for Groq.
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GROQ_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        model_to_use = self.search_model if use_search else self.model

        try:
            system_message = "You are a helpful AI assistant."
            if use_search:
                system_message += (
                    " Use live web search before answering current factual questions. "
                    "Do not invent citations or URLs."
                )

            text, executed_tools, sources = self._request(
                model=model_to_use,
                temperature=0.3,
                max_tokens=1800,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
            )

            web_tool_used = _used_web_tool(executed_tools)

            return LLMResult(
                ok=True,
                provider=self.name,
                model=model_to_use,
                text=text,
                data=None,
                error=None,
                search_used=web_tool_used,
                search_failed=bool(use_search and not web_tool_used),
                sources=sources,
                executed_tools=executed_tools,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=model_to_use,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class AnthropicProvider(BaseProvider):
    """
    Direct Anthropic API provider (native Messages API, NOT the DeepInfra
    proxy for Claude models -- see llm_provider.py's roadmap/se_documentation
    tier comments for why: DeepInfra's Claude proxy was observed live
    truncating long structured responses at wildly inconsistent points
    (4.7k/18.9k/25.9k chars across repeated identical requests), which a
    direct connection to Anthropic's own API should not exhibit since it
    removes that third-party relay entirely.

    Only wired into ProviderChain for the "roadmap" and "se_documentation"
    tiers (see ProviderChain.__init__) -- every other agent's chain is
    unaffected and never sees this provider.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
        # Same SEC-3 rationale as GroqProvider/DeepInfraProvider: an explicit
        # per-call timeout is required so a hung request cannot block a
        # review-pipeline attempt indefinitely. Kept well below the 300s
        # Roadmap/SE Documentation writer budget (unlike the 240-280s
        # allowance DeepInfra's flaky Claude proxy needed) -- this provider
        # is now FIRST in a 4-provider chain (Anthropic -> DeepInfra -> Groq
        # -> Ollama), so it must leave real time for the rest of the cascade
        # if it fails, not consume nearly the whole budget itself. A direct
        # Anthropic connection (no third-party relay) is expected to be
        # meaningfully faster than DeepInfra's proxy was; adjust from live
        # testing if this is too tight.
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "120"),
        )
        self.max_retries = max_retries
        self.enabled = bool(self.api_key)
        # Claude Sonnet 5 defaults every request to output_config.effort=
        # "high", which lets it spend an unbounded share of max_tokens on
        # invisible internal reasoning before writing the actual answer --
        # confirmed live (and via Anthropic's own docs) as the root cause of
        # this provider's truncated JSON: thinking tokens count against the
        # same max_tokens budget, so truncation was happening well before
        # the requested budget was reached. "medium" is Anthropic's
        # documented recommendation for structured, non-agentic output like
        # ours (JSON generation, not multi-step reasoning) -- a meaningful
        # cost/speed step down from "high" with quality comparable to the
        # previous model generation at "high".
        self.effort = os.getenv("ANTHROPIC_EFFORT", "medium")

    def _client(self, timeout_override: float | None = None):
        from anthropic import Anthropic

        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": timeout_override if timeout_override is not None else self.timeout_seconds,
        }
        if self.max_retries is not None:
            client_kwargs["max_retries"] = self.max_retries

        return Anthropic(**client_kwargs)

    @staticmethod
    def _extract_text(response: Any) -> str:
        return "".join(
            getattr(block, "text", "") for block in (response.content or [])
            if getattr(block, "type", None) == "text"
        )

    def _repair_json_with_provider(
        self, malformed_text: str, parse_error: str, schema_description: str | None,
        max_tokens: int = 2200, deadline: float | None = None,
    ) -> str | None:
        """Same contract as DeepInfraProvider._repair_json_with_provider --
        ONE low-temperature JSON-syntax-only repair request, never raises.

        `deadline` is OPTIONAL (an absolute time.monotonic() timestamp, same
        convention as ReviewPipeline.run's `deadline` -- see that method's
        docstring) purely for the repair-attempt telemetry below: it lets
        llm_provider.anthropic_repair_usage report how much of the caller's
        overall wall-clock budget remained when this repair request started
        and finished, without this method making any timeout/scheduling
        decision of its own. None (the default, every existing caller before
        this parameter existed) simply omits that reading rather than
        guessing a budget.
        """
        requested_max_tokens = max(max_tokens, 2200)
        start = time.monotonic()
        remaining_at_start = None if deadline is None else round(deadline - start, 1)
        try:
            response = self._client().messages.create(
                model=self.model,
                # No `temperature` -- Claude Sonnet 5 rejects it with a 400
                # ("temperature is deprecated for this model"), observed live.
                max_tokens=requested_max_tokens,
                output_config={"effort": self.effort},
                # Fully disabled, not just lower effort -- structured JSON
                # generation doesn't need Claude's internal reasoning pass,
                # and disabling it removes thinking-token consumption from
                # max_tokens entirely rather than just reducing it.
                thinking={"type": "disabled"},
                system=_JSON_REPAIR_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": _build_repair_prompt(
                        malformed_text, parse_error, schema_description or "",
                    )},
                ],
            )
            text = self._extract_text(response)
            usage = getattr(response, "usage", None)
            end = time.monotonic()
            remaining_at_end = None if deadline is None else round(deadline - end, 1)
            logger.info(
                "llm_provider.anthropic_repair_usage provider=%s model=%s effort=%s stop_reason=%s "
                "requested_max_tokens=%s input_tokens=%s output_tokens=%s text_chars=%d "
                "duration=%.1fs deadline_remaining_at_start=%s deadline_remaining_at_end=%s",
                self.name, self.model, self.effort, getattr(response, "stop_reason", None),
                requested_max_tokens, getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None), len(text or ""),
                end - start, remaining_at_start, remaining_at_end,
            )
            return text or None
        except Exception as ex:
            logger.warning(
                "llm_provider.provider_repair_failed provider=%s model=%s error=%s "
                "deadline_remaining_at_start=%s",
                self.name, self.model, ex, remaining_at_start,
            )
            return None

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
        writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        """
        Same `writer_budget_seconds` contract as DeepInfraProvider.generate_json
        -- never stored on self, only ever shortens THIS one attempt's
        timeout to min(self.timeout_seconds, writer_budget_seconds).
        use_search is accepted for signature uniformity but unsupported --
        this provider is never wired into a tier that sets it (Roadmap/SE
        Documentation never search).
        """
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="ANTHROPIC_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
                error_category=json_reliability.TRANSPORT_FAILURE,
            )

        effective_timeout = (
            self.timeout_seconds if writer_budget_seconds is None
            else max(0.0, min(self.timeout_seconds, writer_budget_seconds))
        )

        # Approximate absolute deadline for the repair-attempt telemetry
        # below -- this provider has no absolute deadline parameter of its
        # own (see _repair_json_with_provider's docstring), only the
        # relative writer_budget_seconds ProviderChain._run_cascade already
        # recomputes fresh for each cascade attempt. Captured once here, at
        # generate_json entry, so the repair call's "remaining" reading is
        # relative to the SAME instant this whole attempt's budget was
        # measured, not a moving target.
        call_deadline = None if writer_budget_seconds is None else time.monotonic() + writer_budget_seconds

        system_message = (
            "You are a precise JSON-only AI engine. "
            "Return valid JSON only. "
            "Do not use markdown. "
            "Do not wrap the response in code fences."
        )

        # 2200 (the fallback every other provider uses when a caller omits
        # max_tokens, e.g. ReviewerAgent's own generate_json call) was
        # observed live consuming its ENTIRE budget on invisible thinking
        # tokens alone (thinking_tokens=2200, text_chars=0) -- Claude Sonnet
        # 5's adaptive thinking needs real headroom even at effort="medium",
        # so this provider's own no-max_tokens-given fallback is kept much
        # higher than DeepInfra/Groq's, which never had this problem.
        effective_max_tokens = max_tokens or 8000

        try:
            response = self._client(
                effective_timeout if writer_budget_seconds is not None else None,
            ).messages.create(
                model=self.model,
                # No `temperature` -- see the repair method's comment above;
                # Claude Sonnet 5 400s on this parameter entirely.
                max_tokens=effective_max_tokens,
                output_config={"effort": self.effort},
                # Fully disabled -- see the repair method's identical
                # comment above. Structured JSON generation doesn't need
                # Claude's internal reasoning pass, and this removes
                # thinking-token consumption from max_tokens entirely.
                thinking={"type": "disabled"},
                system=system_message,
                messages=[{"role": "user", "content": prompt}],
            )
            text = self._extract_text(response)
            finish_reason = getattr(response, "stop_reason", None)
            usage = getattr(response, "usage", None)
            thinking_tokens = getattr(
                getattr(usage, "output_tokens_details", None), "thinking_tokens", None,
            )
            logger.info(
                "llm_provider.anthropic_usage effort=%s stop_reason=%s output_tokens=%s thinking_tokens=%s "
                "text_chars=%d",
                self.effort, finish_reason, getattr(usage, "output_tokens", None), thinking_tokens, len(text),
            )
        except Exception as ex:
            category = _classify_sdk_exception(ex)
            status_code = getattr(ex, "status_code", None)
            logger.warning(
                "llm_provider.transport_failure provider=%s model=%s category=%s status_code=%s error=%s",
                self.name, self.model, category, status_code, ex,
            )
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
                error_category=category,
            )

        outcome = json_reliability.parse_json_response(
            text,
            repair_fn=(
                (lambda candidate, error: self._repair_json_with_provider(
                    candidate, error, schema_description,
                    max_tokens=_repair_max_tokens(effective_max_tokens), deadline=call_deadline,
                ))
                if schema_description else None
            ),
            schema_description=schema_description or "",
            finish_reason=finish_reason,
        )
        _log_json_parse_result(provider=self.name, model=self.model, outcome=outcome)

        return LLMResult(
            ok=outcome.success,
            provider=self.name,
            model=self.model,
            text=text,
            data=outcome.data,
            error=None if outcome.success else (outcome.error or "Anthropic returned invalid JSON."),
            search_used=False,
            search_failed=use_search,
            error_category=None if outcome.success else outcome.category,
            parse_diagnostics=_parse_diagnostics(outcome),
        )


class GeminiProvider(BaseProvider):
    """
    Secondary cloud provider.

    Uses your existing app/services/gemini_client.py.
    """

    name = "gemini"

    def __init__(self):
        self.enabled = bool(os.getenv("GEMINI_API_KEY"))

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
    ) -> LLMResult:
        # Excluded from the default ProviderChain (see the module
        # docstring) -- reporter accepted for signature uniformity only;
        # not streamed. schema_description accepted for signature
        # uniformity too but unused -- this provider predates the JSON
        # reliability pipeline and isn't part of the default chain.
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GEMINI_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            from app.services.gemini_client import GeminiClient

            client = GeminiClient()

            # Gemini's SDK has no explicit output-token cap in GeminiClient
            # today, so max_tokens is accepted (to keep this signature
            # interchangeable with GroqProvider/OllamaProvider) but unused.
            data = client.generate_json(
                prompt,
                use_search=use_search,
                fallback_without_search=True,
            )

            return LLMResult(
                ok=True,
                provider=self.name,
                model=getattr(client, "model_used", None)
                or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=data,
                error=getattr(client, "last_error", None),
                search_used=getattr(client, "search_used", False),
                search_failed=getattr(client, "search_failed", False),
                sources=_deduplicate_sources(
                    list(getattr(client, "sources", []) or [])
                    + list(getattr(client, "grounding_sources", []) or [])
                ),
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=None,
                text="",
                data=None,
                error="GEMINI_API_KEY is missing",
                search_used=False,
                search_failed=use_search,
            )

        try:
            from app.services.gemini_client import GeminiClient

            client = GeminiClient()

            text = client.generate_text(
                prompt,
                use_search=use_search,
                fallback_without_search=True,
            )

            return LLMResult(
                ok=True,
                provider=self.name,
                model=getattr(client, "model_used", None)
                or os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text=text,
                data=None,
                error=getattr(client, "last_error", None),
                search_used=getattr(client, "search_used", False),
                search_failed=getattr(client, "search_failed", False),
                sources=_deduplicate_sources(
                    list(getattr(client, "sources", []) or [])
                    + list(getattr(client, "grounding_sources", []) or [])
                ),
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=use_search,
            )


class OllamaProvider(BaseProvider):
    """
    Local fallback provider.

    Uses Ollama /api/generate.
    """

    name = "ollama"
    # Ollama's /api/generate natively supports format="json" (a hard
    # server-side JSON-object constraint, already passed as extra_body
    # below) -- no response_format-style fallback dance needed here, unlike
    # the OpenAI-compatible providers.
    supports_json_object = True

    def __init__(self, timeout_seconds: float | None = None):
        self.base_url = os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ).rstrip("/")

        self.model = os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5-coder:7b",
        )

        self.enabled = (
            os.getenv("OLLAMA_FALLBACK_ENABLED", "true").lower()
            == "true"
        )

        # Same rationale as DeepInfraProvider/GroqProvider's per-tier timing
        # overrides: the previous hardcoded (5, 90) request timeout let a
        # slow local model generation hang for up to 90s on its own --
        # observed live consuming almost this provider's entire read timeout
        # as the LAST fallback attempt on a request that had already spent
        # most of its 45s end-to-end deadline on DeepInfra+Groq, overshooting
        # the deadline by nearly 90s even with those two providers already
        # tightly bounded. None preserves the previous (5, 90) behavior for
        # every agent that doesn't request a tighter tier.
        self.timeout_seconds = timeout_seconds

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
        writer_budget_seconds: float | None = None,
    ) -> LLMResult:
        # writer_budget_seconds: see DeepInfraProvider.generate_json's
        # docstring -- same opt-in, never-stored-on-self contract.
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error="OLLAMA_FALLBACK_ENABLED is false",
                search_used=False,
                search_failed=False,
                error_category=json_reliability.TRANSPORT_FAILURE,
            )

        options: dict[str, Any] = {
            "temperature": 0.2,
            # Scale the context window with the requested output budget
            # so a larger SE Documentation section isn't cut short by a
            # fixed 4096-token window -- default preserved for every
            # other caller that doesn't pass max_tokens.
            "num_ctx": max(4096, max_tokens * 2) if max_tokens else 4096,
        }
        if max_tokens:
            options["num_predict"] = max_tokens

        started_at = time.monotonic()
        effective_timeout = self.timeout_seconds or 90
        if writer_budget_seconds is not None:
            effective_timeout = max(0.0, min(effective_timeout, writer_budget_seconds))

        try:
            text = self._generate(
                prompt=prompt,
                options=options,
                extra_body={"format": "json"},
                reporter=reporter,
                timeout_override=effective_timeout,
            )
        except Exception as ex:
            elapsed = time.monotonic() - started_at
            category = _classify_requests_exception(ex)
            logger.warning(
                "llm_provider.transport_failure provider=%s model=%s category=%s "
                "elapsed=%.1fs timeout=%.1fs error=%s",
                self.name, self.model, category, elapsed, effective_timeout, ex,
            )
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=False,
                error_category=category,
            )

        elapsed = time.monotonic() - started_at
        logger.info(
            "llm_provider.ollama_request_completed provider=%s model=%s elapsed=%.1fs timeout=%.1fs",
            self.name, self.model, elapsed, effective_timeout,
        )

        outcome = json_reliability.parse_json_response(
            text,
            repair_fn=(
                (lambda candidate, error: self._repair_json_with_provider(
                    candidate, error, schema_description, max_tokens=max_tokens,
                ))
                if schema_description else None
            ),
            schema_description=schema_description or "",
        )
        _log_json_parse_result(provider=self.name, model=self.model, outcome=outcome)

        return LLMResult(
            ok=outcome.success,
            provider=self.name,
            model=self.model,
            text=text,
            data=outcome.data,
            error=None if outcome.success else (outcome.error or "Ollama returned invalid JSON."),
            search_used=False,
            search_failed=False,
            error_category=None if outcome.success else outcome.category,
            parse_diagnostics=_parse_diagnostics(outcome),
        )

    def _repair_json_with_provider(
        self, malformed_text: str, parse_error: str, schema_description: str | None,
        max_tokens: int | None = None,
    ) -> str | None:
        """Same contract as DeepInfraProvider._repair_json_with_provider.
        Ollama's /api/generate has no separate system-message slot, so the
        repair instruction is prepended directly to the single prompt
        field. `max_tokens` mirrors the original request's budget (see
        DeepInfraProvider's own docstring for why) via the same
        num_ctx/num_predict scaling generate_json itself uses."""
        try:
            repair_prompt = (
                f"{_JSON_REPAIR_SYSTEM_PROMPT}\n\n"
                f"{_build_repair_prompt(malformed_text, parse_error, schema_description or '')}"
            )
            repair_options: dict[str, Any] = {
                "temperature": 0,
                "num_ctx": max(4096, max_tokens * 2) if max_tokens else 4096,
            }
            if max_tokens:
                repair_options["num_predict"] = max_tokens

            text = self._generate(
                prompt=repair_prompt,
                options=repair_options,
                extra_body={"format": "json"},
                reporter=None,
            )
            return text or None
        except Exception as ex:
            logger.warning("llm_provider.provider_repair_failed provider=%s error=%s", self.name, ex)
            return None

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error="OLLAMA_FALLBACK_ENABLED is false",
                search_used=False,
                search_failed=False,
            )

        try:
            text = self._generate(
                prompt=prompt,
                options={"temperature": 0.3, "num_ctx": 4096},
                extra_body={},
                reporter=reporter,
            )

            return LLMResult(
                ok=True,
                provider=self.name,
                model=self.model,
                text=text,
                data=None,
                error=None,
                search_used=False,
                search_failed=False,
            )

        except Exception as ex:
            return LLMResult(
                ok=False,
                provider=self.name,
                model=self.model,
                text="",
                data=None,
                error=str(ex),
                search_used=False,
                search_failed=False,
            )

    def _generate(
        self,
        *,
        prompt: str,
        options: dict[str, Any],
        extra_body: dict[str, Any],
        reporter: "ProgressReporter | None",
        timeout_override: float | None = None,
    ) -> str:
        """
        Shared request body for generate_json/generate_text. When reporter
        is supplied, requests Ollama's own streamed NDJSON response
        ("stream": true) and reports each line as a real chunk; the
        deadline-aware (5, timeout) read timeout is unchanged either way.
        When reporter is None, this is byte-for-byte today's single
        non-streaming POST. `timeout_override` is a plain parameter (never
        stored on self) -- when omitted, behaves exactly as before
        (self.timeout_seconds or 90).
        """
        read_timeout = timeout_override if timeout_override is not None else (self.timeout_seconds or 90)

        if reporter is None:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": options,
                    **extra_body,
                },
                timeout=(5, read_timeout),
            )
            response.raise_for_status()
            return response.json().get("response", "")

        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": True,
                "options": options,
                **extra_body,
            },
            timeout=(5, read_timeout),
            stream=True,
        )
        response.raise_for_status()

        text_parts: list[str] = []

        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            chunk = json.loads(line)
            piece = chunk.get("response", "")
            if piece:
                text_parts.append(piece)
                reporter.provider_chunk_received()

            if chunk.get("done"):
                # Ollama's own final line reports real generated-token
                # counts (eval_count) -- never estimated, never derived
                # from a num_predict/max_tokens upper bound.
                eval_count = chunk.get("eval_count")
                if isinstance(eval_count, int):
                    reporter.provider_usage_received(eval_count)
                break

        return "".join(text_parts)


# Per-task accuracy/cost tier for the DeepInfra leg of the chain -- some
# agents (SE Documentation) need the highest-accuracy model available,
# others (Defense Simulator's question/evaluation prompts) don't, so a
# single global DEEPINFRA_MODEL would either overpay everywhere or
# underpower the agents that need it most. Each tier's model is still
# overridable per-deployment via its own env var.
_DEEPINFRA_TIER_DEFAULTS: dict[str, str] = {
    # Highest-accuracy tier: strict-schema, high-stakes generation (SE Docs,
    # Project Roadmap, Idea Generator).
    # NOTE: DeepInfra's API requires the full "org/model" slug -- the bare
    # display name from DeepInfra's own pricing table (e.g. "claude-opus-4-8")
    # 404s as model_not_found. Verified live against the real API.
    "high": "anthropic/claude-opus-4-8",
    # Default tier: most agents (market needs, project DNA, market
    # footprint) -- confirmed working, cheap, good instruction-following.
    "standard": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    # Lightweight tier: short, simple prompts (Defense Simulator).
    "light": "google/gemma-3-12b-it",
    # Mentor Chat tier: interactive, latency-sensitive, needs strong coding
    # ability (generates codeBlocks) and broad multilingual support -- Qwen3
    # Coder is a coding-specialized MoE model (fast for its size due to
    # sparse activation) rather than a general dense model tuned for one
    # of those three at the expense of the others.
    "mentor": "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo",
}

# Idea Comparison deliberately reuses the "light" tier's model rather than a
# new hardcoded one -- measured live against the "standard" 70B model on the
# same 4-idea comparison prompt: the 70B model timed out at 40s, while
# gemma-3-12b-it finished in ~22.5s with equally idea-specific, comparative,
# non-repetitive output. A ranking/explanation task over a handful of
# already-generated ideas doesn't need the highest-accuracy tier.
_DEEPINFRA_TIER_DEFAULTS["comparison"] = _DEEPINFRA_TIER_DEFAULTS["light"]

# Project Roadmap gets its OWN tier -- originally the same DeepInfra model as
# "high" so its Ollama fallback leg (see _OLLAMA_TIER_TIMING below) could use
# a longer, roadmap-specific timeout without changing the shared "high"
# tier's timing for Idea Generator. Was moved off "high" (anthropic/claude-
# opus-4-8, expensive) to "standard"'s model (cheap) for cost, but live
# testing showed the 70B model consistently dropping 2-3 of the mandatory
# lifecycle categories (safety_validation, data_governance_privacy,
# architecture_design) from its phase plan under this prompt's combined
# constraint load, even with named example phases spelled out for each --
# moved to Claude Sonnet 5 for stronger instruction-following on this
# specific many-constraint task; same "org/model" slug format as "high"
# requires (see that tier's comment). The roadmap-specific timing tuning
# below is unaffected: only the model changed, not the timeout/retry values.
_DEEPINFRA_TIER_DEFAULTS["roadmap"] = _DEEPINFRA_TIER_DEFAULTS["standard"]
# NOTE: Claude Sonnet 5 (anthropic/claude-sonnet-5) was tried here and wrote
# noticeably better, more complete content when it worked -- but DeepInfra's
# proxy for it truncated long responses at wildly inconsistent points across
# repeated live tests (4.7k / 18.9k / 25.9k chars), which isn't fixable by
# tuning a timeout or token budget number. Reverted to the reliable 70B
# model for the Roadmap tier's live deadline; revisit if DeepInfra's Claude
# proxy stability improves.

# Idea Comparison's job-based Reviewer stage only (app/jobs/workers/
# idea_comparison_worker.py) -- kept separate from "comparison" above, which
# stays on gemma-3-12b-it for the Writer/Rewrite calls. Live testing showed
# the 12B model judging its own rubric inconsistently -- e.g. flagging a
# concrete, non-score-based justification as "fabricated_score" while its own
# description text said "this is a concrete reason, not a fabricated score" --
# so the Reviewer specifically needs a more reliable model. gemma-3-27b-it is
# the same model family (so it should follow the existing rubric wording more
# consistently than switching families) at roughly 2.25x the parameters,
# priced well below the "standard" 70B tier.
_DEEPINFRA_TIER_DEFAULTS["comparison_review"] = "google/gemma-3-27b-it"

# SE Documentation gets its OWN tier so its DeepInfra per-call timeout (see
# _DEEPINFRA_TIER_TIMING below) can be sized for its actual ~6500-token
# structured JSON sections (measured live at 121s) without changing
# "standard"'s 60s default for every other agent sharing that tier (market
# needs, project DNA, project footprint). Claude Sonnet 5 was tried here
# (same rationale as "roadmap": a rejected document whose Architecture and
# AI Technical Report sections described mutually inconsistent AI
# approaches) but never live-verified before Sonnet's DeepInfra proxy was
# found to truncate long Roadmap responses at inconsistent points -- kept on
# the reliable 70B model here too rather than ship an unverified path.
_DEEPINFRA_TIER_DEFAULTS["se_documentation"] = _DEEPINFRA_TIER_DEFAULTS["standard"]

# Per-tier timing overrides for the DeepInfra leg of the chain. Absent here
# (every tier except "comparison") means "use DeepInfraProvider's own
# defaults" (DEEPINFRA_TIMEOUT_SECONDS env var, openai SDK's default retry
# count) -- unchanged for every other agent. This is what lets a
# latency-sensitive caller (e.g. Idea Comparison, budgeted for a live-defense-
# facing 2-5 idea batch) ask ProviderChain(tier=...) for a faster, single-
# attempt DeepInfra call WITHOUT constructing its own DeepInfraProvider --
# the shared ProviderChain still owns order/fallback/timing/retries.
_DEEPINFRA_TIER_TIMING: dict[str, dict[str, float | int]] = {
    # Sized for the writer call, the primary deliverable (the actual
    # idea-specific comparison) -- measured live at ~14s/20s/31s for
    # 2/3/4 ideas (a direct 4-idea benchmark against real DeepInfra
    # measured 31.3s -- right at a 30.0s timeout's edge, and observed
    # timing out intermittently at exactly that setting live through the
    # router). 34s gives that common 4-idea case a real margin to
    # complete instead of a coin-flip timeout, while still leaving room
    # in the 45s end-to-end deadline for the Groq/Ollama fallback legs
    # below (each now tightly bounded) if DeepInfra genuinely fails.
    "comparison": {"timeout_seconds": 34.0, "max_retries": 0},
    # Sized for the job-based Reviewer call on gemma-3-27b-it (see
    # "comparison_review" above) -- a bigger model with a larger input
    # prompt (full candidate JSON + rubric + context) than the writer call,
    # so it gets more room than "comparison"'s 34s. The job path's own
    # deadline gates (_MIN_SECONDS_FOR_SYNC_REVIEW/_MIN_SECONDS_FOR_REWRITE
    # in idea_comparison_worker.py) are sized to comfortably cover this.
    "comparison_review": {"timeout_seconds": 50.0, "max_retries": 0},
    # Configurable via ROADMAP_DEEPINFRA_TIMEOUT_SECONDS (default 120s).
    # The structured per-task phase-plan schema needs a bigger max_tokens
    # budget than most JSON agents (see ProjectRoadmapAgent.
    # _estimate_phase_plan_max_tokens), which in turn takes the model
    # longer to generate -- DeepInfraProvider's own 60s default timeout
    # was observed live cutting the request off with a timeout error
    # before the (now-larger) response could finish, immediately after
    # widening the token budget fixed the previous truncation failure.
    # max_retries=0 so a genuine timeout fails fast into the Groq fallback
    # leg rather than the SDK's own retry-with-backoff eating further time.
    "roadmap": {"timeout_seconds": None, "max_retries": 0},  # resolved by _deepinfra_timing_for_tier
    # Configurable via SE_DOCUMENTATION_DEEPINFRA_TIMEOUT_SECONDS (default
    # 180s). Measured live: a ~6500-token structured JSON section (the size
    # SE Documentation's writer sections actually request) took 121s on
    # meta-llama/Llama-3.3-70B-Instruct-Turbo -- the previous 60s default
    # (shared with "standard") was cutting every section off before it could
    # finish, forcing a wasteful cascade to Groq on every single section.
    # max_retries=0 for the same reason as "roadmap": a genuine timeout
    # should fail fast into the Groq leg once, not have the SDK silently
    # retry (and re-pay for) the same slow call 2 more times first.
    "se_documentation": {"timeout_seconds": None, "max_retries": 0},  # resolved by _deepinfra_timing_for_tier
}

# Same rationale as _DEEPINFRA_TIER_TIMING, for the Groq fallback leg.
# GroqProvider's own default (60s timeout, groq SDK's default max_retries=2)
# meant that once DeepInfra failed/timed out (already consuming most of a
# 45s end-to-end deadline), a SINGLE Groq fallback attempt could itself take
# up to ~180s -- observed live as a 429 rate-limit response followed by the
# SDK's own retry/backoff, blowing the request well past .NET's hard cutoff
# even though the writer's own compare() call never saw a chance to return.
# Absent here (every tier except "comparison") means "use GroqProvider's own
# defaults" -- unchanged for every other agent.
# Groq's org-level tokens-per-minute cap observed live (HTTP 413 "Request
# too large ... TPM: Limit 12000, Requested 29987") -- callers that can
# estimate their own request size before calling (see ProviderChain.
# generate_json's opt-in estimated_prompt_tokens/provider_token_limits, only
# used today by SE Documentation's targeted rewrite path) use this so a
# request already known to exceed the limit is never sent to Groq at all,
# instead of paying for a guaranteed 413. Configurable rather than hardcoded
# in case Groq's account tier changes; every caller that doesn't pass
# provider_token_limits is completely unaffected.
_DEFAULT_GROQ_REQUEST_TOKEN_LIMIT = 12000


def groq_request_token_limit() -> int:
    return int(os.getenv("GROQ_REQUEST_TOKEN_LIMIT", str(_DEFAULT_GROQ_REQUEST_TOKEN_LIMIT)))


_GROQ_TIER_TIMING: dict[str, dict[str, float | int]] = {
    "comparison": {"timeout_seconds": 6.0, "max_retries": 0},
    # Same tight fallback budget as "comparison" -- Groq/Ollama always use
    # their own single globally-configured model regardless of DeepInfra
    # tier (see GroqProvider/OllamaProvider's GROQ_MODEL/OLLAMA_MODEL env
    # vars), so this is purely about keeping a fallback attempt fast, not
    # about the Reviewer's bigger primary model.
    "comparison_review": {"timeout_seconds": 6.0, "max_retries": 0},
}

# Same rationale again, for the final local-Ollama fallback leg. Its
# previous hardcoded (5, 90) request timeout meant that even after DeepInfra
# and Groq were both tightly bounded for the "comparison" tier, a THIRD
# fallback attempt (Ollama, when a local server happens to be running) could
# still hang for up to 90s of local CPU inference -- observed live consuming
# almost the full 90s as the last leg of a request that had already spent
# most of its 45s end-to-end deadline, overshooting the deadline by nearly
# 90s. Absent here (every tier except "comparison") means "use the previous
# (5, 90) timeout" -- unchanged for every other agent.
_OLLAMA_TIER_TIMING: dict[str, dict[str, float]] = {
    "comparison": {"timeout_seconds": 3.0},
    "comparison_review": {"timeout_seconds": 3.0},
    # Configurable via ROADMAP_OLLAMA_TIMEOUT_SECONDS (default 180s) rather
    # than a hardcoded literal -- the structured roadmap phase/task schema
    # is large enough that a small local model can legitimately need more
    # than the generic 90s default, without raising the timeout for every
    # other "high"-tier agent (see _DEEPINFRA_TIER_DEFAULTS["roadmap"]
    # above). Evaluated lazily (a function, not a literal) so the env var
    # can still be changed without a process restart in tests.
    "roadmap": {"timeout_seconds": None},  # resolved by _ollama_timing_for_tier
}


def _deepinfra_model_for_tier(tier: str) -> str:
    resolved_tier = tier if tier in _DEEPINFRA_TIER_DEFAULTS else "standard"
    env_key = f"DEEPINFRA_MODEL_{resolved_tier.upper()}"
    return os.getenv(env_key, _DEEPINFRA_TIER_DEFAULTS[resolved_tier])


_DEFAULT_ROADMAP_DEEPINFRA_TIMEOUT_SECONDS = 280.0
_DEFAULT_SE_DOCUMENTATION_DEEPINFRA_TIMEOUT_SECONDS = 180.0


def _deepinfra_timing_for_tier(tier: str) -> dict[str, float | int]:
    timing = dict(_DEEPINFRA_TIER_TIMING.get(tier, {}))

    if tier == "roadmap":
        timing["timeout_seconds"] = float(
            os.getenv("ROADMAP_DEEPINFRA_TIMEOUT_SECONDS", str(_DEFAULT_ROADMAP_DEEPINFRA_TIMEOUT_SECONDS)),
        )

    if tier == "se_documentation":
        timing["timeout_seconds"] = float(
            os.getenv(
                "SE_DOCUMENTATION_DEEPINFRA_TIMEOUT_SECONDS",
                str(_DEFAULT_SE_DOCUMENTATION_DEEPINFRA_TIMEOUT_SECONDS),
            ),
        )

    return timing


def _groq_timing_for_tier(tier: str) -> dict[str, float | int]:
    return _GROQ_TIER_TIMING.get(tier, {})


_DEFAULT_ROADMAP_OLLAMA_TIMEOUT_SECONDS = 180.0


def _ollama_timing_for_tier(tier: str) -> dict[str, float]:
    timing = dict(_OLLAMA_TIER_TIMING.get(tier, {}))

    if tier == "roadmap":
        timing["timeout_seconds"] = float(
            os.getenv("ROADMAP_OLLAMA_TIMEOUT_SECONDS", str(_DEFAULT_ROADMAP_OLLAMA_TIMEOUT_SECONDS)),
        )

    return timing


class ProviderChain:
    """
    Two genuinely separate provider cascades, both owned by this class.

    GENERATION chain (self.providers, used by generate_json/generate_text):
    1. DeepInfra
    2. Groq
    3. Ollama

    This makes DeepInfra (paid, no free-tier rate limiting) the main
    provider, Groq the backup cloud provider, and local Ollama the final
    fallback. GeminiProvider is intentionally excluded from the default
    chain -- pass providers explicitly to include it.

    `tier` selects the DeepInfra model ("high" / "standard" / "light" /
    "mentor", see _DEEPINFRA_TIER_DEFAULTS) and is ignored if `providers`
    is passed explicitly. Brave is never part of this chain -- it has no
    generate_json/generate_text implementation and must never generate an
    agent's final answer.

    WEB-SEARCH chain (self.search_providers, used only by search_web()):
    1. Brave (BraveSearchProvider) -- primary
    2. Groq (search_web mode, groq/compound-mini) -- fallback
    (no further fallback -- callers proceed with no live evidence)

    Kept as a genuinely separate list from `self.providers` so a change to
    one chain (e.g. swapping the primary search provider) can never affect
    the other (e.g. which model answers a student's question).
    """

    def __init__(
        self,
        providers: list[BaseProvider] | None = None,
        tier: str = "standard",
        search_providers: list[BaseProvider] | None = None,
    ):
        deepinfra_timing = _deepinfra_timing_for_tier(tier)
        groq_timing = _groq_timing_for_tier(tier)
        ollama_timing = _ollama_timing_for_tier(tier)

        # Anthropic (direct API, NOT the DeepInfra Claude proxy -- see
        # AnthropicProvider's docstring for why) is prepended as the FIRST
        # provider only for "roadmap" and "se_documentation" -- every other
        # tier's chain (mentor, market needs, project DNA, etc.) is
        # completely unaffected and never constructs an AnthropicProvider at
        # all. When ANTHROPIC_API_KEY is unset, AnthropicProvider.enabled is
        # False and generate_json fails fast with a clear "key is missing"
        # error, so the cascade falls straight through to DeepInfra exactly
        # as before -- this is safe to leave wired in even without a key set.
        anthropic_providers = (
            [AnthropicProvider()] if tier in ("roadmap", "se_documentation") else []
        )

        self.providers = providers or anthropic_providers + [
            DeepInfraProvider(
                model=_deepinfra_model_for_tier(tier),
                max_retries=deepinfra_timing.get("max_retries"),
                timeout_seconds=deepinfra_timing.get("timeout_seconds"),
            ),
            GroqProvider(
                max_retries=groq_timing.get("max_retries"),
                timeout_seconds=groq_timing.get("timeout_seconds"),
            ),
            OllamaProvider(timeout_seconds=ollama_timing.get("timeout_seconds")),
        ]

        self.search_providers = search_providers or [
            BraveSearchProvider(),
            GroqProvider(),
        ]

    def search_web(self, query: str, *, deadline: float | None = None) -> LLMResult:
        """
        Run direct web search with the first search_providers entry that
        succeeds: Brave first, Groq Compound second, matching the class
        docstring's WEB-SEARCH chain. Generation providers are never
        substituted for the search chain (this loop no longer touches
        self.providers at all) because the API must report whether real
        source URLs were actually obtained, and because Brave/Groq search
        must stay fully independent of which generation provider is active.

        `deadline`, when supplied, is an absolute time.monotonic()
        timestamp -- mirrors generate_json's deadline handling: skips
        starting a search provider once fewer than
        _MIN_SECONDS_PER_PROVIDER_ATTEMPT remain (the SAME shared floor
        generate_json's cascade uses), and clamps each provider's own
        configured timeout to whatever budget is left by forwarding
        `writer_budget_seconds` into that provider's own search_web call --
        only when that provider actually accepts the keyword (see
        _accepts_keyword), so an older search-provider test double without
        it keeps working unchanged. None (the default) preserves the exact
        prior unbounded behavior for every existing caller (FypMentorAgent,
        MarketNeedsAgent, MarketFootprintAgent, etc.) that doesn't pass one.
        """
        errors: list[str] = []

        for provider in self.search_providers:
            writer_budget_seconds = deadline - time.monotonic() if deadline is not None else None

            if writer_budget_seconds is not None and writer_budget_seconds < self._MIN_SECONDS_PER_PROVIDER_ATTEMPT:
                errors.append(f"{provider.name} -> skipped, insufficient deadline time remaining")
                break

            search_kwargs: dict[str, Any] = {}
            if deadline is not None and _accepts_keyword(provider.search_web, "writer_budget_seconds"):
                search_kwargs["writer_budget_seconds"] = writer_budget_seconds

            result = provider.search_web(query, **search_kwargs)

            if result.ok and result.search_used and result.sources:
                return result

            errors.append(
                f"{result.provider}:{result.model} -> {result.error}"
            )

        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="Web search failed. " + " | ".join(errors),
            search_used=False,
            search_failed=True,
        )

    # Below this many remaining deadline seconds, starting one more provider
    # attempt (even a fast local Ollama call) has too little of a chance to
    # complete before the caller's own end-to-end deadline -- skip it and
    # let the cascade end (fall through to the next provider's own check, or
    # to the final "all providers failed" result) instead of starting a call
    # a caller with a `deadline` has no time left to wait for. Ignored
    # entirely when `deadline` is None (every caller that doesn't pass one
    # keeps today's unconditional cascade, unchanged).
    _MIN_SECONDS_PER_PROVIDER_ATTEMPT = 4.0

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        max_tokens: int | None = None,
        deadline: float | None = None,
        reporter: "ProgressReporter | None" = None,
        schema_description: str | None = None,
        cap_timeout_to_deadline: bool = False,
        estimated_prompt_tokens: int | None = None,
        provider_token_limits: dict[str, int] | None = None,
    ) -> LLMResult:
        """
        `estimated_prompt_tokens`/`provider_token_limits` are a SEPARATE
        opt-in pair (both default None, so every existing caller is
        unaffected): when both are given, any provider named in
        `provider_token_limits` whose limit is smaller than
        `estimated_prompt_tokens` is skipped entirely -- recorded as
        `provider_ineligible_for_payload_size` -- rather than sent a request
        already known to be rejected for size. Every other provider (not
        named in the dict) is unaffected and still tried in the usual order.
        See se_documentation_rewrite_scope.py's targeted rewrite, the only
        current caller.

        `schema_description` is an OPTIONAL, opt-in compact description of
        the expected JSON shape -- when given, a provider whose deterministic
        local JSON repair fails may make ONE additional low-temperature
        "fix only JSON syntax" request to itself before this call is
        considered failed and the cascade moves to the next provider (see
        json_reliability.py / each provider's generate_json). Omitted
        (None, the default) preserves every existing caller's behavior
        exactly -- no provider-repair-request is ever attempted without it.

        `cap_timeout_to_deadline` is a SEPARATE opt-in from `deadline` --
        `deadline` alone (the existing, shared behavior used by every other
        caller) only decides whether to SKIP starting the next fallback
        provider when too little time remains; it never shortens an
        individual provider's own configured request timeout. Setting
        `cap_timeout_to_deadline=True` additionally passes
        `writer_budget_seconds` (a fresh `deadline - time.monotonic()`,
        recomputed for EACH provider in the cascade) into that provider's
        own generate_json call, so effective_timeout = min(configured
        provider timeout, remaining budget) -- see SE Documentation's
        bounded section queue, the only current caller of this flag. False
        (default) leaves every other caller's behavior byte-for-byte
        unchanged, including callers that already pass `deadline`.
        """
        return self._run_cascade(
            call=lambda provider, writer_budget_seconds: provider.generate_json(
                prompt, use_search=use_search, max_tokens=max_tokens, reporter=reporter,
                schema_description=schema_description,
                **({"writer_budget_seconds": writer_budget_seconds} if cap_timeout_to_deadline else {}),
            ),
            is_success=lambda result: result.ok and result.data is not None,
            deadline=deadline,
            reporter=reporter,
            use_search=use_search,
            estimated_prompt_tokens=estimated_prompt_tokens,
            provider_token_limits=provider_token_limits,
        )

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        deadline: float | None = None,
        reporter: "ProgressReporter | None" = None,
    ) -> LLMResult:
        return self._run_cascade(
            call=lambda provider, writer_budget_seconds: provider.generate_text(
                prompt, use_search=use_search, reporter=reporter,
            ),
            is_success=lambda result: bool(result.ok and result.text),
            deadline=deadline,
            reporter=reporter,
            use_search=use_search,
        )

    def _run_cascade(
        self,
        *,
        call: "Any",
        is_success: "Any",
        deadline: float | None,
        reporter: "ProgressReporter | None",
        use_search: bool,
        estimated_prompt_tokens: int | None = None,
        provider_token_limits: dict[str, int] | None = None,
    ) -> LLMResult:
        """
        Shared sequential-fallback loop for generate_json/generate_text.
        When reporter is None, this is byte-for-byte the original
        loop/break/error-message behavior -- no reporter calls happen at
        all, so every existing synchronous caller is unaffected. When a
        reporter is supplied: provider_started fires for the first attempt,
        fallback_started for every subsequent one, provider_succeeded/
        provider_failed bracket each call's outcome, deadline_prevented_fallback
        fires when the remaining-deadline check trips, and is_cancelled() is
        checked before EVERY attempt (not just the first) so cancellation
        stops any further fallback from starting -- while never aborting a
        call already in flight (the honest-cancellation requirement: an
        already-started attempt is always allowed to finish naturally).

        `call` receives (provider, writer_budget_seconds) -- the second
        argument is `deadline - time.monotonic()` recomputed fresh for THIS
        provider (None when `deadline` itself is None), always passed
        through regardless of cap_timeout_to_deadline; only generate_json's
        cap_timeout_to_deadline flag decides whether its lambda actually
        forwards it into the provider call.
        """
        errors: list[str] = []

        for index, provider in enumerate(self.providers):
            if reporter is not None and reporter.is_cancelled():
                errors.append(f"{provider.name} -> skipped, cancelled")
                break

            if (
                estimated_prompt_tokens is not None
                and provider_token_limits is not None
                and provider.name in provider_token_limits
                and estimated_prompt_tokens > provider_token_limits[provider.name]
            ):
                errors.append(
                    f"{provider.name} -> skipped, provider_ineligible_for_payload_size "
                    f"(estimated {estimated_prompt_tokens} tokens exceeds "
                    f"{provider_token_limits[provider.name]} limit)"
                )
                if reporter is not None:
                    reporter.provider_failed(provider.name, "provider_ineligible_for_payload_size")
                continue

            writer_budget_seconds = deadline - time.monotonic() if deadline is not None else None

            if writer_budget_seconds is not None and writer_budget_seconds < self._MIN_SECONDS_PER_PROVIDER_ATTEMPT:
                errors.append(f"{provider.name} -> skipped, insufficient deadline time remaining")
                if reporter is not None:
                    reporter.deadline_prevented_fallback(provider.name)
                break

            if reporter is not None:
                if index == 0:
                    reporter.provider_started(provider.name)
                else:
                    reporter.fallback_started(provider.name)

            result = call(provider, writer_budget_seconds)

            if is_success(result):
                if not _basic_secret_scan_ok(result):
                    errors.append(
                        f"{result.provider}:{result.model} -> response withheld by basic secret scan"
                    )
                    if reporter is not None:
                        reporter.provider_failed(provider.name, "response withheld by basic secret scan")
                    continue

                if reporter is not None:
                    reporter.provider_succeeded(result.provider, result.model)
                return result

            errors.append(
                f"{result.provider}:{result.model} -> [{result.error_category or 'unknown'}] {result.error}"
            )
            if reporter is not None:
                reporter.provider_failed(provider.name, result.error or "unknown error")

        return LLMResult(
            ok=False,
            provider="none",
            model=None,
            text="",
            data=None,
            error="All providers failed. " + " | ".join(errors),
            search_used=False,
            search_failed=use_search,
        )


