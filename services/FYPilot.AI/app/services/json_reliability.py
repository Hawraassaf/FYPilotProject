"""
Robust JSON extraction, deterministic repair, truncation detection, and
bounded/redacted error-context diagnostics for LLM provider output.

Used by every generation provider adapter in app/services/llm_provider.py so
a malformed-but-repairable JSON response (HTTP 200, valid transport, invalid
syntax) is never treated the same as a genuine provider/transport failure.
Agent-agnostic on purpose -- this module has no knowledge of roadmaps,
Pydantic schemas, or any specific agent; callers that want the optional
provider-repair-request step pass in their own `repair_fn` and
`schema_description`.

Pipeline (see parse_json_response):
    raw provider text
      -> extract_json_object()   (fence/prose strip, quote-aware balanced
                                   brace/bracket scan -- never a naive
                                   first-"{"-to-last-"}" slice)
      -> json.loads()             (strict parse)
      -> [on failure] classify truncated vs. ordinary malformed
      -> [not truncated] local deterministic repair (json_repair library)
      -> re-parse
      -> [still failing, repair_fn given, content substantial] ONE
         provider repair request (schema-aware, caller-supplied) -> extract
         -> parse
      -> ParseOutcome (success / category / repair method / diagnostics)

This module never itself decides "provider unavailable" -- it only reports
what happened during parsing. The transport/timeout/HTTP-error categories
live in llm_provider.py's own try/except around the network call, since
only that layer can distinguish a raised exception from a successful
HTTP 200 with bad JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Error categories -- shared vocabulary between the transport layer
# (llm_provider.py, which knows about network/HTTP failures) and this
# module (which only ever produces the JSON/schema/semantic categories).
# Never reported to .NET; internal diagnostics/logging only.
# ---------------------------------------------------------------------------
TRANSPORT_FAILURE = "transport_failure"
TIMEOUT = "timeout"
PROVIDER_HTTP_ERROR = "provider_http_error"
EMPTY_RESPONSE = "empty_response"
INVALID_JSON_SYNTAX = "invalid_json_syntax"
SCHEMA_VALIDATION_FAILURE = "schema_validation_failure"
SEMANTIC_VALIDATION_FAILURE = "semantic_validation_failure"

_MIN_SUBSTANTIAL_CHARS = 400
_FINISH_REASONS_INDICATING_TRUNCATION = {"length", "max_tokens", "max_output_tokens"}


def is_substantial(text: str) -> bool:
    """True if there's enough real content to be worth ONE extra provider
    repair request, rather than an empty/near-empty garbage response."""
    return bool(text) and len(text.strip()) >= _MIN_SUBSTANTIAL_CHARS and text.count('"') >= 6


# ─────────────────────────────────────────────────────────────────────────
# Extraction -- fence/prose stripping + quote-aware balanced-span scan.
# ─────────────────────────────────────────────────────────────────────────


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    lines = stripped.split("\n")

    if lines and lines[0].startswith("```"):
        lines = lines[1:]

    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines).strip()


def _scan_balanced_span(text: str, start: int) -> int | None:
    """
    Quote-aware scan from `start` (text[start] must be '{' or '[') to the
    index of its matching close. Tracks whether we're inside a quoted
    string (and honors backslash escapes within it) so a brace or bracket
    that only appears inside a string value is never mistaken for
    structural JSON. Returns None if the text ends before the span
    balances back to depth 0 (i.e. likely truncated).
    """
    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index

    return None


def extract_json_object(text: str) -> str:
    """
    Best-effort JSON candidate substring from raw provider text: strips
    markdown code fences, skips any leading prose, and -- via the
    quote-aware scan above -- returns the first COMPLETE top-level
    object/array if one balances. If it never balances (truncated output),
    returns everything from the first '{'/'[' to the end of the text; this
    is a signal for looks_truncated() to catch, never a claim that the
    returned text is valid JSON.
    """
    if not text:
        return ""

    cleaned = _strip_code_fences(text)

    start = None
    for index, char in enumerate(cleaned):
        if char in "{[":
            start = index
            break

    if start is None:
        return cleaned

    end = _scan_balanced_span(cleaned, start)

    if end is not None:
        return cleaned[start:end + 1]

    return cleaned[start:]


def looks_truncated(candidate: str, *, finish_reason: str | None = None) -> bool:
    """
    True if `candidate` (already passed through extract_json_object) looks
    cut off mid-structure: an explicit length-limited finish_reason (when
    the provider exposes one), an unterminated quoted string, or an
    unbalanced bracket/brace count. Deliberately conservative -- a
    COMPLETE-but-malformed object (e.g. two complete siblings missing a
    comma between them) is NOT truncation, only genuine structural
    incompleteness is.
    """
    if finish_reason and str(finish_reason).strip().lower() in _FINISH_REASONS_INDICATING_TRUNCATION:
        return True

    stripped = candidate.rstrip()

    if not stripped:
        return True

    depth = 0
    in_string = False
    escape = False

    for char in stripped:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1

    if in_string:
        return True

    if depth > 0:
        return True

    if not stripped.endswith(("}", "]")):
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────
# Bounded, redacted diagnostics
# ─────────────────────────────────────────────────────────────────────────

_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9_-]{10,}"),
    re.compile(r"gsk_[a-zA-Z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{10,}", re.IGNORECASE),
)
_SECRET_FIELD_PATTERN = re.compile(
    r'("(?:api[_-]?key|authorization|password|secret|token|access[_-]?key)"\s*:\s*")[^"]*(")',
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return _SECRET_FIELD_PATTERN.sub(r"\1[REDACTED]\2", redacted)


def build_error_context(
    text: str,
    error: json.JSONDecodeError,
    *,
    before: int = 350,
    after: int = 500,
) -> dict[str, Any]:
    """
    Bounded, redacted excerpt around a JSONDecodeError's position --
    ~250-500 characters before/after, never the full raw response. Used for
    diagnostics/logging only; the full raw text is kept only in-memory by
    the caller for as long as it needs it, never logged in full.
    """
    position = min(error.pos, len(text))
    start = max(0, position - before)
    end = min(len(text), position + after)

    return {
        "line": error.lineno,
        "column": error.colno,
        "position": error.pos,
        "context": _redact(text[start:end]),
        "excerptTruncated": start > 0 or end < len(text),
    }


# ─────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ParseOutcome:
    success: bool
    data: dict[str, Any] | None
    category: str | None  # None on success; one of the module constants otherwise
    error: str | None
    initial_json_valid: bool
    is_truncated: bool = False
    repair_attempted: bool = False
    repair_method: str | None = None  # "local_json_repair" | "provider_repair" | None
    repair_success: bool = False
    repaired_char_count: int | None = None
    error_context: dict[str, Any] | None = None


def _try_local_repair(candidate: str) -> str | None:
    """Deterministic, syntax-only repair via the `json_repair` library --
    never invents roadmap/domain content, only corrects structural syntax
    (missing/trailing commas, unquoted keys, single quotes, ...). Returns
    the repaired JSON text, or None if the library is unavailable or itself
    raises."""
    try:
        import json_repair
    except ImportError:
        return None

    try:
        repaired = json_repair.repair_json(candidate)
    except Exception:
        return None

    return repaired or None


def parse_json_response(
    raw_text: str,
    *,
    repair_fn: Callable[[str, str], str | None] | None = None,
    schema_description: str = "",
    finish_reason: str | None = None,
) -> ParseOutcome:
    """
    Pure function -- no logging, no I/O beyond the optional `repair_fn`
    callback the caller supplies (a provider-specific "make one more low-
    temperature repair request" closure). Callers own their own structured
    logging using the returned ParseOutcome's fields.
    """
    if not raw_text or not raw_text.strip():
        return ParseOutcome(
            success=False, data=None, category=EMPTY_RESPONSE,
            error="Provider returned an empty response.", initial_json_valid=False,
        )

    candidate = extract_json_object(raw_text)

    try:
        data = json.loads(candidate)
        return ParseOutcome(success=True, data=data, category=None, error=None, initial_json_valid=True)
    except json.JSONDecodeError as exc:
        # `except ... as name` implicitly deletes `name` once the block
        # exits, so it must be captured into a variable declared outside
        # the except block before being used below.
        first_error = exc

    truncated = looks_truncated(candidate, finish_reason=finish_reason)
    error_context = build_error_context(candidate, first_error)

    repair_attempted = False
    repair_method: str | None = None

    # Truncated content is never handed to the local repairer: a JSON-repair
    # library "succeeding" on truncated input means it silently invented a
    # plausible-looking closure (fabricated brackets/quotes), which is
    # exactly the "blindly close brackets and accept incomplete content"
    # behavior this pipeline must not do. Truncation only ever proceeds to
    # the schema-aware provider repair request below (which is told the
    # response was cut off), never local repair.
    if not truncated:
        repair_attempted = True
        repair_method = "local_json_repair"
        repaired_text = _try_local_repair(candidate)

        if repaired_text:
            try:
                data = json.loads(repaired_text)
                return ParseOutcome(
                    success=True, data=data, category=None, error=None,
                    initial_json_valid=False, is_truncated=False,
                    repair_attempted=True, repair_method="local_json_repair",
                    repair_success=True, repaired_char_count=len(repaired_text),
                    error_context=error_context,
                )
            except Exception:
                pass

    if repair_fn is not None and is_substantial(candidate):
        repair_attempted = True
        repair_method = "provider_repair"

        try:
            repaired_by_provider = repair_fn(candidate, str(first_error))
        except Exception:
            repaired_by_provider = None

        if repaired_by_provider:
            repaired_candidate = extract_json_object(repaired_by_provider)
            try:
                data = json.loads(repaired_candidate)
                return ParseOutcome(
                    success=True, data=data, category=None, error=None,
                    initial_json_valid=False, is_truncated=truncated,
                    repair_attempted=True, repair_method="provider_repair",
                    repair_success=True, repaired_char_count=len(repaired_candidate),
                    error_context=error_context,
                )
            except Exception:
                pass

    return ParseOutcome(
        success=False,
        data=None,
        category=INVALID_JSON_SYNTAX,
        error=(
            f"Provider output was truncated before valid JSON completed "
            f"(line {error_context['line']}, column {error_context['column']})."
            if truncated else
            f"Provider returned malformed JSON that could not be repaired "
            f"(line {error_context['line']}, column {error_context['column']})."
        ),
        initial_json_valid=False,
        is_truncated=truncated,
        repair_attempted=repair_attempted,
        repair_method=repair_method,
        repair_success=False,
        error_context=error_context,
    )
