"""
URL policy — configurable per agent (see app/review/registry.py). Never trust
a model-generated URL automatically, regardless of mode.

Modes:
- no_urls_allowed: any URL-shaped string in the output is flagged.
- source_metadata_only: URL must appear verbatim in the request's
  allowed_sources (real ProviderChain.sources metadata, never invented).
- approved_domain_allowlist: URL's domain must be in allowed_domains.
- internal_application_links_only: only relative paths or configured
  first-party domains are allowed.
"""

import re
from typing import Any
from urllib.parse import urlparse

from app.llm_firewall.models import FirewallFinding

_URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def check(
    text: str,
    *,
    policy: str,
    allowed_sources: list[dict[str, Any]] | None = None,
    allowed_domains: list[str] | None = None,
) -> list[FirewallFinding]:
    if not text:
        return []

    urls = _URL_PATTERN.findall(text)
    if not urls:
        return []

    allowed_sources = allowed_sources or []
    allowed_domains = allowed_domains or []
    allowed_source_urls = {str(source.get("url", "")).strip() for source in allowed_sources}

    findings: list[FirewallFinding] = []

    for url in urls:
        clean_url = url.rstrip('.,;:!?)]}')

        if policy == "no_urls_allowed":
            findings.append(
                FirewallFinding(
                    rule="url_not_permitted",
                    severity="medium",
                    action="block",
                    detail=f"A URL was returned but this agent's policy is no_urls_allowed ({_domain(clean_url) or 'unknown domain'}).",
                    field="output",
                )
            )

        elif policy == "source_metadata_only":
            if clean_url not in allowed_source_urls:
                findings.append(
                    FirewallFinding(
                        rule="url_not_in_source_metadata",
                        severity="high",
                        action="block",
                        detail=f"URL is not present in the retrieved source metadata for this request ({_domain(clean_url) or 'unknown domain'}).",
                        field="output",
                    )
                )

        elif policy == "approved_domain_allowlist":
            if _domain(clean_url) not in allowed_domains:
                findings.append(
                    FirewallFinding(
                        rule="url_domain_not_allowlisted",
                        severity="high",
                        action="block",
                        detail=f"URL domain is not on the approved allowlist ({_domain(clean_url) or 'unknown domain'}).",
                        field="output",
                    )
                )

        elif policy == "internal_application_links_only":
            if not clean_url.startswith("/") and _domain(clean_url) not in allowed_domains:
                findings.append(
                    FirewallFinding(
                        rule="url_not_internal",
                        severity="high",
                        action="block",
                        detail=f"URL is not an internal application link ({_domain(clean_url) or 'unknown domain'}).",
                        field="output",
                    )
                )

    return findings
