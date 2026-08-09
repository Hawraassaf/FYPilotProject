"""
Opt-in, development-only sanitized candidate snapshot for SE Documentation.

Disabled by default everywhere, including this codebase's own tests and CI.
When explicitly enabled (SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR set to a local
directory path), writes a REDACTED copy of the candidate immediately before
final whole-document structural validation -- so a failed live run's
candidate can be replayed locally against schema_validation.validate_detailed
/ diagnose_structural_invariants with ZERO provider calls, instead of being
lost the moment the HTTP response is returned (the exact gap that made an
earlier live failure impossible to diagnose after the fact).

Never served to the student UI or any API response: no router in this
codebase ever reads from this directory, and this module is only ever
called from ReviewPipeline's own internal structural-repair path.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("fypilot-review-pipeline")

# Opt-in switch. Empty/unset (the default in every environment) means this
# module does nothing at all -- see maybe_write_se_documentation_debug_snapshot.
_ENV_VAR = "SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR"

# Large enough for a full SE Documentation candidate (~20-70k estimated
# tokens observed live, well under this in raw JSON bytes), small enough
# that an enabled snapshot directory can never become an unbounded disk-fill
# vector even under repeated failures.
_MAX_SNAPSHOT_BYTES = 2_000_000

# Bound cumulative local growth as well as individual-file size. Reusing the
# same request id already overwrites in place; across distinct requests only
# the newest files up to this limit are retained.
_MAX_SNAPSHOT_FILES = 20

# Every provider API key this codebase reads (see llm_provider.py's
# per-provider `enabled = bool(os.getenv(...))` checks) -- their ACTUAL
# configured values are redacted if they ever appear verbatim in candidate
# content (they never should, but this snapshot must never be the exception
# that proves otherwise).
_SECRET_ENV_VARS = ("GROQ_API_KEY", "DEEPINFRA_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OLLAMA_API_KEY")

# Generic key-shaped-string backstop (e.g. "sk-..." prefixed tokens, or any
# other 32+ char unbroken alphanumeric/underscore/hyphen run) -- defense in
# depth for a secret this snapshot's author never anticipated, at the cost
# of occasionally redacting a long non-secret token (acceptable for a
# development-only diagnostic artifact).
_SECRET_LOOKING_PATTERN = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|[A-Za-z0-9_-]{32,})")

# Redact values carried by explicitly sensitive keys even when the value is
# short and therefore would not match _SECRET_LOOKING_PATTERN.
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"auth[_-]?(?:token|header)|password|passwd|secret|cookie|"
    r"connection[_-]?string)",
    re.IGNORECASE,
)

# Backstop for credentials embedded in ordinary prose rather than placed in
# a dedicated JSON field, for example ``password=example`` or
# ``access_token: abc``.
_INLINE_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"authorization|password|passwd|secret)\b\s*[:=]\s*)([^\s,;]+)"
)

_REDACTED = "[REDACTED]"


def _redact(value: Any, *, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        redacted_dict = {}
        for key, item in value.items():
            if _SENSITIVE_KEY_PATTERN.search(str(key)):
                redacted_dict[key] = _REDACTED
            else:
                redacted_dict[key] = _redact(item, secret_values=secret_values)
        return redacted_dict
    if isinstance(value, list):
        return [_redact(item, secret_values=secret_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secret_values:
            if secret:
                redacted = redacted.replace(secret, _REDACTED)
        redacted = _INLINE_SECRET_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{_REDACTED}", redacted
        )
        return _SECRET_LOOKING_PATTERN.sub(_REDACTED, redacted)
    return value


def _is_local_path(snapshot_dir: str) -> bool:
    """Reject URL/UNC destinations; snapshots are local diagnostics only."""
    return not (
        snapshot_dir.startswith(("\\\\", "//"))
        or "://" in snapshot_dir
    )


def _prune_old_snapshots(snapshot_dir: str) -> None:
    candidates = []
    for entry in os.scandir(snapshot_dir):
        if not entry.name.startswith("se_documentation_candidate_") or not entry.name.endswith(".json"):
            continue
        if not entry.is_file(follow_symlinks=False):
            continue
        candidates.append((entry.stat(follow_symlinks=False).st_mtime, entry.name, entry.path))

    candidates.sort()
    for _mtime, _name, path in candidates[:-_MAX_SNAPSHOT_FILES]:
        os.remove(path)


def maybe_write_se_documentation_debug_snapshot(candidate: dict, *, request_id: str) -> None:
    """
    No-op unless SE_DOCUMENTATION_DEBUG_SNAPSHOT_DIR is set. When enabled,
    writes exactly ONE JSON file per request/job id (re-running the same
    request_id overwrites rather than accumulating), sanitized against
    sensitive keys, inline credentials, known provider API key values and
    generic key-shaped strings. Individual files and cumulative file count
    are bounded. Never raises into the caller -- a snapshot failure must
    never affect the actual review pipeline result or acceptance decision.
    """
    snapshot_dir = os.getenv(_ENV_VAR, "").strip()
    if not snapshot_dir:
        return

    if not _is_local_path(snapshot_dir):
        logger.warning("se_documentation.debug_snapshot_nonlocal_path_rejected")
        return

    try:
        secret_values = tuple(value for value in (os.getenv(name) for name in _SECRET_ENV_VARS) if value)
        sanitized = _redact(candidate, secret_values=secret_values)
        text = json.dumps(sanitized, indent=2, default=str, ensure_ascii=False)

        encoded = text.encode("utf-8")
        if len(encoded) > _MAX_SNAPSHOT_BYTES:
            text = (
                encoded[:_MAX_SNAPSHOT_BYTES].decode("utf-8", errors="ignore")
                + "\n... [truncated -- exceeded debug snapshot size bound]"
            )

        os.makedirs(snapshot_dir, exist_ok=True)
        safe_request_id = re.sub(r"[^A-Za-z0-9_-]", "_", request_id)[:128] or "unknown"
        path = os.path.join(snapshot_dir, f"se_documentation_candidate_{safe_request_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

        _prune_old_snapshots(snapshot_dir)

        logger.info(
            "se_documentation.debug_snapshot_written path=%s bytes=%d", path, len(text.encode("utf-8")),
        )
    except Exception as exc:
        logger.warning("se_documentation.debug_snapshot_write_failed reason=%s", exc)
