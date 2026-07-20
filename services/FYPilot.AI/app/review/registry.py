"""
Per-agent configuration for the review pipeline.

Only "FypMentorAgent" is wired for the pilot (see app/routers/fyp_chat.py).
Adding another agent here is configuration, not new architecture -- but it
does not take effect on its own; that agent's router must also be switched
to call ReviewPipeline. Until that happens, an agent NOT listed here (or not
actually wired) gets none of this pipeline's protection.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from app.agents.fyp_mentor_agent import FypMentorAnswer


@dataclass
class AgentReviewConfig:
    schema: type[BaseModel]
    max_rewrites: int = 1
    url_mode: str = "no_urls_allowed"
    allow_unreviewed_output: bool = False
    known_risky_claims: list[str] = field(default_factory=list)
    mandatory_fields: list[str] = field(default_factory=list)
    max_total_seconds: float = 90.0


# Migrated (copied, not moved) from app/agents/answer_review_agent.py's
# risky_claim_replacements. There they were blindly regex-replaced; here they
# are domain knowledge fed into the semantic Reviewer's prompt so the
# Reviewer decides whether the claim is actually present and unsupported,
# and the Rewrite Agent -- not a regex -- corrects it.
_MENTOR_KNOWN_RISKY_CLAIMS = [
    "ASP.NET Core Identity",
    "data is encrypted",
    "database encryption",
    "regular security audits",
    "deployed to production",
    "production-ready",
    "React frontend",
    "Node.js backend",
    "Flask",
    "AWS",
    "Azure",
    "Kubernetes",
]


AGENT_REGISTRY: dict[str, AgentReviewConfig] = {
    "FypMentorAgent": AgentReviewConfig(
        schema=FypMentorAnswer,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_MENTOR_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["reply"],
        max_total_seconds=90.0,
    ),
}


def get_agent_config(agent_name: str) -> AgentReviewConfig:
    try:
        return AGENT_REGISTRY[agent_name]
    except KeyError as exc:
        raise KeyError(
            f"No review pipeline configuration registered for agent '{agent_name}'. "
            "Add an entry to app/review/registry.py before wiring it into ReviewPipeline."
        ) from exc
