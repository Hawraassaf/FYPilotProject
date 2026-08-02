"""
Per-agent stage-key lists for the centralized AI Agent Loading System.

Each list is built from that agent's OWN real, confirmed workflow steps
(read from its actual router/agent source), never a synthetic evenly-spaced
placeholder -- this is what keeps "no fake progress" true even at the
definition level. Stage keys and labels here are the client-facing circular
loader's row (see wwwroot/js/ai-agent-progress.js); StageState per key comes
from AgentJobManager.snapshot()/ProgressReporter at runtime, never from
this list directly.

Only agents that have been wired end-to-end (worker function + registry
entry, see app/routers/ai_jobs.py) have a plan here -- added one at a time
as each agent's page is wired (Phase 7 for Idea Comparison, Phase 8 for the
rest), not guessed in advance.
"""

from __future__ import annotations

# (stage_key, label) -- label is display text only, never sent as the
# authoritative stage list to a page that hasn't been wired yet.
StagePlan = list[tuple[str, str]]

# The job-based worker's own flow (app/jobs/workers/idea_comparison_worker.py)
# -- distinct from the synchronous /compare-generated-ideas endpoint, which
# is unchanged and has no rewrite step. "generate" covers the writer call
# plus firewall/soft-schema checks on its output; "review" is the first
# semantic review; "rewrite" only runs (and only ever once) if that review
# rejected the output and enough of the job's 90s global deadline remains;
# "final_checks" is the post-rewrite validation bundle (schema, idea-id/
# score-consistency, repetition, firewall, one final semantic review) --
# only reached if a rewrite happened; "save" is .NET's finalizer stage,
# never touched by Python.
IDEA_COMPARISON_PLAN: StagePlan = [
    ("prepare_context", "Preparing comparison context"),
    ("generate", "Generating recommendation"),
    ("review", "Reviewing quality"),
    ("rewrite", "Refining based on feedback"),
    ("final_checks", "Final quality checks"),
    ("save", "Saving recommendation"),
]

AGENT_STAGE_PLANS: dict[str, StagePlan] = {
    "IdeaComparisonAgent": IDEA_COMPARISON_PLAN,
}


def stage_keys_for(agent_name: str) -> list[str]:
    plan = AGENT_STAGE_PLANS.get(agent_name)
    if plan is None:
        raise KeyError(f"No StagePlan registered for agent '{agent_name}'")
    return [stage_key for stage_key, _label in plan]
