"""
Word-boundary-aware, alias-normalized text matching.

Replaces every `keyword in lowered_text` substring check that used to live in
roadmap_scheduler.py / project_roadmap_agent.py. Two problems that substring
matching caused:

- False positives on short keywords: "ui" matched inside "required" and
  "build" (both literally contain the letters "ui" back-to-back), "ml"
  could match inside unrelated words.
- False negatives on spacing/spelling variants: "set up" didn't match
  "setup", "PostgreSQL" didn't match "Postgres".

`normalize()` fixes both: punctuation is folded to spaces, known variant
phrases are rewritten to one canonical token, and every keyword lookup after
that only ever tests token-level, `\b`-bounded matches against the
normalized text -- never a raw substring test.
"""

from __future__ import annotations

import re

# Canonical token -> surface variants that should collapse to it. Checked
# longest-phrase-first so e.g. "postgresql" (which itself contains "sql")
# is rewritten before a shorter alias could partially match inside it.
_ALIASES: dict[str, tuple[str, ...]] = {
    "setup": ("set up", "set-up"),
    "postgres": ("postgresql", "postgre sql"),
    "aspnet": ("asp.net core", "asp .net core", "asp net core", "asp.net"),
    "fastapi": ("fast api",),
    "frontend": ("front end", "front-end"),
    "backend": ("back end", "back-end"),
    "database": ("db",),
    "ui": ("user interface",),
    "ux": ("user experience",),
    "api": ("apis",),
    "ml": ("machine learning",),
    "nlp": ("natural language processing",),
    "cv": ("computer vision",),
    "oauth": ("o auth",),
    "crud": ("create read update delete",),
    "cicd": ("ci/cd", "ci cd", "continuous integration"),
    "iot": ("internet of things",),
}

# Longest surface-variant strings first, so multi-word aliases are rewritten
# before any shorter one nested inside them.
_ALIAS_REPLACEMENTS: list[tuple[re.Pattern, str]] = sorted(
    (
        (re.compile(r"\b" + re.escape(variant) + r"\b"), canonical)
        for canonical, variants in _ALIASES.items()
        for variant in variants
    ),
    key=lambda pair: -len(pair[0].pattern),
)

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


# Proper nouns/technology names that end in a plain "s" but are not plural
# -- stripping their trailing "s" would desync a technology name from its
# own alias canonical form (e.g. "postgres" -> "postgre").
_DESTEM_EXCEPTIONS = {"postgres", "js", "nodejs", "aws"}


def _destem(token: str) -> str:
    """Strip a plain trailing plural 's' (e.g. "tests" -> "test",
    "endpoints" -> "endpoint") so a singular keyword phrase still matches
    plural wording, without a `\\b`-defeating partial-word match. Skips
    words ending "ss" (e.g. "business", "access") and known non-plural
    proper nouns (see _DESTEM_EXCEPTIONS) to avoid mangling them."""
    if token in _DESTEM_EXCEPTIONS:
        return token
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalize(text: str) -> str:
    """Lowercase, fold punctuation to spaces, strip simple plurals per
    token, collapse whitespace, then rewrite known variant phrases to their
    canonical token. Idempotent."""
    lowered = (text or "").lower()
    folded = _NON_ALNUM.sub(" ", lowered)
    destemmed = " ".join(_destem(token) for token in folded.split())
    collapsed = _WHITESPACE.sub(" ", destemmed).strip()

    for pattern, canonical in _ALIAS_REPLACEMENTS:
        collapsed = pattern.sub(canonical, collapsed)

    return _WHITESPACE.sub(" ", collapsed).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    """True if `phrase` (any length, possibly multi-word) appears in `text`
    as a token-boundary match after normalization -- never a raw substring
    test. "ui" will not match inside "required" or "build"; "setup" matches
    both "setup" and "set up" in the input."""
    normalized_text = normalize(text)
    normalized_phrase = normalize(phrase)

    if not normalized_phrase:
        return False

    return re.search(r"\b" + re.escape(normalized_phrase) + r"\b", normalized_text) is not None


def contains_any(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    normalized_text = normalize(text)
    return any(
        re.search(r"\b" + re.escape(normalize(phrase)) + r"\b", normalized_text) is not None
        for phrase in phrases
        if phrase
    )


def matched_phrases(text: str, phrases: tuple[str, ...] | list[str]) -> list[str]:
    """Every phrase (in the given order) that matches `text`."""
    normalized_text = normalize(text)
    matches = []

    for phrase in phrases:
        normalized_phrase = normalize(phrase)
        if normalized_phrase and re.search(
            r"\b" + re.escape(normalized_phrase) + r"\b", normalized_text
        ):
            matches.append(phrase)

    return matches


def token_set(text: str) -> set[str]:
    return set(normalize(text).split())
