"""
Per-agent configuration for the review pipeline.

Only "FypMentorAgent" is wired for the pilot (see app/routers/fyp_chat.py).
Adding another agent here is configuration, not new architecture -- but it
does not take effect on its own; that agent's router must also be switched
to call ReviewPipeline. Until that happens, an agent NOT listed here (or not
actually wired) gets none of this pipeline's protection.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, model_validator

from app.agents.defense_simulator.defense_simulator_orchestrator import (
    DefenseEvaluationCandidate,
    DefenseQuestionBatch,
)
from app.agents.fyp_mentor_agent import FypMentorAnswer
from app.agents.project_dna_agent import ProjectDNAResponse
from app.agents.project_idea_agent import IDEAS_PER_BATCH, ProjectIdea
from app.agents.project_idea_comparison import IdeaComparisonResponse
from app.agents.project_roadmap_agent import ProjectRoadmapResponse
from app.agents.se_documentation.se_documentation_orchestrator import SEDocumentationDto
from app.models.market_footprint_models import MarketFootprintResponse
from app.models.market_needs_models import MarketNeedsResponse


@dataclass
class AgentReviewConfig:
    schema: type[BaseModel]
    max_rewrites: int = 1
    url_mode: str = "no_urls_allowed"
    allow_unreviewed_output: bool = False
    known_risky_claims: list[str] = field(default_factory=list)
    mandatory_fields: list[str] = field(default_factory=list)
    max_total_seconds: float = 90.0
    # Agent-specific rubric text appended to the Reviewer's prompt, in
    # addition to the shared rubric (factual alignment, mandatory content,
    # quality, consistency). Empty string changes nothing for agents that
    # don't set it -- purely additive, see ReviewerAgent.build_prompt.
    extra_rubric: str = ""


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


# Roadmap-specific overclaiming to watch for, parallel to the Mentor claims
# above but scoped to what a roadmap narrative could plausibly overstate.
_ROADMAP_KNOWN_RISKY_CLAIMS = [
    "fully tested",
    "production-ready",
    "deployed to production",
    "guaranteed timeline",
    "zero risk",
    "AWS",
    "Azure",
    "Kubernetes",
    "React",
    "Node.js",
    "Flutter",
    "blockchain",
]

_ROADMAP_EXTRA_RUBRIC = """
ROADMAP-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Phase order: phases must follow a logical dependency order for this stack
  (ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL) -- database/schema
  design and authentication/core setup should not be positioned after phases
  that depend on them.
- Required phases: the roadmap should include, at minimum, some recognizable
  form of (a) requirements/scope definition, (b) core feature implementation,
  and (c) testing/validation. If any is entirely absent from the phase
  names/goals, flag it with category "missing_mandatory_content".
- Durations: flag (category "quality") if a single week's task list clearly
  describes multi-week-scale work with no other supporting week nearby.
- Dependencies: a week's tasks must not assume something is already built
  that a LATER phase is responsible for building. Flag as category
  "contradiction".
- Technology alignment: flag (category "project_alignment") any mention,
  anywhere in the roadmap (tasks, deliverables, riskWarning, checkpoint,
  skillsToLearn, mainGoal, teamStrategy), of a technology outside this
  project's actual stack or its listed required technologies -- especially
  React, Node.js, Flutter, AWS, Azure, Kubernetes, or blockchain.
- Testing before deployment: any week covering testing/QA must have a LOWER
  week number than any week covering final deployment, submission, or
  presentation. Flag as category "contradiction" if testing appears at or
  after deployment/submission.
- Do NOT suggest changing totalWeeks, the number of weeks, or the number of
  teamResponsibilities entries in any week -- those are computed
  deterministically by the platform and are correct by construction. Only
  flag the narrative content (task wording, phase names, risk/checkpoint
  text, goal text).
"""


class RoadmapCandidateSchema(ProjectRoadmapResponse):
    """
    Wraps the agent's own ProjectRoadmapResponse with structural invariants
    that must survive a Rewrite untouched -- week count matching totalWeeks,
    sequential week numbering, and a consistent teamResponsibilities count
    across weeks. A violation here is a schema failure (schema_ok=False),
    handled entirely by the pipeline's existing structural-repair path --
    no changes to pipeline.py's decision logic were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "RoadmapCandidateSchema":
        if len(self.weeks) != self.totalWeeks:
            raise ValueError(
                f"weeks count ({len(self.weeks)}) does not match "
                f"totalWeeks ({self.totalWeeks})"
            )

        expected_numbers = list(range(1, self.totalWeeks + 1))
        actual_numbers = [week.weekNumber for week in self.weeks]

        if actual_numbers != expected_numbers:
            raise ValueError(
                f"week numbers are not sequential 1..{self.totalWeeks}: {actual_numbers}"
            )

        responsibility_counts = {len(week.teamResponsibilities) for week in self.weeks}

        if len(responsibility_counts) > 1:
            raise ValueError(
                f"teamResponsibilities count is inconsistent across weeks: "
                f"{sorted(responsibility_counts)}"
            )

        return self


# SE Documentation-specific overclaiming to watch for -- the same forbidden
# out-of-stack technologies ProjectRoadmapAgent already blocks deterministically
# in its own generation step, fed here as domain knowledge for the semantic
# Reviewer instead.
_SEDOC_KNOWN_RISKY_CLAIMS = [
    "fully secure",
    "production-ready",
    "GDPR compliant",
    "scales to millions of users",
    "zero downtime",
    "fully tested",
    "React",
    "Node.js",
    "Flutter",
    "AWS",
    "Azure",
    "Kubernetes",
    "blockchain",
]

_SEDOC_EXTRA_RUBRIC = """
SE DOCUMENTATION-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT suggest changing documentationQualityScore -- it is computed
  deterministically by the platform, not by the sections you are reviewing.
- Do NOT critique mermaidERD, mermaidClassDiagram, activityDiagram, or
  sequenceDiagram content or syntax -- these four fields are generated
  deterministically from databaseEntities/entityRelationships, not written by
  the LLM sections under review.
- Referential integrity: every id referenced in useCases.relatedRequirements,
  edgeCases.relatedRequirement, systemModules.relatedRequirements, or
  testingPlan.relatedRequirements must correspond to a real functional or
  non-functional requirement id actually present in this candidate. Flag a
  reference to a requirement id that does not exist as category
  "contradiction".
- Technology alignment: flag (category "project_alignment") any systemModule,
  apiIntegrationPoint, or architecture description that assumes a technology
  outside this project's actual stack (ASP.NET Core Razor Pages, Python
  FastAPI, PostgreSQL, Bootstrap, Ollama) unless the project's own
  requiredTechnologies explicitly lists it.
- Completeness: flag (category "missing_mandatory_content") if
  functionalRequirements, useCases, systemModules, databaseEntities, or
  testingPlan is empty.
- Consistency: flag (category "contradiction") if the architecture
  description conflicts with the project's actual required technologies.
"""


class SEDocumentationCandidateSchema(SEDocumentationDto):
    """
    Wraps the agent's own SEDocumentationDto with referential-integrity and
    uniqueness invariants that must survive a Rewrite untouched -- ids unique
    within each list, and every relatedRequirements/relatedRequirement
    reference pointing at an FR/NFR id that actually exists in this
    candidate. A violation here is a schema failure (schema_ok=False),
    handled entirely by the pipeline's existing structural-repair path -- no
    changes to pipeline.py's decision logic were needed for this (same
    pattern as RoadmapCandidateSchema above).
    """

    @staticmethod
    def _check_unique_ids(items: list, field_name: str) -> None:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{field_name} ids are not unique: {ids}")

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "SEDocumentationCandidateSchema":
        self._check_unique_ids(self.functionalRequirements, "functionalRequirements")
        self._check_unique_ids(self.nonFunctionalRequirements, "nonFunctionalRequirements")
        self._check_unique_ids(self.useCases, "useCases")
        self._check_unique_ids(self.edgeCases, "edgeCases")
        self._check_unique_ids(self.systemModules, "systemModules")
        self._check_unique_ids(self.testingPlan, "testingPlan")

        entity_names = [entity.name for entity in self.databaseEntities]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError(f"databaseEntities names are not unique: {entity_names}")

        requirement_ids = {req.id for req in self.functionalRequirements} | {
            req.id for req in self.nonFunctionalRequirements
        }

        for use_case in self.useCases:
            for req_id in use_case.relatedRequirements:
                if req_id not in requirement_ids:
                    raise ValueError(
                        f"useCase '{use_case.id}' references unknown requirement '{req_id}'"
                    )

        for edge_case in self.edgeCases:
            if edge_case.relatedRequirement and edge_case.relatedRequirement not in requirement_ids:
                raise ValueError(
                    f"edgeCase '{edge_case.id}' references unknown requirement "
                    f"'{edge_case.relatedRequirement}'"
                )

        for module in self.systemModules:
            for req_id in module.relatedRequirements:
                if req_id not in requirement_ids:
                    raise ValueError(
                        f"systemModule '{module.id}' references unknown requirement '{req_id}'"
                    )

        for test in self.testingPlan:
            for req_id in test.relatedRequirements:
                if req_id not in requirement_ids:
                    raise ValueError(
                        f"testCase '{test.id}' references unknown requirement '{req_id}'"
                    )

        return self


# Idea Generation-specific overclaiming to watch for. Blocked technologies
# are already deterministically scrubbed inside ProjectIdeaAgent itself
# (_clean_text/_sanitize_technologies), so this list is a defense-in-depth
# backstop in case a Rewrite reintroduces one, plus unsupported-evidence
# phrasing that only a semantic Reviewer can judge.
_IDEA_GENERATION_KNOWN_RISKY_CLAIMS = [
    "proven to increase",
    "guaranteed to succeed",
    "government adopted",
    "millions of users",
    "clinically proven",
    "peer-reviewed study shows",
    "React",
    "Node.js",
    "Vue",
    "Angular",
    "Flutter",
    "Kafka",
    "Azure",
    "AWS",
    "GCP",
    "Kubernetes",
    "blockchain",
    "Web3",
    "Solidity",
]

_IDEA_GENERATION_EXTRA_RUBRIC = """
IDEA GENERATION-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT suggest changing innovationScore, feasibilityScore,
  marketDemandScore, expectedDurationWeeks, or difficultyLevel -- these are
  computed deterministically by the platform, never written by the LLM.
- Unsupported claims: flag (category "unsupported_claim") any specific
  statistic, named organization, named report, or citation that is not
  clearly general domain knowledge. This agent's own live web-search step
  gathers real evidence separately -- idea fields themselves must never
  contain invented statistics, organizations, reports, or URLs.
- Technology alignment: flag (category "project_alignment") any mention of
  React, Node.js, Vue, Angular, Flutter, Dart, Kafka, Azure, AWS, GCP,
  Kubernetes, blockchain, Web3, or Solidity anywhere in the idea fields.
- Distinctiveness: flag (category "quality") if two or more of the 4 ideas
  in this batch describe substantially the same concept.
- Domain adherence: flag (category "project_alignment") any idea whose
  domain does not match the student's preferred domain from the trusted
  project context, without a clear justification in the idea text.
"""


class IdeaGenerationCandidate(BaseModel):
    ideas: list[ProjectIdea]


class IdeaGenerationCandidateSchema(IdeaGenerationCandidate):
    """
    Wraps the agent's own list-of-4-ProjectIdea output with the structural
    invariants that must survive a Rewrite untouched -- exactly
    IDEAS_PER_BATCH ideas, and no two ideas sharing the same title. A
    violation here is a schema failure (schema_ok=False), handled entirely
    by the pipeline's existing structural-repair path -- no changes to
    pipeline.py's decision logic were needed for this (same pattern as
    RoadmapCandidateSchema/SEDocumentationCandidateSchema above).
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "IdeaGenerationCandidateSchema":
        if len(self.ideas) != IDEAS_PER_BATCH:
            raise ValueError(
                f"ideas count ({len(self.ideas)}) does not equal the required {IDEAS_PER_BATCH}"
            )

        titles = [idea.title.strip().lower() for idea in self.ideas]

        if len(titles) != len(set(titles)):
            raise ValueError(f"duplicate idea titles found in this batch: {titles}")

        return self


# Project DNA / Idea Comparison-specific overclaiming to watch for --
# blocked technologies are already deterministically scrubbed inside both
# agents' own _clean_text, so this is a defense-in-depth backstop.
_DNA_KNOWN_RISKY_CLAIMS = [
    "guaranteed",
    "proven to succeed",
    "zero risk",
    "React",
    "Node.js",
    "Vue",
    "Angular",
    "Flutter",
    "Kafka",
    "Azure",
    "AWS",
    "GCP",
    "Kubernetes",
    "blockchain",
    "Web3",
    "Solidity",
]

_DNA_EXTRA_RUBRIC = """
PROJECT DNA-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- requiredSkillsAnalysis status must not contradict the student's actual
  skillRatings in the trusted project context -- a skill rated 3/5 or higher
  must never be marked "Missing". Flag as category "contradiction".
- Do not exaggerate risk for a skill rated 3/5 or above; only a skill rated
  1/5 or 2/5 may genuinely be treated as a weakness or risk.
- Technology alignment: flag (category "project_alignment") any mention of
  React, Node.js, Vue, Angular, Flutter, Dart, Kafka, Azure, AWS, GCP,
  Kubernetes, blockchain, Web3, or Solidity anywhere in the analysis.
- Dataset interpretation: flag (category "contradiction") if
  dataReadinessScore is low despite datasetNeeded clearly indicating no
  dataset is required, or vice versa.
"""


class ProjectDNACandidateSchema(ProjectDNAResponse):
    """
    Wraps the agent's own ProjectDNAResponse with the structural invariants
    that must survive a Rewrite untouched -- riskProfile must contain the
    2-4 items the agent's own prompt contract promises (the deterministic
    Python fallback path already respects this range, but nothing enforced
    a minimum on the LLM path before this), and requiredSkillsAnalysis must
    never be empty. A violation here is a schema failure (schema_ok=False),
    handled entirely by the pipeline's existing structural-repair path -- no
    changes to pipeline.py's decision logic were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "ProjectDNACandidateSchema":
        if not (2 <= len(self.riskProfile) <= 4):
            raise ValueError(
                f"riskProfile must contain 2 to 4 items, got {len(self.riskProfile)}"
            )

        if not self.requiredSkillsAnalysis:
            raise ValueError("requiredSkillsAnalysis must not be empty")

        return self


_IDEA_COMPARISON_EXTRA_RUBRIC = """
IDEA COMPARISON-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- skillFitScore must not contradict the student's actual skillRatings in the
  trusted project context -- a skill rated 3/5 or higher must never be
  treated as a weakness or a reason for a low score. Flag as category
  "contradiction".
- Technology alignment: flag (category "project_alignment") any mention of
  React, Node.js, Vue, Angular, Flutter, Dart, Kafka, Azure, AWS, GCP,
  Kubernetes, blockchain, Web3, or Solidity anywhere in bestFor, strengths,
  weaknesses, recommendation, or finalRecommendation.
- Ranking consistency: flag (category "contradiction") if finalRecommendation
  does not clearly point to the idea identified as bestIdeaId/bestIdeaTitle.
- Unsupported claims: flag (category "unsupported_claim") any invented
  statistic, named organization, or citation not grounded in the provided
  idea data.
"""


class IdeaComparisonCandidateSchema(IdeaComparisonResponse):
    """
    Wraps the agent's own IdeaComparisonResponse with the structural
    invariants that must survive a Rewrite untouched -- the ideas list count
    must match totalIdeasCompared, ranks must be an exact 1..N permutation
    (no gaps or duplicates), and bestIdeaId/bestIdeaTitle must actually be
    the idea ranked #1. A violation here is a schema failure
    (schema_ok=False), handled entirely by the pipeline's existing
    structural-repair path -- no changes to pipeline.py's decision logic
    were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "IdeaComparisonCandidateSchema":
        if len(self.ideas) != self.totalIdeasCompared:
            raise ValueError(
                f"ideas count ({len(self.ideas)}) does not match "
                f"totalIdeasCompared ({self.totalIdeasCompared})"
            )

        ranks = sorted(idea.rank for idea in self.ideas)
        expected_ranks = list(range(1, len(self.ideas) + 1))

        if ranks != expected_ranks:
            raise ValueError(f"ranks are not an exact 1..N permutation: {ranks}")

        if self.ideas:
            top_ranked = next(idea for idea in self.ideas if idea.rank == 1)

            if top_ranked.ideaId != self.bestIdeaId or top_ranked.title != self.bestIdeaTitle:
                raise ValueError(
                    "bestIdeaId/bestIdeaTitle does not match the idea ranked #1: "
                    f"top_ranked=({top_ranked.ideaId}, '{top_ranked.title}'), "
                    f"declared=({self.bestIdeaId}, '{self.bestIdeaTitle}')"
                )

        return self


# Shared across both market agents -- blocked technologies are already
# deterministically scrubbed from narrative text where possible, so this is
# a defense-in-depth backstop, plus generic overclaiming phrases only a
# semantic Reviewer can judge.
_MARKET_KNOWN_RISKY_CLAIMS = [
    "guaranteed",
    "proven to succeed",
    "zero risk",
    "millions of users",
    "billion-dollar market",
    "React",
    "Node.js",
    "Vue",
    "Angular",
    "Flutter",
    "Kafka",
    "Azure",
    "AWS",
    "GCP",
    "Kubernetes",
    "blockchain",
    "Web3",
    "Solidity",
]

_MARKET_FOOTPRINT_EXTRA_RUBRIC = """
MARKET FOOTPRINT-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT suggest changing overallOpportunityScore, overallConfidenceScore,
  any region's opportunityScore/confidenceScore, or evidenceStrength --
  these are computed deterministically from verified source evidence,
  never written by the LLM.
- Do NOT suggest changing sourceUrls, sourceTitles, or the sources list --
  these are matched deterministically from real search results, never
  authored by the LLM.
- Unsupported claims: flag (category "unsupported_claim") any statistic,
  percentage, market size, revenue figure, CAGR, or user count that is not
  explicitly present in the verified search evidence.
- Every evidenceSummary, bestLaunchReason, and strategicRecommendation claim
  should be traceable to that region's own matched sources; flag ungrounded
  claims as "unsupported_claim".
- Technology alignment: flag (category "project_alignment") any mention of
  a technology outside the project's actual stack.
"""


class MarketFootprintCandidateSchema(MarketFootprintResponse):
    """
    Wraps the agent's own MarketFootprintResponse with the structural
    invariants that must survive a Rewrite untouched -- exactly the 3
    required regions (lebanon/mena/global), and every sourceUrl referenced
    by a region must actually exist in the top-level sources list (sources
    are matched deterministically from real search results in the agent
    itself, so a Rewrite inventing a sourceUrl not in that list is a real
    defect, not a stylistic difference). A violation here is a schema
    failure (schema_ok=False), handled entirely by the pipeline's existing
    structural-repair path -- no changes to pipeline.py's decision logic
    were needed for this (same pattern as RoadmapCandidateSchema and the
    other candidate schemas above).
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "MarketFootprintCandidateSchema":
        region_keys = {region.region_key for region in self.regions}

        if region_keys != {"lebanon", "mena", "global"}:
            raise ValueError(
                f"regions must be exactly lebanon/mena/global, got: {sorted(region_keys)}"
            )

        known_urls = {source.url for source in self.sources}

        for region in self.regions:
            for url in region.source_urls:
                if url not in known_urls:
                    raise ValueError(
                        f"region '{region.region_key}' references a sourceUrl "
                        f"not present in the verified sources list: {url}"
                    )

        return self


_MARKET_NEEDS_EXTRA_RUBRIC = """
MARKET NEEDS-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT suggest changing demandScore, confidenceScore, any scoreBreakdown
  dimension, any yearlyPoints demandIndex/confidenceScore, or
  annualForecast -- these are computed deterministically (annualForecast
  uses a real statistical time-series model), never written by the LLM.
- Do NOT suggest changing sources, yearlyPoints sourceUrls, or
  trendSignals sourceUrl -- these are matched deterministically from real
  search results, never authored by the LLM.
- Unsupported claims: flag (category "unsupported_claim") any statistic,
  market size, revenue figure, or user count not explicitly present in the
  verified search evidence.
- Annual values are evidence indices from 0 to 100, not revenue, market
  size, or Google Trends values -- flag any claim treating them as such.
- Technology alignment: flag (category "project_alignment") any mention of
  a technology outside the project's actual stack.
"""


class MarketNeedsCandidateSchema(MarketNeedsResponse):
    """
    Wraps the agent's own MarketNeedsResponse with the structural invariant
    that must survive a Rewrite untouched -- every sourceUrl referenced by
    a yearly point or trend signal must actually exist in the top-level
    sources list (sources are matched deterministically from real search
    results in the agent itself, so a Rewrite inventing one is a real
    defect). A violation here is a schema failure (schema_ok=False),
    handled entirely by the pipeline's existing structural-repair path --
    no changes to pipeline.py's decision logic were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "MarketNeedsCandidateSchema":
        known_urls = {source.url for source in self.sources}

        for point in self.yearly_points:
            for url in point.source_urls:
                if url not in known_urls:
                    raise ValueError(
                        f"yearly point {point.year} references a sourceUrl "
                        f"not present in the verified sources list: {url}"
                    )

        for signal in self.trend_signals:
            if signal.source_url and signal.source_url not in known_urls:
                raise ValueError(
                    f"trend signal '{signal.topic}' references a sourceUrl "
                    f"not present in the verified sources list: {signal.source_url}"
                )

        return self


# Migrated from DefenseSimulatorOrchestrator._clean_unverified_project_claims's
# replacement dict (see defense_simulator_orchestrator.py) -- there they were
# blindly regex-replaced; here they are domain knowledge fed into the semantic
# Reviewer's prompt, matching how _MENTOR_KNOWN_RISKY_CLAIMS was migrated from
# answer_review_agent.py. The regex cleanup itself is left in place as a
# defense-in-depth backstop, not removed.
_DEFENSE_KNOWN_RISKY_CLAIMS = [
    "ASP.NET Core Identity",
    "data is encrypted before storing",
    "regular security audits",
    "security audits will be conducted",
    "deployment is complete",
    "deployed to production",
    "natural language processing techniques",
    "React",
    "Node.js",
    "Vue",
    "Angular",
    "Flutter",
    "Azure",
    "AWS",
    "Kubernetes",
    "blockchain",
]

_DEFENSE_QUESTIONS_VALID_CATEGORIES = {
    "Problem Understanding",
    "Technical Architecture",
    "Database Design",
    "AI Integration",
    "Feasibility",
    "Testing and Validation",
    "Security",
    "Limitations",
    "Future Work",
    "Business Value",
}
_DEFENSE_QUESTIONS_VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}

_DEFENSE_QUESTIONS_EXTRA_RUBRIC = """
DEFENSE QUESTION-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT invent or imply confirmed project features (security audits,
  database encryption, ASP.NET Core Identity, completed deployment) unless
  the selected idea/roadmap context explicitly states them; flag as
  "unsupported_claim".
- category must be one of: Problem Understanding, Technical Architecture,
  Database Design, AI Integration, Feasibility, Testing and Validation,
  Security, Limitations, Future Work, Business Value. Flag any other value
  as "missing_mandatory_content".
- difficulty must be one of: Easy, Medium, Hard.
- Technology alignment: flag (category "project_alignment") any mention of
  a technology outside the project's actual stack.
- Each question should be specific to this project, not generic.
"""


class DefenseQuestionBatchCandidateSchema(DefenseQuestionBatch):
    """
    Wraps the orchestrator's own DefenseQuestionBatch with the structural
    invariants that must survive a Rewrite untouched -- unique question ids,
    and every question's category/difficulty drawn from the fixed valid
    sets DefenseSimulatorOrchestrator itself enforces deterministically
    (_clean_questions). A violation here is a schema failure
    (schema_ok=False), handled entirely by the pipeline's existing
    structural-repair path -- no changes to pipeline.py's decision logic
    were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "DefenseQuestionBatchCandidateSchema":
        if not self.questions:
            raise ValueError("questions must not be empty")

        ids = [question.id for question in self.questions]

        if len(ids) != len(set(ids)):
            raise ValueError(f"question ids are not unique: {ids}")

        for question in self.questions:
            if question.category not in _DEFENSE_QUESTIONS_VALID_CATEGORIES:
                raise ValueError(
                    f"question '{question.id}' has an invalid category: {question.category}"
                )

            if question.difficulty not in _DEFENSE_QUESTIONS_VALID_DIFFICULTIES:
                raise ValueError(
                    f"question '{question.id}' has an invalid difficulty: {question.difficulty}"
                )

        return self


_DEFENSE_EVALUATION_EXTRA_RUBRIC = """
DEFENSE EVALUATION-SPECIFIC REVIEW CRITERIA (in addition to the standard criteria above):
- Do NOT invent or imply confirmed project features (security audits,
  database encryption, ASP.NET Core Identity, completed deployment) unless
  clearly present in the provided project context; flag as
  "unsupported_claim".
- Do not mark a point as missing if the student's answer covers it with
  different wording; flag an unfair missingPoints entry as category
  "quality".
- feedbackSummary and improvedAnswer should be constructive and specific to
  the actual question and answer, not generic.
"""


def _defense_score_to_level(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 80:
        return "Very Good"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Average"
    return "Weak"


class DefenseEvaluationCandidateSchema(DefenseEvaluationCandidate):
    """
    Wraps the orchestrator's own DefenseEvaluationCandidate with the
    structural invariant that must survive a Rewrite untouched -- level
    must equal the deterministic score-to-level mapping
    DefenseSimulatorOrchestrator._score_to_level already uses (0-100 score,
    exactly 5 level bands). A violation here is a schema failure
    (schema_ok=False), handled entirely by the pipeline's existing
    structural-repair path -- no changes to pipeline.py's decision logic
    were needed for this.
    """

    @model_validator(mode="after")
    def _check_structural_invariants(self) -> "DefenseEvaluationCandidateSchema":
        if not (0 <= self.score <= 100):
            raise ValueError(f"score must be between 0 and 100, got {self.score}")

        expected_level = _defense_score_to_level(self.score)

        if self.level != expected_level:
            raise ValueError(
                f"level '{self.level}' does not match the deterministic mapping "
                f"for score {self.score} (expected '{expected_level}')"
            )

        return self


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
    "ProjectRoadmapAgent": AgentReviewConfig(
        schema=RoadmapCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_ROADMAP_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["roadmapTitle", "teamStrategy", "finalAdvice"],
        max_total_seconds=90.0,
        extra_rubric=_ROADMAP_EXTRA_RUBRIC,
    ),
    "SEDocumentationAgent": AgentReviewConfig(
        schema=SEDocumentationCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_SEDOC_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["projectTitle", "projectOverview", "problemStatement"],
        # Higher than Roadmap/Mentor Chat's 90s: the Writer stage here makes
        # up to 5 sequential LLM calls (requirements, use cases, modules,
        # database, testing) before the Reviewer even runs once.
        max_total_seconds=180.0,
        extra_rubric=_SEDOC_EXTRA_RUBRIC,
    ),
    "ProjectIdeaAgent": AgentReviewConfig(
        schema=IdeaGenerationCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_IDEA_GENERATION_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["ideas"],
        # Writer stage does a live search call plus a generation call before
        # the Reviewer runs once -- between Roadmap's single-call 90s and
        # SE Documentation's 5-call 180s.
        max_total_seconds=120.0,
        extra_rubric=_IDEA_GENERATION_EXTRA_RUBRIC,
    ),
    "ProjectDNAAgent": AgentReviewConfig(
        schema=ProjectDNACandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_DNA_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["projectDNAType", "summary"],
        max_total_seconds=90.0,
        extra_rubric=_DNA_EXTRA_RUBRIC,
    ),
    "IdeaComparisonAgent": AgentReviewConfig(
        schema=IdeaComparisonCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_DNA_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["summary", "finalRecommendation"],
        max_total_seconds=90.0,
        extra_rubric=_IDEA_COMPARISON_EXTRA_RUBRIC,
    ),
    "MarketFootprintAgent": AgentReviewConfig(
        schema=MarketFootprintCandidateSchema,
        max_rewrites=1,
        # Unlike every other agent so far, this agent's output legitimately
        # and structurally contains real URLs (sources/sourceUrls, matched
        # deterministically from live search, never LLM-authored). The
        # router runs the real analysis once, up front, and populates
        # allowed_source_metadata with exactly those verified sources
        # before calling ReviewPipeline -- see market_footprint.py.
        url_mode="source_metadata_only",
        allow_unreviewed_output=False,
        known_risky_claims=_MARKET_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["bestLaunchMarket", "strategicRecommendation"],
        # Writer stage does 2 sequential calls (search, then generation)
        # before the Reviewer runs once.
        max_total_seconds=150.0,
        extra_rubric=_MARKET_FOOTPRINT_EXTRA_RUBRIC,
    ),
    "MarketNeedsAgent": AgentReviewConfig(
        schema=MarketNeedsCandidateSchema,
        max_rewrites=1,
        # Same reasoning as MarketFootprintAgent above -- sources/
        # sourceUrls are real, deterministically matched, never
        # LLM-authored, so allowed_source_metadata is populated by the
        # router from the same real analysis run before ReviewPipeline
        # is called.
        url_mode="source_metadata_only",
        allow_unreviewed_output=False,
        known_risky_claims=_MARKET_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["targetSector", "recommendation"],
        max_total_seconds=120.0,
        extra_rubric=_MARKET_NEEDS_EXTRA_RUBRIC,
    ),
    "DefenseQuestionAgent": AgentReviewConfig(
        schema=DefenseQuestionBatchCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_DEFENSE_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["questions"],
        max_total_seconds=90.0,
        extra_rubric=_DEFENSE_QUESTIONS_EXTRA_RUBRIC,
    ),
    "DefenseEvaluatorAgent": AgentReviewConfig(
        schema=DefenseEvaluationCandidateSchema,
        max_rewrites=1,
        url_mode="no_urls_allowed",
        allow_unreviewed_output=False,
        known_risky_claims=_DEFENSE_KNOWN_RISKY_CLAIMS,
        mandatory_fields=["feedbackSummary", "improvedAnswer"],
        max_total_seconds=90.0,
        extra_rubric=_DEFENSE_EVALUATION_EXTRA_RUBRIC,
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
