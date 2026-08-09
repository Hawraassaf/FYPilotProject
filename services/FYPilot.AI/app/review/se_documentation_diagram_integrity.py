"""
Deterministic FYPilot diagram INTEGRITY checks for SE Documentation.

Deliberately NOT a claim of Mermaid renderer/browser certification -- see
the quality-recomputation task that introduced this module. Before this
module existed, the `diagramValidity` quality criterion meant only "the
diagram text passed mermaid_utils.validate_mermaid" (header present,
balanced brackets, no empty/overlong labels) -- a diagram could pass that
check while still being semantically/structurally wrong (e.g. the live
defect: two identical `participant ASPNETCoreRazorPages as ...`
declarations plus a meaningless `ASPNETCoreRazorPages -> ASPNETCoreRazorPages`
self-call both passed basic text validation).

This module adds a small set of ADDITIONAL, still fully deterministic
structural facts on top of that existing text validation (reused, never
duplicated): duplicate sequence-participant declarations, an undeclared
message endpoint, a self-call, a non-empty required diagram, and an ER
relationship whose endpoint entity does not actually exist in this
document's own databaseEntities. It does not attempt semantic reasoning
about whether the diagram's CONTENT is the right workflow -- that judgment
is select_primary_use_case's job at generation time (see
se_documentation_orchestrator.py), never re-decided here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.agents.se_documentation.mermaid_utils import validate_mermaid

Candidate = Dict[str, Any]

_ARROWS: Tuple[str, ...] = ("-->>", "->>", "-->", "->")


def _participant_ids(diagram: str) -> List[str]:
    ids: List[str] = []
    for line in diagram.splitlines():
        stripped = line.strip()
        if stripped.startswith("participant ") or stripped.startswith("actor "):
            parts = stripped.split()
            if len(parts) >= 2:
                ids.append(parts[1])
    return ids


def _message_endpoints(diagram: str) -> List[Tuple[str, str, str]]:
    """Returns (left_id, right_id, raw_line) for every message arrow line.
    Checked longest-arrow-first (`-->>` before `->>`) so a `-->>` line is
    never mis-split on the `->>` substring it also contains."""
    out: List[Tuple[str, str, str]] = []
    for line in diagram.splitlines():
        stripped = line.strip()
        for arrow in _ARROWS:
            if arrow in stripped:
                left, right = stripped.split(arrow, 1)
                left_id = left.strip()
                right_id = right.split(":", 1)[0].strip()
                if left_id and right_id:
                    out.append((left_id, right_id, stripped))
                break
    return out


def diagram_integrity_report(candidate: Candidate) -> Dict[str, Any]:
    """
    Returns {"ok": bool, "issues": [str, ...]} describing this candidate's
    CURRENT mermaidERD/mermaidClassDiagram/activityDiagram/sequenceDiagram
    fields -- always call this AFTER any deterministic diagram rebuild
    (rebuild_mermaid_erd/rebuild_mermaid_class_diagram/
    rebuild_mermaid_activity_diagram/rebuild_mermaid_sequence_diagram) so
    the report describes the FINAL diagrams, never stale pre-rebuild ones.
    """
    issues: List[str] = []

    entities = [e for e in (candidate.get("databaseEntities") or []) if isinstance(e, dict)]
    entity_names = {e.get("name") for e in entities if e.get("name")}
    relationships = [r for r in (candidate.get("entityRelationships") or []) if isinstance(r, dict)]
    use_cases = [uc for uc in (candidate.get("useCases") or []) if isinstance(uc, dict)]

    erd = str(candidate.get("mermaidERD") or "")
    class_diagram = str(candidate.get("mermaidClassDiagram") or "")
    activity = str(candidate.get("activityDiagram") or "")
    sequence = str(candidate.get("sequenceDiagram") or "")

    if entities:
        if not erd.strip():
            issues.append("mermaidERD is empty despite databaseEntities being present.")
        else:
            ok, sub_issues = validate_mermaid(erd, expected_header="erDiagram")
            if not ok:
                issues.extend(f"ERD: {i}" for i in sub_issues)

        if not class_diagram.strip():
            issues.append("mermaidClassDiagram is empty despite databaseEntities being present.")
        else:
            ok, sub_issues = validate_mermaid(class_diagram, expected_header="classDiagram")
            if not ok:
                issues.extend(f"Class diagram: {i}" for i in sub_issues)

    for rel in relationships:
        from_entity = rel.get("fromEntity")
        to_entity = rel.get("toEntity")
        if from_entity not in entity_names or to_entity not in entity_names:
            issues.append(
                f"entityRelationships references a relationship endpoint that does not exist "
                f"in databaseEntities: {from_entity!r} -> {to_entity!r} (fabricated/invalid relationship)."
            )

    if not activity.strip():
        issues.append("activityDiagram is empty.")
    else:
        ok, sub_issues = validate_mermaid(activity, expected_header="flowchart")
        if not ok:
            issues.extend(f"Activity diagram: {i}" for i in sub_issues)
        # A real primary-workflow selection always yields at least one step
        # node beyond the flowchart header + start node -- fewer than 3
        # non-empty lines (header, start node, one step) means no workflow
        # was actually represented.
        non_empty_lines = [ln for ln in activity.splitlines() if ln.strip()]
        if use_cases and len(non_empty_lines) < 3:
            issues.append(
                "activityDiagram has no represented workflow steps despite use cases being "
                "present -- no primary workflow appears to have been selected."
            )

    if not sequence.strip():
        issues.append("sequenceDiagram is empty.")
    else:
        ok, sub_issues = validate_mermaid(sequence, expected_header="sequenceDiagram")
        if not ok:
            issues.extend(f"Sequence diagram: {i}" for i in sub_issues)

        declared_ids = _participant_ids(sequence)
        duplicate_ids = sorted({pid for pid in declared_ids if declared_ids.count(pid) > 1})
        if duplicate_ids:
            issues.append(
                f"Sequence diagram declares the same participant id more than once: "
                f"{', '.join(duplicate_ids)}."
            )

        declared_id_set = set(declared_ids)
        for left_id, right_id, raw_line in _message_endpoints(sequence):
            if left_id not in declared_id_set:
                issues.append(f"Sequence diagram message references an undeclared participant '{left_id}': {raw_line}")
            if right_id not in declared_id_set:
                issues.append(f"Sequence diagram message references an undeclared participant '{right_id}': {raw_line}")
            if left_id == right_id:
                issues.append(f"Sequence diagram contains a meaningless self-call: {raw_line}")

    return {"ok": len(issues) == 0, "issues": issues}
