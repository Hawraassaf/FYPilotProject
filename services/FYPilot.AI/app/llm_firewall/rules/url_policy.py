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

# IGNORECASE + the www.-prefixed alternative close two real gaps a freeze
# audit found in the original https?://-only, case-sensitive pattern: a
# mixed/upper-case scheme ("HTTPS://evil.example") matched neither this
# detector nor strip_urls()'s backstop, and neither did a scheme-less
# "www.evil.example" mention -- both classes are realistic ways a scraped
# search result's title/snippet can carry a second, unrelated link. Bare
# domains with no scheme and no "www." prefix (e.g. "evil.example/page")
# are deliberately NOT matched here: a general word.tld heuristic risks
# false-positive matches against ordinary prose (project/tech names like
# "Node.js", abbreviations like "e.g.", "Inc."), which would incorrectly
# strip or block legitimate content -- www./explicit-scheme is the
# practical, safe boundary for a deterministic backstop.
_URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s\"'<>)\]]+", re.IGNORECASE)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def strip_urls(text: str) -> str:
    """
    Deterministically remove every http(s):// URL from `text`, collapsing
    the whitespace/punctuation left behind so the surrounding sentence still
    reads naturally (e.g. "see (https://x.com) for more" -> "see for more").
    Handles multiple URLs in one string and URLs immediately followed by
    punctuation (both consumed by `_URL_PATTERN` itself, since neither is
    excluded from its character class).

    A deterministic backstop for any agent whose output must satisfy
    url_mode="no_urls_allowed" or "source_metadata_only" (see check()
    above): those modes block the ENTIRE candidate the instant one
    URL-shaped string appears anywhere in it, so a narrative field that
    ends up carrying a URL -- whether from the Writer ignoring a "never
    write URLs" prompt instruction, or from a retrieved source's own
    snippet embedding a second, unrelated link that got echoed verbatim --
    must never reach that check unsanitized. Prompt wording alone cannot
    guarantee this (a model can still ignore an instruction, and a source's
    own embedded link was never LLM-authored at all), so this must run
    deterministically on every field required to contain no URLs, never
    left to model compliance. Logic mirrors MarketNeedsAgent's own
    `_strip_urls` (app/agents/market_needs_agent.py), which fixed this
    exact failure mode live for Market Demand -- centralized here so a
    second agent needing the same guarantee reuses it instead of
    reimplementing slightly-different URL-cleanup logic.
    """
    if not text:
        return text

    stripped = _URL_PATTERN.sub("", text)
    stripped = re.sub(r"\(\s*\)", "", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped)
    stripped = re.sub(r"\s+([.,;:!?])", r"\1", stripped)
    return stripped.strip()


def strip_urls_from_narrative_fields(value: Any) -> Any:
    """
    Recursively apply strip_urls() to every string value in a candidate
    dict/list structure, EXCEPT values reached through a dict key whose
    name contains "url" (case-insensitive -- matches "url", "sourceUrl",
    "sourceUrls", "source_url", the real field names Market Footprint/
    Market Needs/Mentor Chat's schemas use for their genuinely real,
    deterministically-matched source links).

    Exists specifically to close a gap the Writer-time per-agent narrative
    cleaning (each agent's own _normalize_sources/_clean_text, all of which
    already call strip_urls) never covered: a Reviewer-triggered semantic
    rewrite is merged back in verbatim (see section_scope.py's
    apply_scoped_rewrite) for every agent except SEDocumentationAgent, with
    no re-run of that per-agent narrative cleaning. For an agent whose
    url_mode is "source_metadata_only", the output firewall's check() only
    blocks a URL that does NOT match an already-verified real source --
    it has no concept of which FIELD a URL appears in, so a rewrite that
    echoes a genuinely real, allowed source URL into a narrative field
    (e.g. a "strategicRecommendation" paragraph) passes that check
    unblocked. Only meaningfully needed for that url_mode (every other
    mode either blocks any URL outright, in which case the field it landed
    in already made no difference, or isn't currently used by any agent) --
    call sites gate on that, this function itself is agent/schema-agnostic
    so it never needs updating when a schema's narrative fields change.
    """
    if isinstance(value, str):
        return strip_urls(value)

    if isinstance(value, list):
        return [strip_urls_from_narrative_fields(item) for item in value]

    if isinstance(value, dict):
        return {
            key: (
                item if isinstance(key, str) and "url" in key.lower()
                else strip_urls_from_narrative_fields(item)
            )
            for key, item in value.items()
        }

    return value


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
