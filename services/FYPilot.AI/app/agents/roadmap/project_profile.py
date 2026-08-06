"""
Deterministic project profiling for the roadmap generator.

Infers a project's type, risk characteristics, required lifecycle coverage,
and a duration-sensitive phase-count range from the request text -- using
word-boundary/token matching (see lexicon.py), never raw substring checks.
This profile drives:
  - the phase-count guidance given to the LLM prompt (replacing a fixed
    "4-7 phases" instruction that applied to every project regardless of
    duration or domain),
  - lifecycle coverage validation (project_roadmap_agent rejects/merges a
    phase plan that never mentions a mandatory lifecycle area for this
    project type),
  - the effort multiplier applied during PERT estimation for
    safety/security-sensitive work (see task_taxonomy.py).

No LLM call. Pure function of the request's own fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.roadmap import lexicon

# ---------------------------------------------------------------------------
# Project types
# ---------------------------------------------------------------------------

# Checked in this order; a project can match more than one (-> hybrid). Kept
# deliberately specific so a plain CRUD web app doesn't also light up as
# "ai_ml" just because its tech list mentions Python.
_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nlp": (
        "nlp", "natural language processing", "text classification",
        "sentiment analysis", "chatbot", "language model", "tokenization",
        "text generation", "named entity recognition", "text summarization",
        "arabic nlp", "speech to text", "machine translation",
    ),
    "computer_vision": (
        "computer vision", "cv", "image classification", "object detection",
        "image recognition", "opencv", "image segmentation", "facial recognition",
    ),
    "ai_ml": (
        "machine learning", "ml model", "deep learning", "neural network",
        "pytorch", "tensorflow", "scikit learn", "model training",
        "predictive model", "recommendation engine", "classification model",
    ),
    "data_science": (
        "data science", "data pipeline", "data visualization",
        "exploratory data analysis", "statistical analysis", "data mining",
    ),
    "mobile": (
        "mobile app", "mobile application", "android app", "ios app",
        "flutter", "react native", "cross platform mobile",
    ),
    "iot": (
        "iot", "internet of things", "embedded system", "arduino",
        "raspberry pi", "microcontroller", "firmware", "sensor network",
    ),
    "cybersecurity": (
        "cybersecurity", "penetration testing", "intrusion detection",
        "threat detection", "security audit", "vulnerability scanning",
        "malware", "security monitoring", "network security",
    ),
    "api_integration": (
        "api integration", "third party api", "integration platform",
        "webhook", "middleware platform", "data synchronization service",
    ),
}

_DEFAULT_TYPE = "web"


def _detect_project_types(combined_text: str) -> tuple[str, ...]:
    matched = [
        project_type
        for project_type, keywords in _TYPE_KEYWORDS.items()
        if lexicon.contains_any(combined_text, keywords)
    ]

    if not matched:
        return (_DEFAULT_TYPE,)

    return tuple(matched)


# ---------------------------------------------------------------------------
# Risk characteristics
# ---------------------------------------------------------------------------

_RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "external_api_dependency": (
        "third party api", "external api", "calendar api", "payment gateway",
        "external service", "google calendar", "payment integration",
    ),
    "dataset_dependency": (
        "dataset", "training data", "data collection", "corpus",
        "annotated data", "labeled data", "data annotation",
    ),
    "data_privacy": (
        "personal data", "privacy", "gdpr", "sensitive data",
        "patient data", "medical record", "confidential data",
    ),
    "medical_safety": (
        "medical", "health", "patient", "diagnosis", "triage", "clinical",
        "healthcare", "symptom", "treatment",
    ),
    "auth_security": (
        "authentication", "authorization", "login", "security", "encryption",
        "access control", "role based access",
    ),
    "hardware_dependency": (
        "hardware", "sensor", "arduino", "raspberry pi", "embedded",
        "firmware", "microcontroller", "actuator",
    ),
    "model_training_uncertainty": (
        "model training", "train a model", "machine learning",
        "deep learning", "neural network", "fine tuning",
    ),
    "deployment_complexity": (
        "deployment", "docker", "kubernetes", "ci cd", "production environment",
        "cloud hosting", "containerization",
    ),
}


def _detect_risks(combined_text: str, missing_skill_count: int) -> frozenset[str]:
    risks = {
        risk
        for risk, keywords in _RISK_KEYWORDS.items()
        if lexicon.contains_any(combined_text, keywords)
    }

    if missing_skill_count >= 2:
        risks.add("missing_skill_burden")

    return frozenset(risks)


# ---------------------------------------------------------------------------
# Lifecycle coverage
# ---------------------------------------------------------------------------

UNIVERSAL_MANDATORY_LIFECYCLE: tuple[str, ...] = (
    "requirements_scope",
    "architecture_design",
    "core_implementation",
    "integration",
    "testing_validation",
    "documentation",
    "final_delivery",
)

# Lifecycle category -> phrases that count as "this area is covered", checked
# against every phase's name + goal + task text combined. Word-boundary
# matching via lexicon, so "ui" only matches as a real token.
LIFECYCLE_EVIDENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "requirements_scope": ("requirement", "scope", "problem statement", "mvp", "feature list"),
    "architecture_design": ("architecture", "design", "wireframe", "schema", "entity", "database design"),
    "core_implementation": ("implement", "develop", "build the", "core workflow", "core feature"),
    "integration": ("integration", "integrate", "connect", "endpoint", "api"),
    "testing_validation": ("test", "testing", "validation", "qa", "bug fix"),
    "documentation": ("documentation", "user guide", "technical report", "write up", "report", "write-up"),
    "final_delivery": ("deployment", "submission", "presentation", "demo", "final package"),
    "data_sourcing": ("dataset", "data collection", "data sourcing", "corpus", "data acquisition"),
    "data_preprocessing": (
        "preprocessing", "preprocess", "data cleaning", "clean the data", "annotation", "annotate",
        "normalization", "normalize", "tokenization", "tokenize",
    ),
    "baseline_model": ("baseline model", "baseline", "initial model"),
    "model_evaluation": (
        "evaluation", "evaluate", "evaluating", "error analysis", "model performance", "metrics",
        "confusion matrix",
    ),
    "data_governance_privacy": (
        "privacy", "data governance", "consent", "anonymization", "licensing", "gdpr", "compliance",
    ),
    "safety_validation": (
        "safety", "supervisor validation", "domain validation", "clinical review", "expert review",
        "supervisor review", "safety validation", "clinical validation", "domain expert",
    ),
    "threat_modeling": ("threat model", "risk assessment", "attack surface"),
    "security_testing": ("security testing", "penetration test", "vulnerability", "security review"),
    "hardware_integration": ("hardware integration", "firmware", "sensor integration", "circuit"),
    "calibration_reliability": ("calibration", "reliability testing", "field testing", "stress test"),
}


def _lifecycle_for_profile(
    project_types: tuple[str, ...],
    risks: frozenset[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    mandatory = list(UNIVERSAL_MANDATORY_LIFECYCLE)
    optional: list[str] = []

    is_data_ai = any(t in project_types for t in ("ai_ml", "nlp", "computer_vision", "data_science"))

    if is_data_ai:
        mandatory += ["data_sourcing", "data_preprocessing", "baseline_model", "model_evaluation"]

    if "medical_safety" in risks:
        mandatory += ["data_governance_privacy", "safety_validation"]

    if "cybersecurity" in project_types:
        mandatory += ["threat_modeling", "security_testing"]

    if "iot" in project_types:
        mandatory += ["hardware_integration", "calibration_reliability"]

    if "data_privacy" in risks and "data_governance_privacy" not in mandatory:
        optional.append("data_governance_privacy")

    # De-dupe, preserve order.
    mandatory = list(dict.fromkeys(mandatory))
    optional = [item for item in dict.fromkeys(optional) if item not in mandatory]

    # Move safety/compliance-critical categories to right after
    # requirements_scope instead of leaving them at the end of the list.
    # Live testing showed that when a provider's response gets cut off
    # (token budget or transport truncation), the categories listed LAST in
    # the prompt are consistently the ones the model hadn't written a phase
    # for yet -- for a safety-sensitive project, safety_validation and
    # data_governance_privacy must not be the ones silently lost to that.
    _EARLY_PRIORITY = ("safety_validation", "data_governance_privacy")
    priority_present = [c for c in _EARLY_PRIORITY if c in mandatory]
    if priority_present:
        rest = [c for c in mandatory if c not in _EARLY_PRIORITY]
        insert_at = min(1, len(rest))
        mandatory = rest[:insert_at] + priority_present + rest[insert_at:]

    return tuple(mandatory), tuple(optional)


# ---------------------------------------------------------------------------
# Phase-count guidance
# ---------------------------------------------------------------------------

# (upper_weeks_bound, min_phases, max_phases) -- guidance bounds, not a
# blind template. Checked in ascending order of upper_weeks_bound.
_DURATION_PHASE_BANDS: tuple[tuple[int, int, int], ...] = (
    (6, 4, 5),
    (9, 5, 7),
    (12, 6, 8),
    (16, 7, 10),
    (20, 8, 11),
    (10_000, 9, 12),
)


def _phase_count_bounds(total_weeks: int, mandatory_lifecycle_count: int) -> tuple[int, int]:
    base_min, base_max = 4, 5
    for upper_bound, lo, hi in _DURATION_PHASE_BANDS:
        if total_weeks <= upper_bound:
            base_min, base_max = lo, hi
            break

    # Lifecycle coverage matters more than hitting a phase-count target: if
    # this project's mandatory areas genuinely need more distinct phases
    # than the duration band alone suggests, widen the upper bound rather
    # than forcing unrelated lifecycle areas into one umbrella phase --
    # never below the duration guidance, never above total_weeks (a phase
    # needs at least 1 week).
    widened_max = max(base_max, min(mandatory_lifecycle_count, total_weeks))

    phase_min = min(base_min, total_weeks)
    phase_max = max(phase_min, min(widened_max, total_weeks))

    return phase_min, phase_max


# ---------------------------------------------------------------------------
# Student readiness / skill gap
# ---------------------------------------------------------------------------


def _skill_gap_level(
    required_skill_count: int,
    missing_skill_count: int,
    average_rating: float | None,
) -> str:
    if required_skill_count == 0 and missing_skill_count == 0:
        return "low"

    missing_ratio = missing_skill_count / max(1, required_skill_count + missing_skill_count)

    if missing_ratio >= 0.5 or (average_rating is not None and average_rating < 2.0):
        return "high"

    if missing_ratio >= 0.2 or (average_rating is not None and average_rating < 3.5):
        return "moderate"

    return "low"


# ---------------------------------------------------------------------------
# Public profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectProfileInput:
    idea_title: str = ""
    problem_statement: str = ""
    required_technologies: str = ""
    required_skills: str = ""
    missing_skills: str = ""
    domain: str = ""
    final_deliverables: str = ""
    difficulty_level: str = "medium"
    total_weeks: int = 10
    team_size: int = 1
    hours_per_week: int = 10
    student_skills: list[str] = field(default_factory=list)
    skill_ratings: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectProfile:
    project_types: tuple[str, ...]
    primary_type: str
    risk_flags: frozenset[str]
    mandatory_lifecycle: tuple[str, ...]
    optional_lifecycle: tuple[str, ...]
    phase_count_min: int
    phase_count_max: int
    weekly_team_capacity: int
    missing_skill_count: int
    skill_gap_level: str
    is_safety_sensitive: bool
    is_security_sensitive: bool
    difficulty_level: str = "medium"


def build_profile(data: ProjectProfileInput) -> ProjectProfile:
    combined_text = " ".join(
        [
            data.idea_title,
            data.problem_statement,
            data.required_technologies,
            data.required_skills,
            data.domain,
            data.final_deliverables,
        ]
    )

    missing_skills = [item.strip() for item in _split(data.missing_skills) if item.strip()]
    required_skills = [item.strip() for item in _split(data.required_skills) if item.strip()]

    project_types = _detect_project_types(combined_text)
    risks = _detect_risks(combined_text, len(missing_skills))
    mandatory_lifecycle, optional_lifecycle = _lifecycle_for_profile(project_types, risks)
    phase_min, phase_max = _phase_count_bounds(data.total_weeks, len(mandatory_lifecycle))

    ratings = list(data.skill_ratings.values()) if data.skill_ratings else []
    average_rating = (sum(ratings) / len(ratings)) if ratings else None

    return ProjectProfile(
        project_types=project_types,
        primary_type=project_types[0],
        risk_flags=risks,
        mandatory_lifecycle=mandatory_lifecycle,
        optional_lifecycle=optional_lifecycle,
        phase_count_min=phase_min,
        phase_count_max=phase_max,
        weekly_team_capacity=max(1, data.team_size) * max(1, data.hours_per_week),
        missing_skill_count=len(missing_skills),
        skill_gap_level=_skill_gap_level(len(required_skills), len(missing_skills), average_rating),
        is_safety_sensitive="medical_safety" in risks,
        is_security_sensitive="cybersecurity" in project_types or "auth_security" in risks,
        difficulty_level=(data.difficulty_level or "medium").strip().lower(),
    )


def text_indicates_medical_safety(text: str) -> bool:
    """Used by the scheduler to decide whether the safety-sensitive effort
    surcharge applies, re-derived from the candidate's OWN narrative text
    (phase/task wording) rather than the original request -- so the same
    answer is reached whether this runs on the Writer's first candidate or
    a Rewrite pass, neither of which carries the original request forward."""
    return lexicon.contains_any(text, _RISK_KEYWORDS["medical_safety"])


def text_indicates_security_sensitivity(text: str) -> bool:
    return lexicon.contains_any(text, _RISK_KEYWORDS["auth_security"]) or lexicon.contains_any(
        text, _TYPE_KEYWORDS["cybersecurity"]
    )


def text_indicates_ai_ml(text: str) -> bool:
    return any(
        lexicon.contains_any(text, _TYPE_KEYWORDS[key])
        for key in ("ai_ml", "nlp", "computer_vision", "data_science")
    )


def text_indicates_external_api_risk(text: str) -> bool:
    return lexicon.contains_any(text, _RISK_KEYWORDS["external_api_dependency"])


def text_indicates_hardware_risk(text: str) -> bool:
    return (
        lexicon.contains_any(text, _RISK_KEYWORDS["hardware_dependency"])
        or lexicon.contains_any(text, _TYPE_KEYWORDS["iot"])
    )


# ---------------------------------------------------------------------------
# Contingency capacity policy
# ---------------------------------------------------------------------------
#
# A schedule that plans every available hour leaves no room for supervisor
# feedback, rework, or the normal slippage of student projects. For
# higher-risk profiles (advanced+AI, medical/safety-sensitive, an external
# API dependency, a hardware dependency, or a large skill gap) Python
# proactively reserves part of nominal capacity as contingency BEFORE
# deciding what fits -- never by inventing filler work, only by being more
# willing to defer optional/medium-priority scope earlier. The reported
# capacityHours/totalCapacityHours always stay the true nominal figure
# (team_size * hoursPerWeekPerMember * totalWeeks); only the internal
# deferral threshold is reduced for these profiles.
CONTINGENCY_RESERVE_FRACTION = 0.20  # middle of the requested 15-25% band

_ADVANCED_DIFFICULTY_LEVELS = {"advanced", "hard", "expert"}

# 3+ distinct skills to learn is used as a text-derivable proxy for "high
# missing-skill burden" during re-validation passes, where the original
# request (and its skillRatings/missingSkills fields) is not available --
# see project_roadmap_agent.ProjectProfile.skill_gap_level for the
# request-aware version used on the Writer's own first pass.
_HIGH_SKILL_GAP_DISTINCT_SKILLS_THRESHOLD = 3


def contingency_reserve_fraction(
    text: str,
    *,
    difficulty_level: str = "medium",
    distinct_skills_to_learn: int = 0,
    skill_gap_level: str | None = None,
) -> float:
    """
    Returns the fraction of nominal capacity to treat as contingency buffer
    for deferral purposes -- 0.0 for an ordinary project, otherwise
    CONTINGENCY_RESERVE_FRACTION. Derivable purely from roadmap narrative
    text (+ the small structural signals already available to both the
    agent's first pass and the schema's re-validation pass), so the same
    policy applies consistently whether this runs on the Writer's candidate
    or a Rewrite pass.
    """
    is_advanced = (difficulty_level or "").strip().lower() in _ADVANCED_DIFFICULTY_LEVELS
    is_ai = text_indicates_ai_ml(text)
    is_medical = text_indicates_medical_safety(text)
    is_external_api = text_indicates_external_api_risk(text)
    is_hardware = text_indicates_hardware_risk(text)
    is_high_skill_gap = (
        skill_gap_level == "high"
        or distinct_skills_to_learn >= _HIGH_SKILL_GAP_DISTINCT_SKILLS_THRESHOLD
    )

    triggers_reserve = (
        (is_advanced and is_ai)
        or is_medical
        or is_external_api
        or is_hardware
        or is_high_skill_gap
    )

    return CONTINGENCY_RESERVE_FRACTION if triggers_reserve else 0.0


# Utilization risk bands -- INTERNAL categorization only, used to choose
# warning text. Never assigned to the public scheduleFeasibility field,
# which must stay one of the existing "feasible" /
# "feasible_after_scope_reduction" / "over_capacity" values .NET already
# understands.
def utilization_risk_band(utilization_percentage: float) -> str:
    if utilization_percentage <= 80:
        return "comfortable"
    if utilization_percentage <= 85:
        return "feasible"
    if utilization_percentage <= 90:
        return "feasible_with_risk"
    if utilization_percentage <= 100:
        return "very_tight"
    return "over_capacity"


def _split(text: str) -> list[str]:
    if not text:
        return []
    import re

    return [part.strip() for part in re.split(r",|;|\n", text) if part.strip()]


def lifecycle_coverage(
    profile: ProjectProfile,
    phase_texts: list[str],
) -> tuple[list[str], list[str]]:
    """
    Returns (covered, missing_mandatory) lifecycle category names. A
    category counts as covered if ANY phase's combined name+goal+task text
    matches one of its evidence phrases (word-boundary matched).
    """
    combined = " ".join(phase_texts)
    covered = [
        category
        for category in profile.mandatory_lifecycle + profile.optional_lifecycle
        if lexicon.contains_any(combined, LIFECYCLE_EVIDENCE_KEYWORDS.get(category, ()))
    ]
    missing_mandatory = [
        category for category in profile.mandatory_lifecycle if category not in covered
    ]
    return covered, missing_mandatory
