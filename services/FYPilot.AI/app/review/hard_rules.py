"""
Trivial deterministic normalization ONLY — score/confidence clamping, missing
-default fill, list dedupe/cap, whitespace trimming. Content-quality problems
(empty/generic replies, unsupported claims, contradictions) are explicitly
NOT handled here — they must pass through the semantic Reviewer, the
deterministic ReviewDecisionEngine, and the Rewrite Agent instead.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class HardRuleSpec:
    field: str
    kind: Literal["clamp_int", "dedupe_cap_list", "trim_text", "fill_default"]
    params: dict[str, Any] = field(default_factory=dict)


def _clamp_int(value: Any, *, lo: int, hi: int, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default

    return max(lo, min(number, hi))


def _dedupe_cap_list(value: Any, *, max_items: int) -> list[str]:
    if not value:
        return []

    items = value if isinstance(value, list) else [value]

    seen: set[str] = set()
    result: list[str] = []

    for item in items:
        text = str(item).strip()
        if not text:
            continue

        key = text.lower()
        if key in seen:
            continue

        seen.add(key)
        result.append(text)

        if len(result) >= max_items:
            break

    return result


def _trim_text(value: Any, *, max_length: int | None = None) -> str:
    text = str(value).strip() if value is not None else ""
    return text[:max_length] if max_length else text


def apply_hard_rules(
    candidate: dict[str, Any],
    specs: list[HardRuleSpec],
) -> tuple[dict[str, Any], list[str]]:
    """
    Applies each spec in order. Returns (normalized_candidate, violations) —
    violations is a list of short, human-readable notes about what was
    auto-corrected (for audit only; auto-correction here never blocks or
    triggers a rewrite by itself).
    """
    result = dict(candidate)
    violations: list[str] = []

    for spec in specs:
        if spec.field not in result and spec.kind != "fill_default":
            continue

        original = result.get(spec.field)

        if spec.kind == "clamp_int":
            new_value = _clamp_int(
                original,
                lo=spec.params.get("lo", 0),
                hi=spec.params.get("hi", 100),
                default=spec.params.get("default", spec.params.get("lo", 0)),
            )
        elif spec.kind == "dedupe_cap_list":
            new_value = _dedupe_cap_list(
                original,
                max_items=spec.params.get("max_items", 4),
            )
        elif spec.kind == "trim_text":
            new_value = _trim_text(
                original,
                max_length=spec.params.get("max_length"),
            )
        elif spec.kind == "fill_default":
            if original not in (None, "", []):
                continue
            new_value = spec.params.get("default")
        else:
            continue

        if new_value != original:
            violations.append(
                f"{spec.field}: normalized ({spec.kind})."
            )

        result[spec.field] = new_value

    return result, violations
