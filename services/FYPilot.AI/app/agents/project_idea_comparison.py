"""
IdeaComparisonAgent — qualitative ranking/explanation of a student's own
already-generated project ideas for FYPilot.

Design:
- ProviderChain(tier="comparison") -- the exact same shared orchestrator
  every other agent uses (DeepInfra -> Groq -> Ollama, fallback, timing,
  retries, provider logging, model resolution, error classification all
  owned by app/services/llm_provider.py). This agent never constructs a
  DeepInfraProvider/GroqProvider/OllamaProvider itself and never makes a
  direct SDK/HTTP call of its own -- the "comparison" tier (see
  _DEEPINFRA_TIER_DEFAULTS/_DEEPINFRA_TIER_TIMING in llm_provider.py) is
  the centralized place that gives this agent a faster model and tighter
  timeout/retry budget, the same mechanism every tier already uses.
- This agent produces QUALITATIVE output only: rank, comparative reasoning,
  strength, risk, best-use case, comparison advantage, required validation,
  recommendation. It never invents Innovation/Feasibility/MarketDemand/
  Overall percentages -- those are generated once during Idea Generation
  (ProjectIdeaAgent), saved on ProjectIdea, and read-only evidence here.
  ComparedIdeaQualitative has no score fields at all, so a fabricated
  number cannot even be parsed into the structured result.
- One batch call ranks every visible idea at once (never one call per idea).
- Repetitive/near-identical or generic (no concrete idea attribute) output
  gets exactly one targeted rewrite attempt with feedback naming the
  affected ideas; if it's still repetitive after that, or every provider
  failed, this returns a safe "unavailable" response (available=False) --
  never fabricated idea-specific-looking text. See _find_repetition_issues
  and _safe_unavailable_response.
- Field-length limits and structural invariants (rank permutation,
  bestIdeaId/rank-1 match) are enforced by IdeaComparisonCandidateSchema in
  app/review/registry.py, reusing ReviewPipeline's existing schema-repair
  loop rather than a second, duplicate retry mechanism here.
"""

import difflib
import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from app.services.llm_provider import LLMResult, ProviderChain

logger = logging.getLogger("fypilot-idea-comparison-agent")


class IdeaComparisonInput(BaseModel):
    id: int
    title: str
    problemStatement: str = ""
    whyUseful: str = ""
    targetUsers: str = ""
    requiredTechnologies: str = ""
    requiredSkills: str = ""
    missingSkills: str = ""
    difficultyLevel: str = "medium"
    expectedDurationWeeks: int = 10
    datasetNeeded: str = ""
    domain: str = ""
    lebaneseMarketRelevance: str = ""
    # Official, already-generated scores from Idea Generation -- read-only
    # evidence for the LLM's reasoning, never regenerated here. 0 means
    # "not evaluated for this idea" (ScoreHelper.IsEvaluated's convention
    # on the .NET side), not a real low score. Reasons are explicitly
    # optional/nullable -- older ideas generated before score explanations
    # existed send JSON null here (see IdeaComparisonModel.GetNullableString
    # on the .NET side), not an empty string, so these must accept None.
    innovationScore: float = 0
    innovationScoreReason: str | None = None
    feasibilityScore: float = 0
    feasibilityScoreReason: str | None = None
    marketDemandScore: float = 0
    marketDemandScoreReason: str | None = None
    createdAt: str = ""


class IdeaComparisonRequest(BaseModel):
    studentMajor: str = "Computer Science"
    experienceLevel: str = "intermediate"
    teamSize: int = 1
    availableHoursPerWeek: int = 10
    studentSkills: list[str] = Field(default_factory=list)
    skillRatings: dict[str, int] = Field(default_factory=dict)
    ideas: list[IdeaComparisonInput] = Field(default_factory=list)


class ComparedIdeaQualitative(BaseModel):
    """Qualitative-only per-idea result -- no numeric score fields."""

    ideaId: int
    rank: int
    title: str
    contextSummary: str
    whyThisRank: str
    mainStrength: str
    mainRisk: str
    bestFor: str
    comparisonAdvantage: str
    requiredValidation: str
    riskLevel: str
    recommendation: str


class IdeaComparisonResponse(BaseModel):
    comparisonTitle: str
    totalIdeasCompared: int
    bestIdeaId: int
    bestIdeaTitle: str
    summary: str
    ideas: list[ComparedIdeaQualitative]
    finalRecommendation: str
    # False only for the safe "could not be generated" response (all
    # providers failed, or output stayed repetitive/generic after one
    # targeted rewrite) -- lets the .NET/Razor side show the compact safe
    # message instead of an empty-looking winner/ranking UI.
    available: bool = True


# Word-count ceilings enforced by IdeaComparisonCandidateSchema (see
# app/review/registry.py) -- kept here too so the agent's own fallback/
# completion text always stays within them.
FIELD_WORD_LIMITS: dict[str, int] = {
    "contextSummary": 18,
    "whyThisRank": 32,
    "mainStrength": 18,
    "mainRisk": 18,
    "bestFor": 18,
    "comparisonAdvantage": 24,
    "requiredValidation": 20,
    "recommendation": 24,
}

_REPETITIVE_TEXT_FIELDS = [
    "contextSummary", "whyThisRank", "mainStrength", "mainRisk",
    "bestFor", "comparisonAdvantage", "recommendation",
]

_DOMAIN_GUIDANCE: dict[str, str] = {
    "healthcare": (
        "clinical workflow fit, private health information, dataset/appointment-record "
        "access, required validation, safety boundaries"
    ),
    "agriculture": (
        "crop or weather data sourcing, irrigation logic, local price data, seasonal "
        "variation, rural connectivity, farmer usability"
    ),
    "finance": (
        "transaction/invoice data accuracy, privacy, security, financial validation, "
        "data-entry reliability"
    ),
    "arabic_nlp": (
        "Arabic dialects, mixed Arabic-English input, labelled text data availability, "
        "classification accuracy, text normalization"
    ),
    "university": (
        "access to student data, course feedback, academic workflows, privacy, "
        "university adoption"
    ),
    "general": (
        "implementation risk grounded in this idea's own technologies, dataset needs "
        "and scope"
    ),
}


class IdeaComparisonAgent:
    """
    Qualitative-ranking agent: ProviderChain reasons and ranks, Python
    validates structure and protects the app from invalid or repetitive
    output. Never computes or requests numeric scores.
    """

    def __init__(self):
        # Shared orchestrator, same as every other agent -- just a
        # different tier. "comparison" (see _DEEPINFRA_TIER_DEFAULTS /
        # _DEEPINFRA_TIER_TIMING in llm_provider.py) resolves to a faster
        # model (measured: gemma-3-12b-it finished this agent's prompt in
        # ~22s with equally idea-specific output, while the 70B "standard"
        # model timed out at 40s on the same prompt) and a single 30s
        # DeepInfra attempt before falling to Groq -- both centralized,
        # reusable settings, not something this agent constructs itself.
        self.provider_chain = ProviderChain(tier="comparison")

        self.last_llm_used = False
        self.last_error: str | None = None
        self.last_raw_llm_response: str | None = None
        self.last_provider: str | None = None
        self.last_model_used: str | None = None
        self.last_rewrite_attempted = False

        # A normal comparison batch is the student's latest Idea Generator
        # run (~3-4 ideas) or a manual selection of 2-5 -- see
        # IdeaComparisonModel on the .NET side, which now enforces this
        # before the request ever reaches here. max_ideas stays as a
        # defensive backstop only, never the expected normal size.
        self.max_ideas = 12

        # Above this batch size, a second full LLM call to fix flagged
        # repetition would push worst-case latency past the deadline --
        # see the size check in compare(). Set above the new hard maximum
        # (5) so it only ever matters for a request that slipped past the
        # .NET-side cap.
        self._max_ideas_for_auto_rewrite = 5

        # Below this many remaining deadline seconds, a retry call (tuned
        # for a ~30s DeepInfra attempt, see the "comparison" tier) has too
        # little time left to plausibly finish -- skip it and go straight
        # to the safe fallback rather than starting a call that will just
        # get cut off.
        self._min_seconds_for_retry = 25.0

        self.blocked_terms = [
            "react", "node", "vue", "angular", "flutter", "dart", "kafka",
            "azure", "aws", "gcp", "kubernetes", "blockchain", "web3",
            "solidity", "smart contract",
        ]

    # =========================================================================
    # Public entry points
    # =========================================================================

    def compare(
        self,
        request: IdeaComparisonRequest,
        deadline: float | None = None,
    ) -> IdeaComparisonResponse:
        self.last_llm_used = False
        self.last_error = None
        self.last_raw_llm_response = None
        self.last_provider = None
        self.last_model_used = None
        self.last_rewrite_attempted = False

        ideas = self._normalize_ideas(request.ideas)

        if not ideas:
            return self._empty_response()

        limited_request = IdeaComparisonRequest(
            studentMajor=request.studentMajor,
            experienceLevel=request.experienceLevel,
            teamSize=request.teamSize,
            availableHoursPerWeek=request.availableHoursPerWeek,
            studentSkills=request.studentSkills,
            skillRatings=request.skillRatings,
            ideas=ideas,
        )

        raw, provider, model, error = self._call_provider(limited_request, deadline=deadline)

        if raw is None:
            self.last_llm_used = False
            self.last_error = error or (
                "All AI providers failed to return valid idea comparison JSON."
            )
            return self._safe_unavailable_response(limited_request)

        self.last_llm_used = True
        self.last_provider = provider
        self.last_model_used = model

        result_obj = self._complete_and_validate(limited_request, raw)
        flagged = self._find_repetition_issues(result_obj, limited_request)

        if not flagged:
            return result_obj

        if len(limited_request.ideas) > self._max_ideas_for_auto_rewrite:
            # A second full DeepInfra/Groq call on a large batch would push
            # worst-case latency well past what a live defense demo can
            # wait for. The semantic Reviewer's own single rewrite
            # (registry.py's "repetitive_or_generic_comparison" rubric)
            # still gets a chance on this candidate downstream -- this just
            # skips the agent's OWN extra full-batch retry.
            logger.info(
                "IdeaComparisonAgent: repetitive/generic output detected for idea "
                "ids %s on a %d-idea batch -- skipping the agent-level rewrite "
                "(too slow at this size); returning to the safe fallback.",
                flagged,
                len(limited_request.ideas),
            )
            self.last_llm_used = False
            self.last_error = (
                "Comparison output was repetitive or generic for a large batch; "
                "skipped the extra rewrite attempt to stay within a reasonable response time."
            )
            return self._safe_unavailable_response(limited_request)

        if deadline is not None and (deadline - time.monotonic()) < self._min_seconds_for_retry:
            # Do not start another provider call when insufficient request
            # time remains -- the request's end-to-end deadline (see
            # routers/idea_comparison.py) already ate most of the budget on
            # the first attempt.
            logger.info(
                "IdeaComparisonAgent: repetitive/generic output detected for idea "
                "ids %s but insufficient deadline time remains for a retry; "
                "returning to the safe fallback.",
                flagged,
            )
            self.last_llm_used = False
            self.last_error = (
                "Comparison output was repetitive or generic and there was not enough "
                "request time left for a rewrite attempt."
            )
            return self._safe_unavailable_response(limited_request)

        logger.info(
            "IdeaComparisonAgent: repetitive/generic output detected for idea "
            "ids %s -- attempting one targeted rewrite.",
            flagged,
        )
        self.last_rewrite_attempted = True

        retry_raw, retry_provider, retry_model, _retry_error = self._call_provider(
            limited_request, feedback_idea_ids=flagged, deadline=deadline,
        )

        if retry_raw is not None:
            retry_result = self._complete_and_validate(limited_request, retry_raw)
            still_flagged = self._find_repetition_issues(retry_result, limited_request)

            if not still_flagged:
                self.last_provider = retry_provider
                self.last_model_used = retry_model
                return retry_result

        # Still repetitive/generic (or the rewrite call itself failed) --
        # do not show text we already know is not idea-specific.
        self.last_llm_used = False
        self.last_error = (
            "Comparison output remained repetitive or generic after one "
            "targeted rewrite attempt."
        )
        return self._safe_unavailable_response(limited_request)

    def build_safe_fallback(self, request: IdeaComparisonRequest) -> IdeaComparisonResponse:
        """
        Public entry point for the safe "unavailable" response -- used by
        the router whenever ReviewPipeline could not produce a usable
        result (provider failure, firewall block, rejected, or review
        timeout with nothing safe to show). Never fabricates idea-specific-
        looking strengths/weaknesses; the saved score comparison table
        remains the fallback UI, not this response (see IdeaComparison.cshtml).
        """
        ideas = self._normalize_ideas(request.ideas)

        if not ideas:
            return self._empty_response()

        limited_request = IdeaComparisonRequest(
            studentMajor=request.studentMajor,
            experienceLevel=request.experienceLevel,
            teamSize=request.teamSize,
            availableHoursPerWeek=request.availableHoursPerWeek,
            studentSkills=request.studentSkills,
            skillRatings=request.skillRatings,
            ideas=ideas,
        )

        return self._safe_unavailable_response(limited_request)

    def generate_candidate(
        self,
        request: IdeaComparisonRequest,
        deadline: float | None = None,
    ) -> LLMResult | None:
        """
        Writer-stage entry point for ReviewPipeline. Reuses compare() end to
        end, then wraps the result as an LLMResult so it flows through
        guarded_call like any other LLM stage.

        Returns None -- signaling "no real provider output" to guarded_call,
        which the pipeline maps to status="provider_unavailable" -- whenever
        compare() had to fall back internally (self.last_llm_used is False,
        whether from provider failure or unresolved repetition) or there
        were no valid ideas to compare; the router uses build_safe_fallback()
        directly in both cases.
        """
        result = self.compare(request, deadline=deadline)

        if not self.last_llm_used:
            return None

        return LLMResult(
            ok=True,
            provider=self.last_provider or "unknown",
            model=self.last_model_used,
            text="",
            data=result.model_dump(),
        )

    # =========================================================================
    # Provider call
    # =========================================================================

    def _call_provider(
        self,
        request: IdeaComparisonRequest,
        *,
        feedback_idea_ids: list[int] | None = None,
        deadline: float | None = None,
    ) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
        prompt = self._build_prompt(request, feedback_idea_ids=feedback_idea_ids)

        try:
            result = self.provider_chain.generate_json(
                prompt,
                use_search=False,
                max_tokens=self._max_tokens_for(request),
                deadline=deadline,
            )
        except Exception as ex:
            logger.exception("Idea comparison generation failed.")
            return None, None, None, f"Idea comparison generation failed: {ex}"

        provider = result.provider if result.provider != "none" else None

        if result.ok and isinstance(result.data, dict):
            self.last_raw_llm_response = json.dumps(result.data, ensure_ascii=False)[:1500]
            return result.data, provider, result.model, None

        return None, provider, result.model, (
            result.error or "No provider returned valid idea comparison JSON."
        )

    def _max_tokens_for(self, request: IdeaComparisonRequest) -> int:
        # Short, word-capped qualitative fields per idea -- scales with
        # batch size instead of one fixed budget. Ceiling lowered to match
        # the new hard maximum of 5 ideas per request (see
        # IdeaComparisonModel.HardMaximumIdeasToCompare on the .NET side) --
        # a normal request is 2-4 ideas.
        return min(2200, 900 + len(request.ideas) * 220)

    # =========================================================================
    # Prompt
    # =========================================================================

    def _detect_domain_category(self, idea: IdeaComparisonInput) -> str:
        text = f"{idea.title} {idea.domain} {idea.problemStatement} {idea.targetUsers}".lower()

        if any(t in text for t in ["clinic", "health", "patient", "medical", "hospital", "triage", "appointment"]):
            return "healthcare"

        if any(t in text for t in ["crop", "farm", "agri", "irrigation", "weather", "harvest", "livestock"]):
            return "agriculture"

        if any(t in text for t in ["invoice", "finance", "payment", "budget", "expense", "cash flow", "accounting", "transaction"]):
            return "finance"

        if any(t in text for t in ["arabic", "dialect", "nlp", "sentiment analysis", "text classification"]):
            return "arabic_nlp"

        if any(t in text for t in ["university", "student", "course", "academic", "curriculum", "supervisor"]):
            return "university"

        return "general"

    def _build_prompt(
        self,
        request: IdeaComparisonRequest,
        *,
        feedback_idea_ids: list[int] | None = None,
    ) -> str:
        skills_text = (
            ", ".join(request.studentSkills) if request.studentSkills else "No skills provided"
        )

        ratings_text = (
            ", ".join(f"{skill}: {rating}/5" for skill, rating in request.skillRatings.items())
            if request.skillRatings
            else "No ratings provided"
        )

        # Trimmed, not the full model_dump(): a 12-idea batch with every
        # field (including three ~150-300 char saved-score reasons each)
        # at full length produced a 24k-character prompt that measured
        # >90s on DeepInfra for this task -- too slow for a live defense.
        # The LLM needs enough to ground idea-specific reasoning, not the
        # complete verbose text of every field.
        ideas_payload = []

        for idea in request.ideas:
            payload = {
                "id": idea.id,
                "title": idea.title,
                "problemStatement": self._truncate(idea.problemStatement, 220),
                "targetUsers": self._truncate(idea.targetUsers, 100),
                "requiredTechnologies": idea.requiredTechnologies,
                "requiredSkills": idea.requiredSkills,
                "missingSkills": idea.missingSkills,
                "difficultyLevel": idea.difficultyLevel,
                "expectedDurationWeeks": idea.expectedDurationWeeks,
                "datasetNeeded": self._truncate(idea.datasetNeeded, 140),
                "domain": idea.domain,
                "domainCategory": self._detect_domain_category(idea),
                "lebaneseMarketRelevance": self._truncate(idea.lebaneseMarketRelevance, 100),
                "innovationScore": idea.innovationScore,
                "innovationScoreReason": self._truncate(
                    idea.innovationScoreReason, 140,
                    fallback="Not recorded (this idea predates saved score explanations).",
                ),
                "feasibilityScore": idea.feasibilityScore,
                "feasibilityScoreReason": self._truncate(
                    idea.feasibilityScoreReason, 140,
                    fallback="Not recorded (this idea predates saved score explanations).",
                ),
                "marketDemandScore": idea.marketDemandScore,
                "marketDemandScoreReason": self._truncate(
                    idea.marketDemandScoreReason, 140,
                    fallback="Not recorded (this idea predates saved score explanations).",
                ),
            }

            ideas_payload.append(payload)

        ideas_text = json.dumps(ideas_payload, ensure_ascii=False, indent=2)

        domain_guidance_text = "\n".join(
            f'- "{category}": discuss {guidance}, when the idea\'s domainCategory matches.'
            for category, guidance in _DOMAIN_GUIDANCE.items()
            if category != "general"
        )

        feedback_section = ""

        if feedback_idea_ids:
            feedback_section = f"""
IMPORTANT -- REWRITE REQUIRED:
Your previous response was rejected because the text for idea ID(s)
{feedback_idea_ids} was too similar to another idea's text, or too generic
(no concrete detail from that specific idea). Rewrite the ENTIRE response.
Keep the same ranking and the same reasoning quality for ideas that were NOT
flagged, but make every flagged idea's contextSummary, whyThisRank,
mainStrength, mainRisk, bestFor, comparisonAdvantage and recommendation
CLEARLY and UNIQUELY grounded in that idea's own title, technologies,
domain, dataset need or missing skills -- text that could be pasted onto
another idea is not acceptable.
"""

        return f"""
You are IdeaComparisonAgent inside FYPilot, an Academic Intelligence System for Final Year Project planning.

Your task is to rank and EXPLAIN a batch of the student's own already-generated
final year project ideas. You do NOT score them -- every idea already has
official Innovation, Feasibility and Market Demand scores, generated and saved
during Idea Generation, included below as FIXED EVIDENCE.

THESE PERCENTAGES WERE ALREADY GENERATED DURING IDEA GENERATION AND SAVED IN
THE DATABASE. THEY ARE FIXED EVIDENCE. DO NOT REPLACE, MODIFY, NORMALIZE,
REINTERPRET OR RETURN ALTERNATIVE NUMERIC SCORES. Do not put any percentage
or score number anywhere in your response -- your output schema has no field
for one. A score of 0 for a given metric means it was not evaluated for that
idea; treat it as unknown, not as a real low score.

Compare ONLY the ideas provided below. Do not invent new ideas. Do not compare
ideas from other students. Do not recommend technologies outside the
provided/common project stack (ASP.NET Core Razor Pages, Python FastAPI,
PostgreSQL, Bootstrap, JavaScript). Never suggest React, Node.js, Flutter,
AWS, Azure, Kubernetes, blockchain, or Web3.

Student profile:
- Major: {request.studentMajor}
- Experience level: {request.experienceLevel}
- Team size: {request.teamSize}
- Available hours per week: {request.availableHoursPerWeek}
- Student skills: {skills_text}
- Skill ratings: {ratings_text}

Ideas to compare (each includes its official saved scores as fixed evidence,
plus a domainCategory hint):
{ideas_text}

Domain-specific reasoning guidance -- apply when an idea's domainCategory matches:
{domain_guidance_text}

MANDATORY: every idea's contextSummary/whyThisRank/mainStrength/mainRisk/
bestFor/comparisonAdvantage must mention at least TWO concrete attributes
from THAT idea specifically -- a named technology, an actual dataset
requirement, a target-user group, a domain-specific workflow, a missing
skill, the expected duration, or a Lebanese-market condition. Text generic
enough to paste onto a different idea is not acceptable. Sharing domain
terminology or the same technology stack across ideas is fine and expected
(most of these ideas share ASP.NET Core/Python FastAPI/PostgreSQL) -- that
is not what makes text generic.

If two or more ideas look similar (same domain, same rough concept), you
MUST name the exact feature or scope difference between them -- e.g. "unlike
the clinic triage assistant, this one predicts no-shows instead of
symptoms" or "narrower scope than the AgriTech platform: price forecasting
only, no crop-image diagnosis." Do NOT reuse the same advantage or
recommendation sentence (even with the title swapped) for two different
ideas -- each idea's comparisonAdvantage and recommendation must be
distinguishable from every other idea's without reading the title.

Comparative reasoning rules (do not evaluate ideas independently):
- For the #1 rank: state its strongest comparative advantage over the other
  ideas, and which uncertainty is lower than competing ideas.
- For middle ranks: explain why the idea is still competitive AND exactly
  what keeps it from ranking higher (be specific, e.g. "ranks below X because
  its dataset is harder to obtain than X's").
- For the lowest ranks: name the concrete blocker (data uncertainty, API
  dependency, excessive scope, weak differentiation, skill gap, privacy
  challenge, or implementation risk).
- Never write a rank justification like "ranks second because it is useful
  and feasible" -- always compare it to at least one other idea by name or
  clear reference.

Risk level rules:
- riskLevel must be exactly one of: "Low", "Medium", "High".
- Base it on dataset/API risk, missing skills, scope, and difficulty for that
  specific idea -- not a copy-pasted risk level.
{feedback_section}
Strict length limits (the response will be rejected and rewritten if these
are exceeded, so stay under them):
- contextSummary: max 18 words
- whyThisRank: max 32 words
- mainStrength: max 18 words
- mainRisk: max 18 words
- bestFor: max 18 words
- comparisonAdvantage: max 24 words
- requiredValidation: max 20 words
- recommendation: max 24 words
- summary: max two sentences
- finalRecommendation: max two sentences

Strict output rules:
- Return only valid JSON. No markdown, no code fences, no text outside JSON.
- ideas array must contain every idea provided in the input, each exactly once.
- Every idea must have a unique rank; ranks must be an exact 1..N permutation.
- Rank 1 must be the best idea, and bestIdeaId/bestIdeaTitle must match it.

Return exactly this JSON structure:

{{
  "comparisonTitle": "Best Project Match",
  "totalIdeasCompared": {len(request.ideas)},
  "bestIdeaId": 0,
  "bestIdeaTitle": "",
  "summary": "",
  "ideas": [
    {{
      "ideaId": 0,
      "rank": 1,
      "title": "",
      "contextSummary": "",
      "whyThisRank": "",
      "mainStrength": "",
      "mainRisk": "",
      "bestFor": "",
      "comparisonAdvantage": "",
      "requiredValidation": "",
      "riskLevel": "",
      "recommendation": ""
    }}
  ],
  "finalRecommendation": ""
}}
"""

    # =========================================================================
    # Completion / validation
    # =========================================================================

    def _complete_and_validate(
        self,
        request: IdeaComparisonRequest,
        raw: dict[str, Any],
    ) -> IdeaComparisonResponse:
        raw_ideas = raw.get("ideas")
        completed: list[ComparedIdeaQualitative] = []

        for idea in request.ideas:
            raw_result = self._find_raw_result(raw_ideas, idea)
            completed.append(self._complete_idea_result(request, idea, raw_result))

        # Preserve the model's own ranking (do not re-sort by a score we no
        # longer compute) -- just make it a clean 1..N permutation in rank
        # order, falling back to input order for any idea the model didn't
        # rank at all.
        completed.sort(key=lambda item: item.rank)

        ranked: list[ComparedIdeaQualitative] = []

        for index, item in enumerate(completed, start=1):
            ranked.append(item.model_copy(update={"rank": index}))

        best = ranked[0]

        return IdeaComparisonResponse(
            comparisonTitle=self._clean_text(raw.get("comparisonTitle"), "Best Project Match"),
            totalIdeasCompared=len(ranked),
            bestIdeaId=best.ideaId,
            bestIdeaTitle=best.title,
            summary=self._clean_text(
                raw.get("summary"),
                f"{best.title} is the strongest overall match for this student's profile.",
            ),
            ideas=ranked,
            finalRecommendation=self._clean_text(
                raw.get("finalRecommendation"),
                f"Select {best.title} after reviewing its required validation step.",
            ),
            available=True,
        )

    def _find_raw_result(self, raw_ideas: Any, idea: IdeaComparisonInput) -> dict[str, Any]:
        if not isinstance(raw_ideas, list):
            return {}

        for item in raw_ideas:
            if isinstance(item, dict) and self._to_int(item.get("ideaId"), -1) == idea.id:
                return item

        for item in raw_ideas:
            if isinstance(item, dict):
                raw_title = str(item.get("title") or "").strip().lower()

                if raw_title == idea.title.strip().lower():
                    return item

        return {}

    def _complete_idea_result(
        self,
        request: IdeaComparisonRequest,
        idea: IdeaComparisonInput,
        raw: dict[str, Any],
    ) -> ComparedIdeaQualitative:
        missing = "Not provided by the AI response."

        return ComparedIdeaQualitative(
            ideaId=idea.id,
            rank=self._to_int(raw.get("rank"), 999),
            title=idea.title,
            contextSummary=self._clean_text(raw.get("contextSummary"), missing),
            whyThisRank=self._clean_text(raw.get("whyThisRank"), missing),
            mainStrength=self._clean_text(raw.get("mainStrength"), missing),
            mainRisk=self._clean_text(raw.get("mainRisk"), missing),
            bestFor=self._clean_text(raw.get("bestFor"), self._fallback_best_for(request)),
            comparisonAdvantage=self._clean_text(raw.get("comparisonAdvantage"), missing),
            requiredValidation=self._clean_text(raw.get("requiredValidation"), missing),
            riskLevel=self._risk_level(raw.get("riskLevel"), idea),
            recommendation=self._clean_text(raw.get("recommendation"), missing),
        )

    def _fallback_best_for(self, request: IdeaComparisonRequest) -> str:
        if request.teamSize <= 1:
            return "A solo student who needs a focused, manageable final year project."

        return f"A {request.teamSize}-member team that can split implementation and testing work."

    def _risk_level(self, value: Any, idea: IdeaComparisonInput) -> str:
        text = str(value or "").strip().lower()

        if text == "low":
            return "Low"

        if text == "medium":
            return "Medium"

        if text == "high":
            return "High"

        # Deterministic completion only -- never prose -- for the rare case
        # the model omits/mistypes riskLevel for one idea.
        if self._has_real_data_risk(idea.datasetNeeded):
            return "Medium"

        difficulty = str(idea.difficultyLevel or "").lower()

        if difficulty in ["advanced", "hard", "5", "4"]:
            return "High"

        return "Medium"

    def _empty_response(self) -> IdeaComparisonResponse:
        return IdeaComparisonResponse(
            comparisonTitle="Idea Comparison",
            totalIdeasCompared=0,
            bestIdeaId=0,
            bestIdeaTitle="",
            summary="No generated ideas were provided for comparison.",
            ideas=[],
            finalRecommendation="Generate project ideas first, then compare them.",
            available=True,
        )

    def _safe_unavailable_response(self, request: IdeaComparisonRequest) -> IdeaComparisonResponse:
        """
        Compact, honest fallback (see class docstring) -- never fabricated
        idea-specific-looking text. The caller (Razor UI) is expected to
        show the saved score comparison table instead when available=False.
        """
        return IdeaComparisonResponse(
            comparisonTitle="Idea Comparison Unavailable",
            totalIdeasCompared=0,
            bestIdeaId=0,
            bestIdeaTitle="",
            summary=(
                "An idea-specific AI recommendation could not be generated. "
                "Use the saved score comparison below to evaluate your ideas."
            ),
            ideas=[],
            finalRecommendation=(
                "Use the saved score comparison below to evaluate your ideas."
            ),
            available=False,
        )

    # =========================================================================
    # Repetition / genericness detection
    # =========================================================================

    def _find_repetition_issues(
        self,
        result: IdeaComparisonResponse,
        request: IdeaComparisonRequest,
    ) -> list[int]:
        """
        Deterministic backstop (in addition to the semantic Reviewer's own
        "repetitive_or_generic_comparison" rubric check, see registry.py) --
        intentionally narrow, catching only near-verbatim copy-paste text
        (the original bug: a handful of fallback template strings reused
        across a whole batch). It does NOT flag two ideas for merely
        sharing a domain or tech stack -- FYPilot ideas routinely share
        "ASP.NET Core Razor Pages, Python FastAPI, PostgreSQL" and a
        "Web Development" domain by design (see ProjectIdeaAgent's allowed
        stack), so treating that as "generic" produced false positives on
        every real batch and forced a needless second LLM call every time.
        Only runs on the writer's raw output, before the semantic reviewer
        ever sees it.
        """
        if len(result.ideas) < 2:
            return []

        flagged: set[int] = set()

        for field in _REPETITIVE_TEXT_FIELDS:
            seen: list[tuple[int, str]] = []

            for idea in result.ideas:
                text = getattr(idea, field, "") or ""
                normalized = self._normalize_for_comparison(text)

                if not normalized:
                    continue

                for other_id, other_normalized in seen:
                    similarity = difflib.SequenceMatcher(
                        None, normalized, other_normalized,
                    ).ratio()

                    # High bar on purpose -- this catches literal or
                    # near-literal copy-paste between two different ideas,
                    # not two ideas that happen to read similarly because
                    # they share a domain/stack/risk level.
                    if similarity >= 0.92:
                        flagged.add(idea.ideaId)
                        flagged.add(other_id)

                seen.append((idea.ideaId, normalized))

        return sorted(flagged)

    @staticmethod
    def _normalize_for_comparison(text: str) -> str:
        lowered = text.lower().strip()
        return re.sub(r"[^a-z0-9 ]+", "", lowered)

    # =========================================================================
    # Small helpers
    # =========================================================================

    def _normalize_ideas(self, ideas: list[IdeaComparisonInput]) -> list[IdeaComparisonInput]:
        valid_ideas = [idea for idea in ideas if idea.title and idea.title.strip()]
        return valid_ideas[: self.max_ideas]

    def _to_int(self, value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return fallback

    def _clean_text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback

        text = str(value).strip()

        if not text:
            return fallback

        lowered = text.lower()

        if any(blocked in lowered for blocked in self.blocked_terms):
            return fallback

        return text

    def _split_items(self, text: str) -> list[str]:
        if not text:
            return []

        parts = re.split(r",|;|\n", text)
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _truncate(text: str | None, max_length: int, *, fallback: str = "") -> str:
        if not text or not text.strip():
            return fallback

        clean = text.strip()
        return clean if len(clean) <= max_length else clean[: max_length - 1].rstrip() + "…"

    def _has_real_data_risk(self, text: str) -> bool:
        lowered = str(text or "").lower().strip()

        if not lowered:
            return False

        no_data_phrases = ["no", "none", "no for mvp", "not required", "optional", "optional later"]

        if any(phrase in lowered for phrase in no_data_phrases):
            return False

        high_risk_terms = [
            "sensitive", "private", "medical records", "patient records",
            "financial records", "large dataset", "real hospital", "real clinic",
            "unavailable", "hard to collect", "requires approval",
        ]

        if any(term in lowered for term in high_risk_terms):
            return True

        required_terms = [
            "required", "must", "depends on", "training dataset",
            "historical data", "real-world data",
        ]

        return any(term in lowered for term in required_terms)
