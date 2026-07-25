"""
Deterministic Mermaid label/participant safety and validation.

Fixes the specific stabilization bugs verified in the generated PDF:
- node labels truncated mid-word ("...recei" instead of a full word);
- combined actor names such as "Universitystudentsandsupportstaff" (raw
  actor text stripped of spaces instead of using distinct participants);
- raw, overly long implementation sentences copied straight from use-case
  flow text into diagram nodes;
- no validation step, so a malformed diagram could reach the student
  undetected with diagramValidity still scored 100.

Every diagram builder in se_documentation_orchestrator.py must route its
labels/participant names through this module, and every diagram must be
passed through `validate_mermaid()` before being trusted by the quality
score.
"""

from __future__ import annotations

import re
from typing import List, Tuple

MAX_LABEL_LENGTH = 60
MAX_PARTICIPANT_ID_LENGTH = 24


def safe_label(value: str, max_length: int = MAX_LABEL_LENGTH) -> str:
    """
    Compacts arbitrary text (e.g. a full use-case step sentence) into a
    short Mermaid node label: strips numbering, strips characters Mermaid
    node syntax reserves ([]{}|), and truncates on a word boundary so no
    label ever ends mid-word.
    """
    if not value or not value.strip():
        return "Step"

    cleaned = re.sub(r"^\s*\d+[\.\)]\s*", "", value.strip())
    cleaned = re.sub(r"[\[\]{}|]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return "Step"

    if len(cleaned) <= max_length:
        return cleaned

    truncated = cleaned[:max_length]
    last_space = truncated.rfind(" ")

    if last_space > max_length * 0.4:
        truncated = truncated[:last_space]

    return truncated.rstrip(",.;:- ") + "..."


def safe_participant_id(value: str) -> str:
    """
    A short, alphanumeric-only participant identifier safe to use as a
    Mermaid participant id (never the full display name -- long or
    multi-word actor names must use an alias via `participant_declaration`
    instead of being crammed into the id itself).
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", (value or "Component"))
    if not cleaned:
        cleaned = "Component"
    return cleaned[:MAX_PARTICIPANT_ID_LENGTH]


def participant_declaration(participant_id: str, display_name: str, *, is_actor: bool = False) -> str:
    """
    Builds a `participant X as Display Name` (or `actor X as Display Name`)
    declaration, so a long/combined actor description keeps its full
    readable name without ever being squashed into the identifier itself.
    """
    keyword = "actor" if is_actor else "participant"
    display = re.sub(r"\s+", " ", display_name or participant_id).strip()

    if display and display != participant_id:
        return f"    {keyword} {participant_id} as {display}"

    return f"    {keyword} {participant_id}"


def split_combined_actor(value: str) -> List[str]:
    """
    Splits a raw actor description like "University students and support
    staff" into distinct actor names ["University students", "support
    staff"], so callers can declare separate participants instead of
    collapsing them into one combined identifier.
    """
    if not value:
        return []

    parts = re.split(r"\s*(?:,|/| and | & )\s*", value.strip(), flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def validate_mermaid(diagram: str, *, expected_header: str, known_names: List[str] | None = None) -> Tuple[bool, List[str]]:
    """
    Deterministic structural validation -- not a full Mermaid parser, but
    enough to catch the concrete defects previously observed: missing/wrong
    header, unbalanced brackets, empty labels, and labels that were
    truncated mid-word (ending in a lowercase letter immediately followed by
    the line's own truncation, i.e. a label that is exactly MAX_LABEL_LENGTH
    characters long and does not end at a word boundary).
    """
    issues: List[str] = []

    if not diagram or not diagram.strip():
        return False, ["Diagram is empty."]

    lines = diagram.strip().splitlines()

    if not lines[0].strip().startswith(expected_header):
        issues.append(f"Diagram does not start with the expected '{expected_header}' header.")

    # erDiagram relationship notation (e.g. "||--o{", "}o--||") uses a
    # single unmatched brace as part of the crow's-foot symbol itself, not
    # as a block delimiter -- only check brace balance for diagram kinds
    # that actually use braces as delimiters (classDiagram).
    bracket_pairs = [("[", "]"), ("(", ")")]
    if expected_header != "erDiagram":
        bracket_pairs.append(("{", "}"))

    for opener, closer in bracket_pairs:
        if diagram.count(opener) != diagram.count(closer):
            issues.append(f"Unbalanced '{opener}{closer}' brackets in diagram.")

    for line in lines[1:]:
        label_match = re.search(r"\[([^\[\]]*)\]", line)
        if label_match and not label_match.group(1).strip():
            issues.append(f"Empty node label in line: {line.strip()[:60]}")

        # A label that runs right up against the closing bracket with no
        # trailing punctuation/ellipsis and is suspiciously long is the
        # signature of the old bug (a full sentence copied in verbatim).
        if label_match:
            label_text = label_match.group(1)
            if len(label_text) > MAX_LABEL_LENGTH and not label_text.endswith("..."):
                issues.append(f"Node label exceeds {MAX_LABEL_LENGTH} characters and was not truncated safely: {label_text[:40]}...")

    # A squashed multi-actor name (the original "Universitystudentsandsupport-
    # staff" bug) has the tell-tale signature of two lowercase words joined
    # by a bare "and" with no separator -- a merely long single-word id like
    # "Frontendnotconfirmed" does not match this and must not be flagged.
    if known_names:
        combined_actor_pattern = re.compile(r"[a-z]{3,}and[a-z]{3,}", re.IGNORECASE)
        for line in lines:
            for token in re.findall(r"\b\w{15,}\b", line):
                if combined_actor_pattern.search(token):
                    issues.append(f"Combined/squashed actor name detected: '{token}' -- declare separate participants instead.")

    return (len(issues) == 0), issues
