"""
Secret/credential detection.

Scanned over ALL outbound fields, trusted and untrusted alike — a secret can
land inside otherwise-trusted database content (e.g. a student accidentally
pastes a real API key into a project idea's description field). This is the
one firewall scan that is never restricted to untrusted-only content.

Findings never include the raw matched secret — only a redacted preview
(prefix + length) is placed in `detail`.
"""

import re

from app.llm_firewall.models import FirewallFinding

# (rule name, compiled pattern, severity, action)
_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    (
        "openai_style_api_key",
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        "critical",
        "block",
    ),
    (
        "groq_api_key",
        re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
        "critical",
        "block",
    ),
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        "critical",
        "block",
    ),
    (
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "critical",
        "block",
    ),
    (
        "aws_access_key_id",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
        "critical",
        "block",
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "critical",
        "block",
    ),
    (
        "postgres_connection_string",
        re.compile(r"\bpostgres(?:ql)?://[^:\s]+:[^@\s]+@[^\s\"']+", re.IGNORECASE),
        "critical",
        "block",
    ),
    (
        "mongodb_connection_string",
        re.compile(r"\bmongodb(?:\+srv)?://[^:\s]+:[^@\s]+@[^\s\"']+", re.IGNORECASE),
        "critical",
        "block",
    ),
    (
        "sqlserver_connection_string",
        re.compile(
            r"(?:Server|Data Source)\s*=.+?;.*?Password\s*=\s*[^;\"'\s]+",
            re.IGNORECASE,
        ),
        "critical",
        "block",
    ),
    (
        "generic_password_assignment",
        re.compile(
            r"\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b"
            r"\s*[:=]\s*[\"']?[^\s\"']{6,}",
            re.IGNORECASE,
        ),
        "high",
        "redact",
    ),
]


def _redacted_preview(matched: str) -> str:
    visible = matched[:4]
    return f"{visible}***REDACTED*** (length {len(matched)})"


def scan(fields: dict[str, str]) -> list[FirewallFinding]:
    """
    fields: field_name -> text. Returns one finding per (rule, field) match,
    never the raw matched secret text.
    """
    findings: list[FirewallFinding] = []

    for field_name, text in fields.items():
        if not text:
            continue

        for rule_name, pattern, severity, action in _PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            findings.append(
                FirewallFinding(
                    rule=rule_name,
                    severity=severity,
                    action=action,
                    detail=f"Possible credential detected ({_redacted_preview(match.group(0))}).",
                    field=field_name,
                )
            )

    return findings
