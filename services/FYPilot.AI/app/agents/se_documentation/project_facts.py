"""
Canonical ProjectFacts -- the single source of truth every SE documentation
section is built from.

Root cause this fixes: SEDocumentationOrchestratorAgent used to make 5
independent LLM calls that each re-read the raw request fields, then
_assemble_documentation() hardcoded architecture/scope/stakeholders/risks/
diagrams describing FYPilot itself (its own tech stack, its own "Idea
Generation Module"/"Roadmap Module" pipeline, its own Supervisor/Evaluation
committee actors) regardless of what project the student actually selected.

ProjectFacts is built once, deterministically, with no LLM call, from the
same SEDocumentationRequest every section already receives. Every prompt and
every deterministic assembly step reads from this structure instead of
re-deriving its own interpretation of the project, and every fact is
classified so the document can honestly label what it knows vs. what it
assumed.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field

_AI_KEYWORDS = [
    "artificial intelligence", "machine learning", " ml ", "ml-", "deep learning",
    "neural network", "nlp", "natural language", "chatbot", "chat bot",
    "llm", "large language model", "recommend", "recommendation", "predict",
    "prediction", "forecast", "classification", "classifier", "clustering",
    "computer vision", "image recognition", "sentiment", "data science",
    "generative ai", "genai", "rag", "retrieval augmented",
]

_DEFAULT_FRONTEND_KEYWORDS = ["razor", "react", "angular", "vue", "blazor", "flutter", "android", "ios", "swift", "kotlin", "html", "css", "bootstrap", "tailwind", "next.js", "nextjs"]
_DEFAULT_BACKEND_KEYWORDS = ["asp.net", "fastapi", "django", "flask", "node", "express", "spring", "laravel", ".net", "nestjs"]
_DEFAULT_DB_KEYWORDS = ["postgres", "postgresql", "mysql", "sql server", "mongodb", "sqlite", "mariadb", "firebase", "redis", "oracle"]


class FactItem(BaseModel):
    """A single fact plus its confidence classification."""

    value: str
    classification: str = "confirmed"  # confirmed | inferred | assumption | unknown


class TechnicalProfile(BaseModel):
    """
    One canonical technical interpretation of the project, computed once and
    shared by every section (requirements, architecture, use cases, AI
    report, diagrams). Fixes the "mixed AI architecture" and "JWT + Identity
    both mentioned" bugs, where independent LLM calls each invented their own
    AI approach / authentication mechanism instead of reading a single
    normalized answer.
    """

    frontend: str = ""
    backend: str = ""
    database: str = ""
    authentication_mechanism: str = "not confirmed"
    ai_enabled: bool = False
    ai_approach: str = "none"  # none|rule_based|intent_classification|retrieval_based|llm_api|rag|hybrid|unresolved
    ai_provider_type: str = "none"  # none|external_api|local_model|unresolved
    training_mode: str = "no_local_training"  # no_local_training|local_supervised_training|unresolved
    retrieval_mode: str = "none"  # none|knowledge_base_lookup|vector_retrieval|unresolved
    confirmed_integrations: List[str] = Field(default_factory=list)
    confirmed_roles: List[str] = Field(default_factory=list)
    confirmed_modules: List[str] = Field(default_factory=list)
    confirmed_data_stores: List[str] = Field(default_factory=list)
    technical_assumptions: List[str] = Field(default_factory=list)
    unresolved_technical_decisions: List[str] = Field(default_factory=list)


class ProjectFacts(BaseModel):
    title: str
    problem: str
    solution: str
    objectives: List[str] = Field(default_factory=list)
    domain: str = "General software"
    difficulty: str = "medium"
    duration_weeks: int = 10
    team_size: int = 1
    student_skills: List[str] = Field(default_factory=list)
    technologies: List[FactItem] = Field(default_factory=list)
    primary_actor: str = "User"
    supporting_actors: List[str] = Field(default_factory=list)
    ai_involved: bool = False
    ai_classification: str = "unknown"  # confirmed | inferred | unknown
    has_external_integration: bool = False
    roadmap_modules: List[str] = Field(default_factory=list)
    final_deliverables: str = ""
    assumptions: List[str] = Field(default_factory=list)
    technical_profile: TechnicalProfile = Field(default_factory=TechnicalProfile)

    def technology_names(self) -> List[str]:
        return [t.value for t in self.technologies]

    def confirmed_technology_names(self) -> List[str]:
        return [t.value for t in self.technologies if t.classification == "confirmed"]


def _looks_like_ai(*texts: str) -> bool:
    blob = " " + " ".join(t.lower() for t in texts if t) + " "
    return any(keyword in blob for keyword in _AI_KEYWORDS)


def _split_technologies(value: str) -> List[str]:
    if not value or not value.strip():
        return []
    items = [item.strip() for item in re.split(r"[,;\n/]+", value) if item.strip()]
    return items


def _looks_like_integration(*texts: str) -> bool:
    blob = " " + " ".join(t.lower() for t in texts if t) + " "
    keywords = ["api", "integration", "payment", "third-party", "third party", "webhook", "external service", "sms", "email service", "gateway"]
    return any(keyword in blob for keyword in keywords)


# ---------------------------------------------------------------------------
# Deterministic AI-approach / authentication classifiers -- one canonical
# answer computed once from confirmed facts, instead of every LLM call
# (requirements, architecture, AI report) independently inventing its own
# mix of intent classification / RAG / fine-tuning / JWT.
# ---------------------------------------------------------------------------

def _classify_ai_approach(blob: str, ai_involved: bool) -> tuple[str, str, str, str, List[str]]:
    """Returns (ai_approach, ai_provider_type, training_mode, retrieval_mode, assumptions)."""
    assumptions: List[str] = []

    if not ai_involved:
        return "none", "none", "no_local_training", "none", assumptions

    lowered = blob.lower()
    has_intent = "intent" in lowered
    has_training_phrase = "training phrase" in lowered
    has_rag = "retrieval augmented" in lowered or " rag" in f" {lowered}" or "vector database" in lowered or "vector store" in lowered
    has_external_api = any(p in lowered for p in ["openai", "gpt-", "chatgpt", "external llm", "llm api", "anthropic", "claude api"])
    has_finetune = "fine-tun" in lowered or "finetun" in lowered
    has_kb = "knowledge base" in lowered or "faq" in lowered

    if has_rag and (has_external_api or has_finetune):
        approach = "hybrid"
    elif has_rag:
        approach = "rag"
    elif has_intent or has_training_phrase:
        approach = "intent_classification"
    elif has_external_api:
        approach = "llm_api"
    elif has_kb:
        approach = "retrieval_based"
    else:
        approach = "unresolved"
        assumptions.append(
            "The specific AI/NLP approach (intent classification, retrieval, or an external "
            "LLM API) was not explicitly confirmed; it is marked unresolved pending student input."
        )

    provider_type = "external_api" if (has_external_api or approach in ("llm_api", "hybrid")) else (
        "local_model" if approach in ("intent_classification", "retrieval_based", "rag") else "unresolved"
    )

    if has_finetune and not has_external_api:
        training_mode = "local_supervised_training"
    elif approach == "intent_classification" and has_training_phrase:
        training_mode = "local_supervised_training"
    elif approach in ("llm_api",):
        training_mode = "no_local_training"
    elif approach == "unresolved":
        training_mode = "unresolved"
        assumptions.append("Whether any local model training/fitting occurs was not confirmed.")
    else:
        training_mode = "no_local_training"

    if has_rag or "vector" in lowered:
        retrieval_mode = "vector_retrieval"
    elif has_kb:
        retrieval_mode = "knowledge_base_lookup"
    elif approach == "unresolved":
        retrieval_mode = "unresolved"
    else:
        retrieval_mode = "none"

    return approach, provider_type, training_mode, retrieval_mode, assumptions


def _classify_authentication(blob: str) -> tuple[str, List[str]]:
    lowered = blob.lower()
    assumptions: List[str] = []

    if "sso" in lowered or "single sign-on" in lowered or "university credential" in lowered:
        return "external university SSO", assumptions
    if "jwt" in lowered:
        return "JWT bearer authentication", assumptions
    if "asp.net" in lowered or "identity" in lowered:
        return "ASP.NET Core Identity with cookie authentication", assumptions
    if "session" in lowered:
        return "custom session authentication", assumptions

    assumptions.append(
        "No specific authentication mechanism was confirmed; the authentication workflow "
        "is proposed rather than confirmed."
    )
    return "not confirmed", assumptions


# Keyword -> (entity name, purpose) coverage table used to guarantee the
# database section actually contains the entities a confirmed feature
# requires (e.g. an escalation feature always gets a SupportTicket entity),
# instead of relying entirely on an independent LLM call to remember to
# include it.
ENTITY_FEATURE_RULES: List[tuple[List[str], List[tuple[str, str]]]] = [
    (["knowledge base", "faq", "article"], [("KnowledgeArticle", "Stores knowledge-base articles/FAQ entries used to answer queries.")]),
    (["intent"], [("Intent", "Stores classifiable intents used to interpret incoming queries.")]),
    (["training phrase"], [("TrainingPhrase", "Stores example phrases used to train or match an intent.")]),
    (["escalat", "support ticket", "ticket"], [("SupportTicket", "Stores escalated support tickets requiring staff follow-up.")]),
    (["feedback", "rating"], [("ResponseFeedback", "Stores user feedback/ratings on system responses.")]),
    (["unanswered", "review queue"], [("UnansweredQuery", "Stores queries the system could not confidently answer, queued for review.")]),
    (["role", "permission", "staff", "admin"], [("Role", "Stores role definitions used for authorization.")]),
    (["conversation", "chatbot", "chat widget", "chat interface", "chat message"], [("Conversation", "Stores a conversation/session between an actor and the system."), ("Message", "Stores individual messages within a conversation.")]),
    (["setting", "configuration", "threshold"], [("SystemSetting", "Stores configurable system thresholds/settings.")]),
    (["log", "analytics", "query log"], [("QueryLog", "Stores a record of processed queries/interactions for analytics and review.")]),
]

# Keyword -> screen coverage table, mirroring ENTITY_FEATURE_RULES, so a
# confirmed feature always gets a real user-facing screen instead of only
# the 3-4 generic screens the deterministic fallback used to produce.
SCREEN_FEATURE_RULES: List[tuple[List[str], List[tuple[str, str]]]] = [
    (["knowledge base management", "manage knowledge", "manage faq", "manage article"], [("Knowledge Base Management", "Create, edit, and organize knowledge-base articles/FAQ entries.")]),
    (["knowledge base", "faq"], [("Knowledge Base Browser", "Browse and search knowledge-base articles/FAQ entries.")]),
    (["unanswered", "review queue"], [("Unanswered Query Queue", "Review and resolve queries the system could not confidently answer.")]),
    (["support ticket", "escalat"], [("Support Ticket Submission", "Submit a new support ticket for escalation."), ("Ticket Tracking", "Track the status of submitted support tickets.")]),
    (["feedback", "rating"], [("Feedback Controls", "Submit feedback/rating on a system response.")]),
    (["analytics", "dashboard", "report"], [("Analytics Dashboard", "View usage analytics and reporting.")]),
    (["setting", "configuration", "threshold"], [("System Configuration", "Configure system thresholds/settings.")]),
    (["role", "permission", "user management"], [("User and Role Management", "Manage users and their assigned roles.")]),
]


def _match_feature_rules(blob: str, rules: List[tuple[List[str], List[tuple[str, str]]]]) -> List[tuple[str, str]]:
    lowered = blob.lower()
    matched: dict[str, str] = {}
    for keywords, items in rules:
        if any(keyword in lowered for keyword in keywords):
            for name, purpose in items:
                matched.setdefault(name, purpose)
    return list(matched.items())


def required_entities_for_text(*texts: str) -> List[tuple[str, str]]:
    return _match_feature_rules(" ".join(t for t in texts if t), ENTITY_FEATURE_RULES)


def required_screens_for_text(*texts: str) -> List[tuple[str, str]]:
    return _match_feature_rules(" ".join(t for t in texts if t), SCREEN_FEATURE_RULES)


def build_project_facts(request) -> ProjectFacts:
    """
    Pure, deterministic. Never calls an LLM. `request` is a
    SEDocumentationRequest (duck-typed here to avoid a circular import).
    """
    profile = getattr(request, "studentProfile", None)
    idea = getattr(request, "selectedIdea", None)
    roadmap = getattr(request, "roadmap", None) or []

    assumptions: List[str] = []

    title = (getattr(idea, "title", "") or "").strip()
    if not title:
        title = "Untitled Final Year Project"
        assumptions.append("No project title was provided; the document uses a placeholder title.")

    problem = (getattr(idea, "problemStatement", "") or "").strip()
    if not problem:
        problem = (
            f"A specific problem statement was not provided for '{title}'. "
            "This document proceeds using the project title and domain only, "
            "and every requirement derived without a confirmed problem statement "
            "is labeled as an assumption."
        )
        assumptions.append("No problem statement was provided; downstream sections assume a generic problem framing for this domain.")

    why_useful = (getattr(idea, "whyUseful", "") or "").strip()
    target_users = (getattr(idea, "targetUsers", "") or "").strip()
    solution = why_useful or (
        f"{title} is expected to address the stated problem for {target_users or 'its target users'}, "
        "using the confirmed technologies and features described in this document."
    )
    if not why_useful:
        assumptions.append("No explicit 'why useful' / solution summary was provided; the solution summary above was derived from the title and target users.")

    domain = (getattr(idea, "domain", "") or "").strip() or "General software"
    difficulty = (getattr(idea, "difficultyLevel", "") or "").strip() or "medium"
    duration_weeks = getattr(idea, "expectedDurationWeeks", 0) or 10
    final_deliverables = (getattr(idea, "finalDeliverables", "") or "").strip()

    team_size = getattr(profile, "teamSize", None) or 1
    student_skills = list(getattr(profile, "skills", None) or [])

    raw_tech = (getattr(idea, "requiredTechnologies", "") or "").strip()
    tech_names = _split_technologies(raw_tech)
    technologies: List[FactItem] = []
    if tech_names:
        technologies = [FactItem(value=t, classification="confirmed") for t in tech_names]
    else:
        technologies = [
            FactItem(value="A web application frontend (exact framework not specified)", classification="assumption"),
            FactItem(value="A server-side application backend (exact framework not specified)", classification="assumption"),
            FactItem(value="A relational or document database (exact engine not specified)", classification="assumption"),
        ]
        assumptions.append("No technology stack was confirmed by the student; a generic web application stack was assumed instead of inventing specific frameworks.")

    primary_actor = target_users.split(",")[0].strip() if target_users else ""
    if not primary_actor:
        primary_actor = "End User"
        assumptions.append("No target users were specified; 'End User' is used as the primary actor placeholder.")

    supporting_actors: List[str] = []
    if team_size and team_size > 0:
        supporting_actors.append("System Administrator")

    ai_text_blob = " ".join([title, problem, solution, domain, raw_tech, final_deliverables, why_useful])
    ai_involved = _looks_like_ai(ai_text_blob)
    ai_classification = "confirmed" if ai_involved else "unknown"

    has_external_integration = _looks_like_integration(ai_text_blob)

    roadmap_modules = [
        (getattr(phase, "name", "") or "").strip()
        for phase in roadmap
        if (getattr(phase, "name", "") or "").strip()
    ]

    objectives: List[str] = []
    if final_deliverables:
        objectives = [item.strip() for item in re.split(r"[,;\n]+", final_deliverables) if item.strip()]
    if not objectives:
        objectives = [f"Deliver a working {title} that addresses the stated problem for {primary_actor}."]
        assumptions.append("No final deliverables were confirmed; a single generic objective was derived from the title instead.")

    full_blob = " ".join([title, problem, solution, domain, raw_tech, final_deliverables, why_useful, target_users])

    ai_approach, ai_provider_type, training_mode, retrieval_mode, ai_assumptions = _classify_ai_approach(
        full_blob, ai_involved
    )
    authentication_mechanism, auth_assumptions = _classify_authentication(full_blob)

    technical_assumptions = list(ai_assumptions) + list(auth_assumptions)
    unresolved_technical_decisions = [
        a for a in technical_assumptions
        if "unresolved" in a.lower() or "not confirmed" in a.lower() or "proposed" in a.lower()
    ]

    technical_profile = TechnicalProfile(
        frontend="",
        backend="",
        database="",
        authentication_mechanism=authentication_mechanism,
        ai_enabled=ai_involved,
        ai_approach=ai_approach,
        ai_provider_type=ai_provider_type,
        training_mode=training_mode,
        retrieval_mode=retrieval_mode,
        confirmed_integrations=[],
        confirmed_roles=[],
        confirmed_modules=[],
        confirmed_data_stores=[],
        technical_assumptions=technical_assumptions,
        unresolved_technical_decisions=unresolved_technical_decisions,
    )
    assumptions.extend(technical_assumptions)

    return ProjectFacts(
        title=title,
        problem=problem,
        solution=solution,
        objectives=objectives,
        domain=domain,
        difficulty=difficulty,
        duration_weeks=duration_weeks,
        team_size=team_size,
        student_skills=student_skills,
        technologies=technologies,
        primary_actor=primary_actor,
        supporting_actors=supporting_actors,
        ai_involved=ai_involved,
        ai_classification=ai_classification,
        has_external_integration=has_external_integration,
        roadmap_modules=roadmap_modules,
        final_deliverables=final_deliverables,
        assumptions=assumptions,
        technical_profile=technical_profile,
    )


def facts_context_text(facts: ProjectFacts) -> str:
    """
    The single canonical context block injected into every LLM section
    prompt, so every section reads the same facts instead of each call
    re-deriving its own interpretation of the project.
    """
    tech_lines = "\n".join(
        f"- {t.value} ({t.classification})" for t in facts.technologies
    )
    modules_line = ", ".join(facts.roadmap_modules) or "Not provided"
    profile = facts.technical_profile

    return f"""
CANONICAL TECHNICAL PROFILE (the ONE interpretation every section must use --
do not describe a different AI approach or authentication mechanism anywhere
in your output):
AI approach: {profile.ai_approach}
AI provider type: {profile.ai_provider_type}
Training mode: {profile.training_mode}
Retrieval mode: {profile.retrieval_mode}
Authentication mechanism: {profile.authentication_mechanism}
- If AI approach is "none", do not mention any AI/ML/NLP capability anywhere.
- If AI approach is "llm_api", say inference uses a pretrained external model API and
  explicitly do NOT say a model was "trained" or "fine-tuned".
- If AI approach is "intent_classification", describe intent classification with
  knowledge-base lookup -- do NOT mention retrieval-augmented generation (RAG) or
  fine-tuned pretrained LLMs unless the approach above is "rag" or "hybrid".
- If authentication mechanism is "ASP.NET Core Identity with cookie authentication", do
  NOT mention JWT tokens anywhere. If it is "not confirmed", describe the authentication
  workflow as proposed, not confirmed.

CANONICAL PROJECT FACTS (do not contradict these; do not invent facts beyond them):
Project title: {facts.title}
Domain: {facts.domain}
Difficulty: {facts.difficulty}
Team size: {facts.team_size}
Duration (weeks): {facts.duration_weeks}
Problem statement: {facts.problem}
Proposed solution: {facts.solution}
Objectives: {"; ".join(facts.objectives)}
Primary actor: {facts.primary_actor}
Supporting actors: {", ".join(facts.supporting_actors) or "None confirmed"}
Confirmed/assumed technologies:
{tech_lines}
AI/ML/NLP/data-science component involved: {"yes" if facts.ai_involved else "no"}
External API/integration involved: {"yes" if facts.has_external_integration else "no"}
Roadmap-derived modules: {modules_line}
Student skills: {", ".join(facts.student_skills) or "Not provided"}

RULES:
- Every requirement, use case, module, entity, screen, and diagram element must be
  specific to THIS project (title/domain/actors above), never a generic template and
  never a description of a different system.
- Do not invent features, actors, technologies, or integrations that are not listed
  above or clearly implied by the problem/solution text.
- If you must assume something not explicitly confirmed above, set that item's
  "sourceClassification" field to "assumption" (or "inferred") rather than "confirmed".
"""
