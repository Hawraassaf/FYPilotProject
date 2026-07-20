"""
LlmFirewall — content security around LLM calls.

Not to be confused with app/security.py, which authenticates the .NET<->Python
HTTP channel via X-Internal-Api-Key. This module has nothing to do with that
boundary; it inspects the actual content sent to and received from an LLM.

Two entry points, used identically for every LLM stage (Writer, Reviewer,
Rewrite) via app/llm_firewall/guard.py's guarded_call:

- inspect_prompt(trusted_parts, untrusted_parts): runs BEFORE the LLM call.
    Secret scan covers trusted_parts + untrusted_parts (a secret can be
    sitting in otherwise-trusted DB data). Injection scan covers
    untrusted_parts only.
- inspect_output(output, untrusted_parts, ...): runs AFTER the LLM call.
    Secret scan + injection-echo check + URL policy, all applied to the
    model's own output.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm_firewall.models import FirewallVerdict
from app.llm_firewall.rules import injection_patterns, secrets, url_policy


def _flatten(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if hasattr(value, "model_dump"):
        value = value.model_dump()

    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


class LlmFirewall:
    def inspect_prompt(
        self,
        trusted_parts: dict[str, str],
        untrusted_parts: dict[str, str],
    ) -> FirewallVerdict:
        findings = []

        secret_scan_fields = {**trusted_parts, **untrusted_parts}
        findings.extend(secrets.scan(secret_scan_fields))

        findings.extend(injection_patterns.scan(untrusted_parts))

        return FirewallVerdict(findings=findings)

    def inspect_output(
        self,
        output: Any,
        untrusted_parts: dict[str, str],
        *,
        url_mode: str = "no_urls_allowed",
        allowed_sources: list[dict[str, Any]] | None = None,
        allowed_domains: list[str] | None = None,
    ) -> FirewallVerdict:
        output_text = _flatten(output)

        findings = []
        findings.extend(secrets.scan({"output": output_text}))
        findings.extend(injection_patterns.scan_echo(output_text, untrusted_parts))
        findings.extend(
            url_policy.check(
                output_text,
                policy=url_mode,
                allowed_sources=allowed_sources,
                allowed_domains=allowed_domains,
            )
        )

        return FirewallVerdict(findings=findings)
