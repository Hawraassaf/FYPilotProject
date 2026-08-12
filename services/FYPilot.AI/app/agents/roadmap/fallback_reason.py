"""
Typed fallback-reason classification for ProjectRoadmapAgent.

Roadmap-specific (not shared with the review pipeline or other agents) --
see project_roadmap_agent.py and routers/roadmap.py, the only two callers.
Exists so a deterministic-fallback Roadmap always carries a precise,
non-guessed reason instead of every failure category collapsing into the
single generic "provider_unavailable" pipeline status.

Codes are plain strings (not a Python Enum) because they cross the JSON
boundary to .NET verbatim -- a string is what actually goes over the wire,
so this keeps the source of truth and the wire value identical.
"""

from __future__ import annotations

# Category A: provider transport/auth/quota failure -- the call to the
# provider itself never produced a response worth parsing.
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
PROVIDER_RATE_LIMITED = "provider_rate_limited"
PROVIDER_TIMEOUT = "provider_timeout"
PROVIDER_PAYLOAD_INVALID = "provider_payload_invalid"

# Category B: a provider responded, but the response could not be turned
# into a usable phase plan.
JSON_PARSE_FAILED = "json_parse_failed"
SCHEMA_INVALID = "schema_invalid"

# Category C: the phase plan parsed fine but was rejected by Roadmap's own
# deterministic sanity gates.
INSUFFICIENT_USABLE_PHASES = "insufficient_usable_phases"
BLOCKED_CONTENT = "blocked_content"
LIFECYCLE_COVERAGE_FAILED = "lifecycle_coverage_failed"
DELIVERABLE_COVERAGE_FAILED = "deliverable_coverage_failed"

# Category D: the candidate passed the Writer stage but failed later in the
# shared review pipeline (reviewer/rewrite stage, not this agent).
REVIEWER_UNAVAILABLE = "reviewer_unavailable"
SEMANTIC_REWRITE_FAILED = "semantic_rewrite_failed"
FINAL_REVIEW_FAILED = "final_review_failed"

# Category F: the Writer's own reserved slice of the pipeline's absolute
# deadline (see routers/roadmap.py's writer_deadline) ran out before any
# provider produced a usable phase plan -- distinct from
# PROVIDER_TIMEOUT (a single provider's own configured request timeout
# firing) because this can also happen when a provider call is still
# succeeding, just too slowly to leave any budget for review; see
# ProjectRoadmapAgent._classify_generation_failure.
WRITER_DEADLINE_EXCEEDED = "writer_deadline_exceeded"

UNKNOWN = "unknown"

# Safe, user-facing summaries -- no stack traces, no prompts, no secrets.
# Kept short and factual; the precise technical detail (exception text,
# provider name, HTTP status) stays server-side in logs only.
_SAFE_MESSAGES: dict[str, str] = {
    PROVIDER_UNAVAILABLE: "The AI providers could not be reached.",
    PROVIDER_AUTHENTICATION_FAILED: "An AI provider rejected the request due to a configuration/authentication issue.",
    PROVIDER_RATE_LIMITED: "An AI provider is currently rate-limited.",
    PROVIDER_TIMEOUT: "AI generation took too long and timed out.",
    PROVIDER_PAYLOAD_INVALID: "An AI provider returned an unusable response.",
    JSON_PARSE_FAILED: "The AI response could not be parsed as valid structured output.",
    SCHEMA_INVALID: "The AI response did not match the required roadmap structure.",
    INSUFFICIENT_USABLE_PHASES: "The AI proposed too few usable project phases.",
    BLOCKED_CONTENT: "The AI response was blocked by the content safety firewall.",
    LIFECYCLE_COVERAGE_FAILED: "The AI's phase plan was missing required project lifecycle coverage.",
    DELIVERABLE_COVERAGE_FAILED: "The AI's phase plan promised a deliverable that no task actually produces.",
    REVIEWER_UNAVAILABLE: "The AI quality reviewer could not be reached in time.",
    SEMANTIC_REWRITE_FAILED: "A quality issue in the AI roadmap could not be corrected.",
    FINAL_REVIEW_FAILED: "The AI roadmap could not be verified after correction.",
    WRITER_DEADLINE_EXCEEDED: "AI roadmap generation ran out of time before a valid response was produced.",
    UNKNOWN: "AI roadmap generation did not succeed for an unspecified reason.",
}


def safe_message_for(code: str) -> str:
    return _SAFE_MESSAGES.get(code, _SAFE_MESSAGES[UNKNOWN])


def classify_provider_error(error_category: str | None, error_text: str | None) -> str:
    """
    Maps a provider-chain LLMResult's (error_category, error text) to one of
    the typed codes above. error_text is only ever inspected for a small,
    non-secret HTTP status-code substring (e.g. "429", "401") -- never
    logged or returned verbatim by this function.
    """
    from app.services import json_reliability

    text = (error_text or "")

    if error_category == json_reliability.TIMEOUT:
        return PROVIDER_TIMEOUT

    if error_category == json_reliability.TRANSPORT_FAILURE:
        return PROVIDER_UNAVAILABLE

    if error_category == json_reliability.PROVIDER_HTTP_ERROR:
        if "429" in text:
            return PROVIDER_RATE_LIMITED
        if "401" in text or "403" in text or "402" in text:
            return PROVIDER_AUTHENTICATION_FAILED
        return PROVIDER_UNAVAILABLE

    if error_category in (json_reliability.EMPTY_RESPONSE, json_reliability.INVALID_JSON_SYNTAX):
        return JSON_PARSE_FAILED

    if error_category in (
        json_reliability.SCHEMA_VALIDATION_FAILURE,
        json_reliability.SEMANTIC_VALIDATION_FAILURE,
    ):
        return SCHEMA_INVALID

    return UNKNOWN
