"""
Prompt-injection detection.

Scanned over UNTRUSTED fields only — trusted_system_instructions and
trusted_structural_context are never scanned here, because flagging FYPilot's
own legitimate instructions as "injection" would be a false positive by
definition, not a safety improvement.

Two passes:
- scan(): checks untrusted input fields BEFORE a call (the "input firewall").
- scan_echo(): checks a model's OUTPUT for verbatim/near-verbatim echoes of a
  high-confidence injection phrase that appeared in the untrusted input for
  this same request — a much stronger signal than a generic keyword match,
  since it indicates the untrusted content actually influenced the model.
"""

import re

from app.llm_firewall.models import FirewallFinding

# High-confidence phrases: strong signal of an injection attempt, rarely
# appear in legitimate FYP-related text. Blocked outright.
_HIGH_CONFIDENCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_previous_instructions", re.compile(r"ignore (all )?(the )?previous instructions", re.IGNORECASE)),
    ("disregard_previous_instructions", re.compile(r"disregard (the )?(above|previous) instructions", re.IGNORECASE)),
    ("forget_your_instructions", re.compile(r"forget (your|all|previous) instructions", re.IGNORECASE)),
    ("override_instructions", re.compile(r"override (your|the) (system )?instructions", re.IGNORECASE)),
    ("reveal_system_prompt", re.compile(r"(reveal|print|show|repeat) (your|the) (system )?prompt", re.IGNORECASE)),
    ("new_instructions_marker", re.compile(r"new instructions\s*:", re.IGNORECASE)),
    ("jailbreak_keyword", re.compile(r"\bjailbreak\b", re.IGNORECASE)),
    ("do_anything_now", re.compile(r"\bdo anything now\b|\bDAN mode\b", re.IGNORECASE)),
]

# Weaker/ambiguous phrases: worth noting, but common enough in legitimate
# technical writing that blocking outright would cause false positives.
_LOW_CONFIDENCE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("role_swap_you_are_now", re.compile(r"\byou are now\b", re.IGNORECASE)),
    ("system_role_marker", re.compile(r"(?:^|\n)\s*system\s*:\s*", re.IGNORECASE)),
    ("developer_mode", re.compile(r"\bdeveloper mode\b", re.IGNORECASE)),
    ("act_as_marker", re.compile(r"\bact as (a|an|the)\b", re.IGNORECASE)),
]


def scan(untrusted_fields: dict[str, str]) -> list[FirewallFinding]:
    findings: list[FirewallFinding] = []

    for field_name, text in untrusted_fields.items():
        if not text:
            continue

        for rule_name, pattern in _HIGH_CONFIDENCE_PATTERNS:
            if pattern.search(text):
                findings.append(
                    FirewallFinding(
                        rule=rule_name,
                        severity="high",
                        action="block",
                        detail="High-confidence prompt-injection phrase detected in untrusted input.",
                        field=field_name,
                    )
                )

        for rule_name, pattern in _LOW_CONFIDENCE_PATTERNS:
            if pattern.search(text):
                findings.append(
                    FirewallFinding(
                        rule=rule_name,
                        severity="medium",
                        action="flag",
                        detail="Possible role-swap or instruction-like phrasing detected in untrusted input.",
                        field=field_name,
                    )
                )

    return findings


def scan_echo(output_text: str, untrusted_fields: dict[str, str]) -> list[FirewallFinding]:
    """
    A model output that echoes a high-confidence injection phrase which was
    present in this request's untrusted input is a strong signal the model
    was actually hijacked by that content, not just a keyword coincidence.
    """
    if not output_text:
        return []

    combined_untrusted = "\n".join(untrusted_fields.values())
    findings: list[FirewallFinding] = []

    for rule_name, pattern in _HIGH_CONFIDENCE_PATTERNS:
        if pattern.search(combined_untrusted) and pattern.search(output_text):
            findings.append(
                FirewallFinding(
                    rule=f"{rule_name}_echoed_in_output",
                    severity="critical",
                    action="block",
                    detail="Model output echoed a high-confidence injection phrase found in untrusted input.",
                    field="output",
                )
            )

    return findings
