"""
Deterministic source-type classification and relevance scoring for
MarketNeedsAgent's retrieved evidence (app/agents/market_needs_agent.py).

Root problem this fixes (confirmed LIVE, not theoretical, via two unmocked
/analyze-market-demand runs 2026-08-13): MarketNeedsAgent._normalize_sources
previously read `item.get("relevanceScore", 65)` -- but raw Brave/Groq
search-result dicts never carry a "relevanceScore" key (see
llm_provider.py's `_deduplicate_sources`, which only ever produces
title/url/snippet), so EVERY real source was scored exactly 65/100,
verbatim, regardless of actual content. That fabricated-precision number is
rendered directly to students (MarketDemand.cshtml: "@source.RelevanceScore
/100 relevance") and persisted (MarketDemandSource.RelevanceScore), so it
looked like a real computed signal while never being one.

The same live runs also showed `_source_type`'s 4-bucket classifier
(Official/University/Institution/External) and `_is_recognized_domain`'s
~19-domain allowlist marking genuinely credible sources -- arxiv.org,
researchgate.net, frontiersin.org (peer-reviewed), grandviewresearch.com-
style market-research report publishers -- as unverified "External", the
same class of authority-classification gap already confirmed for Mentor
Chat's mentor_search_planner.py (see that module's docstring). This module
is a SEPARATE, Market-Demand-specific fix, not a shared one: Mentor Chat's
scorer is tuned for its own broader intent taxonomy (academic_research,
security, deployment, ...) and is left untouched here per this task's scope
(standalone Market Demand only).

No LLM call, no network call -- pure deterministic classification/scoring,
fully unit-testable without mocking a provider.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

SourceType = Literal[
    "government_or_international_org",
    "peer_reviewed_research",
    "university",
    "market_research_firm",
    "industry_association",
    "financial_business_press",
    "technology_press",
    "commercial_marketplace",
    "blog_or_seo_content",
    "unknown",
]

# =============================================================================
# Source-type classification -- pattern-based, not a single flat allowlist.
# Each category has both an explicit marker list (the well-known publishers)
# and, where the category has a real naming convention, a regex pattern so a
# publisher not individually named still classifies correctly (this is what
# a giant hand-maintained allowlist alone cannot do -- see module docstring).
# =============================================================================

_GOV_OR_INTL_ORG_MARKERS = (
    ".gov", ".gov.", "un.org", "who.int", "worldbank.org", "imf.org",
    "oecd.org", "weforum.org", "itu.int", "ilo.org", "unesco.org",
    "europa.eu", "nist.gov", "iso.org", "w3.org", "ietf.org",
)

_PEER_REVIEWED_MARKERS = (
    "arxiv.org", "researchgate.net", "ncbi.nlm.nih.gov", "pubmed",
    "ieee.org", "ieeexplore", "acm.org", "dl.acm.org", "springer.com",
    "link.springer.com", "sciencedirect.com", "wiley.com",
    "onlinelibrary.wiley.com", "jstor.org", "tandfonline.com", "mdpi.com",
    "frontiersin.org", "semanticscholar.org", "scholar.google", "nature.com",
    "biorxiv.org", "medrxiv.org", "ssrn.com", "plos.org",
)

_UNIVERSITY_MARKERS = (".edu", ".ac.uk", ".ac.", "university")

# Explicit list of well-known market-research report publishers PLUS a
# generic naming-pattern fallback (domain contains "market" immediately
# followed by "research"/"insight(s)"/"report(s)"/"intel(ligence)") -- this
# is the deliberate alternative to a giant flat allowlist the task called
# for: a market-research publisher not individually named below (there will
# always be one) still classifies correctly because the category has a real,
# checkable naming convention, unlike e.g. "financial press" which does not.
_MARKET_RESEARCH_FIRM_MARKERS = (
    "grandviewresearch.com", "statista.com", "gartner.com", "mckinsey.com",
    "marketsandmarkets.com", "alliedmarketresearch.com",
    "precedenceresearch.com", "fortunebusinessinsights.com",
    "researchandmarkets.com", "mordorintelligence.com",
    "futuremarketinsights.com", "imarcgroup.com",
    "verifiedmarketresearch.com", "polarismarketresearch.com",
    "marketresearchfuture.com", "coherentmarketinsights.com", "ibisworld.com",
    "technavio.com", "reportlinker.com", "globenewswire.com",
)
_MARKET_RESEARCH_FIRM_PATTERN = re.compile(
    r"market.{0,2}(research|insights?|reports?|intel(ligence)?)", re.IGNORECASE,
)

_INDUSTRY_ASSOCIATION_PATTERN = re.compile(
    r"(association|federation|alliance|consortium)\.(org|com)$", re.IGNORECASE,
)

_FINANCIAL_BUSINESS_PRESS_MARKERS = (
    "techcrunch.com", "reuters.com", "bloomberg.com", "forbes.com",
    "wsj.com", "ft.com", "economist.com", "businessinsider.com", "cnbc.com",
    "hbr.org",
)

_TECHNOLOGY_PRESS_MARKERS = (
    "healthcareitnews.com", "techradar.com", "zdnet.com", "theverge.com",
    "arstechnica.com", "wired.com", "venturebeat.com", "mobihealthnews.com",
    "healthcareitnews.com", "fiercehealthcare.com", "modernhealthcare.com",
)

# Same category the Mentor-Chat-side scorer already confirmed live for FYP
# source-code marketplaces -- kept here as its own small, explicit list
# (never used to justify authority for a market-demand claim) plus a text
# pattern, matching mentor_search_planner.py's precedent for this category.
_COMMERCIAL_MARKETPLACE_MARKERS = (
    "kashipara.com", "techprofree.com", "1000projects.org",
    "nevonprojects.com", "itsourcecode.com", "sourcecodester.com",
    "freeprojectz.com", "projectsgeek.com",
)
_MARKETPLACE_TEXT_PATTERNS = re.compile(
    r"with source code|download project|project download|free download|"
    r"final year project topics",
    re.IGNORECASE,
)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def classify_source_type(domain: str, title: str, snippet: str) -> SourceType:
    haystack = f"{title} {snippet}"

    if any(marker in domain for marker in _COMMERCIAL_MARKETPLACE_MARKERS):
        return "commercial_marketplace"
    if _MARKETPLACE_TEXT_PATTERNS.search(haystack):
        return "commercial_marketplace"

    if any(marker in domain for marker in _GOV_OR_INTL_ORG_MARKERS):
        return "government_or_international_org"

    if any(marker in domain for marker in _PEER_REVIEWED_MARKERS):
        return "peer_reviewed_research"

    if any(marker in domain for marker in _UNIVERSITY_MARKERS):
        return "university"

    if any(marker in domain for marker in _MARKET_RESEARCH_FIRM_MARKERS):
        return "market_research_firm"
    if _MARKET_RESEARCH_FIRM_PATTERN.search(domain):
        return "market_research_firm"

    if _INDUSTRY_ASSOCIATION_PATTERN.search(domain):
        return "industry_association"

    if any(marker in domain for marker in _FINANCIAL_BUSINESS_PRESS_MARKERS):
        return "financial_business_press"

    if any(marker in domain for marker in _TECHNOLOGY_PRESS_MARKERS):
        return "technology_press"

    if "blog" in domain or "medium.com" in domain:
        return "blog_or_seo_content"

    return "unknown"


# Authority is intent-agnostic here (Market Demand only has one claim type:
# current market/problem evidence) -- unlike Mentor Chat's per-intent
# preferred_source_types, this agent does not need to vary authority by
# search category, so a single fixed table is the correct scope, not a
# missing feature.
_AUTHORITY_BY_TYPE: dict[SourceType, float] = {
    "government_or_international_org": 0.95,
    "peer_reviewed_research": 0.92,
    "university": 0.85,
    "market_research_firm": 0.80,
    "industry_association": 0.75,
    "financial_business_press": 0.70,
    "technology_press": 0.60,
    "unknown": 0.45,
    "blog_or_seo_content": 0.35,
    "commercial_marketplace": 0.10,
}

# A source is "Recognized" (MarketDemand.cshtml's IsVerified badge) when its
# TYPE is inherently high-authority for a market/problem-evidence claim --
# replaces the old ~19-domain flat allowlist, which could never recognize a
# publisher (however credible) that simply wasn't individually named.
_HIGH_AUTHORITY_TYPES = frozenset({
    "government_or_international_org", "peer_reviewed_research", "university",
    "market_research_firm", "industry_association",
})


def is_high_authority(source_type: SourceType) -> bool:
    return source_type in _HIGH_AUTHORITY_TYPES


# =============================================================================
# Relevance scoring -- concept overlap + authority, not raw literal word
# overlap alone.
#
# Root problem this fixes (confirmed live): a long, conversational project
# description naturally shares few exact tokens with a terse report title
# ("Arabic Medical Symptom Triage Assistant" vs. "AI Symptom Checker Market
# Size, Share & Trends") even when the source is obviously on-topic. Pure
# word-overlap scoring punishes this. Blending in a bounded authority
# contribution means a credible, topically-adjacent source is not driven to
# a near-zero score purely by exact wording mismatch, while a source with
# genuinely strong term overlap still scores highest regardless of type.
# =============================================================================

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "to", "of", "for", "in",
    "on", "and", "or", "what", "which", "how", "do", "does", "should", "i",
    "my", "our", "we", "you", "me", "this", "that", "can", "find", "some",
    "any", "with", "will", "be", "as", "at", "by", "from", "it", "its",
    "project", "students", "student", "final", "year", "market", "demand",
})


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def project_concept_terms(
    *,
    project_title: str,
    domain: str,
    technologies: str,
    problem_statement: str,
    target_users: str,
) -> set[str]:
    """
    The set of significant concept terms a source should be judged against --
    project title/domain/technologies/target users in full, plus only the
    first ~40 words of problem_statement (a long problem narrative otherwise
    dilutes the term set with incidental words unrelated to the core
    concept).
    """
    trimmed_problem = " ".join(problem_statement.split()[:40])

    return (
        _tokenize(project_title)
        | _tokenize(domain)
        | _tokenize(technologies)
        | _tokenize(target_users)
        | _tokenize(trimmed_problem)
    )


def _partial_match_count(project_terms: set[str], haystack_terms: set[str]) -> int:
    """
    Credits a project term that appears as a substring of a haystack term or
    vice versa (handles plain pluralization/stemming -- "checker" vs
    "checkers", "triage" vs "triaging") without a full stemming library.
    Counted separately from, and never double-counted with, exact matches.
    """
    exact = project_terms & haystack_terms
    remaining_project = project_terms - exact
    remaining_haystack = haystack_terms - exact

    count = 0
    for term in remaining_project:
        if any(
            (term in other or other in term) and abs(len(term) - len(other)) <= 3
            for other in remaining_haystack
        ):
            count += 1

    return count


# A saturating count of DISTINCT matched concepts, reached at this many
# matches, is treated as full word-overlap credit -- deliberately NOT a
# ratio against the full project_terms set size. Root problem this avoids
# (confirmed live): a verbose problem statement can easily produce 30+
# project terms, so even a source matching 4-5 genuinely distinctive
# concepts ("symptom", "arabic", "triage", "digital health") would score
# under 0.15 on a ratio basis purely because the project description was
# long -- punishing the STUDENT's writing style, not the source's actual
# relevance. 5 distinct matched concepts is a strong relevance signal
# regardless of how many total terms the project description happened to
# tokenize into.
_FULL_CREDIT_MATCH_COUNT = 5


def compute_relevance_score(
    project_terms: set[str],
    source_type: SourceType,
    title: str,
    snippet: str,
) -> int:
    """
    0-100. Two bounded components:
    - up to 70 points from concept-term overlap (exact + partial credit,
      saturating -- see _FULL_CREDIT_MATCH_COUNT) between the project and
      this source's title+snippet.
    - up to 30 points from the source type's fixed authority weight, so a
      credible source is never driven to near-zero purely by wording
      mismatch (see module docstring), while a topically strong match on a
      low-authority source still scores well via the first component.

    Never returns a constant value regardless of input -- the defect this
    module replaces (see module docstring).
    """
    if not project_terms:
        return 50

    haystack_terms = _tokenize(f"{title} {snippet}")
    exact_overlap = len(project_terms & haystack_terms)
    partial_overlap = _partial_match_count(project_terms, haystack_terms)

    match_credit = exact_overlap + 0.5 * partial_overlap
    word_overlap_points = round(
        min(1.0, match_credit / _FULL_CREDIT_MATCH_COUNT) * 70
    )

    authority_points = round(_AUTHORITY_BY_TYPE.get(source_type, 0.45) * 30)

    return max(0, min(100, word_overlap_points + authority_points))
