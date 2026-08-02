"""
Task-type taxonomy: classification, PERT effort estimation, dependency stage
tiering, priority classification, and duplicate/vague-task detection for
roadmap tasks.

Effort estimation deliberately does NOT use any hash of the task text (the
old `_stable_fraction(title)` approach). A task's (min, likely, max) hour
range is looked up purely from its deterministically classified TASK TYPE
(word-boundary keyword classification of the title, see classify_task_type),
then combined into a single expected value via the standard PERT formula:

    expected = (min + 4*likely + max) / 6

This means two different tasks of the same type always get the same base
estimate regardless of exact wording -- "same title always yields the same
hours" holds trivially, and so does "same TYPE always yields the same base
hours", which is what makes the estimate explainable (traceable to one of
the named categories below, not to an opaque hash of the sentence).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.agents.roadmap import lexicon

EFFECTIVE_HOURS_PER_DAY = 6


def compute_working_days(hours: int) -> int:
    """The single deterministic estimatedHours -> estimatedWorkingDays rule,
    reused everywhere so a task's two effort figures can never contradict
    each other. Documented assumption: 6 productive hours per working day."""
    return max(1, math.ceil(hours / EFFECTIVE_HOURS_PER_DAY))


# ─────────────────────────────────────────────────────────────────────────
# Task type taxonomy: (stage tier for dependency ordering, PERT hour range)
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskType:
    key: str
    stage: int  # lower = earlier in the natural build order (see dependency_graph.py)
    hours: tuple[int, int, int]  # (min, likely, max)
    legacy_complexity: str  # kept for the public RoadmapTask.complexity field


TASK_TYPES: dict[str, TaskType] = {
    "requirements_research": TaskType("requirements_research", 0, (2, 4, 8), "simple"),
    "setup_config": TaskType("setup_config", 1, (2, 4, 6), "simple"),
    "database_design": TaskType("database_design", 1, (4, 8, 14), "medium"),
    "data_acquisition": TaskType("data_acquisition", 1, (4, 10, 20), "medium"),
    "data_cleaning_annotation": TaskType("data_cleaning_annotation", 2, (6, 12, 24), "medium"),
    "baseline_model": TaskType("baseline_model", 2, (8, 16, 28), "complex"),
    "simple_ui": TaskType("simple_ui", 2, (3, 6, 10), "small"),
    "crud_api": TaskType("crud_api", 2, (4, 8, 14), "small"),
    "standard_feature": TaskType("standard_feature", 2, (8, 14, 24), "medium"),
    "complex_workflow": TaskType("complex_workflow", 3, (14, 24, 40), "complex"),
    "model_training_tuning": TaskType("model_training_tuning", 3, (12, 24, 48), "complex"),
    "external_api_integration": TaskType("external_api_integration", 3, (8, 16, 28), "complex"),
    "security": TaskType("security", 3, (6, 12, 20), "medium"),
    "model_evaluation": TaskType("model_evaluation", 4, (6, 12, 20), "medium"),
    "user_testing": TaskType("user_testing", 5, (4, 8, 14), "testing"),
    "functional_testing": TaskType("functional_testing", 5, (4, 10, 18), "testing"),
    "deployment": TaskType("deployment", 6, (4, 8, 16), "medium"),
    "documentation_presentation": TaskType("documentation_presentation", 6, (3, 6, 12), "simple"),
}

_DEFAULT_TASK_TYPE = "standard_feature"

# Checked in this order -- first match wins. Domain-specific/high-signal
# categories are listed before generic ones (e.g. "user testing" before
# generic "testing", "deployment" before generic "setup").
_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("requirements_research", ("requirement", "scope definition", "literature review",
                                "feasibility study", "user research", "problem statement")),
    ("data_acquisition", ("dataset", "data collection", "data sourcing", "corpus",
                           "data acquisition", "gather data", "scrape", "data governance")),
    ("data_cleaning_annotation", ("data cleaning", "annotation", "preprocessing",
                                   "normalization", "tokenization", "labeling",
                                   "data labeling", "data quality")),
    ("baseline_model", ("baseline model", "baseline", "initial model", "proof of concept model")),
    ("model_training_tuning", ("model training", "train the model", "fine tuning",
                                "hyperparameter", "model tuning", "retrain", "training pipeline")),
    ("model_evaluation", ("model evaluation", "error analysis", "evaluate the model",
                           "confusion matrix", "performance metrics", "validation accuracy",
                           "evaluation metrics")),
    ("database_design", ("database schema", "database design", "entity relationship",
                          "data model", "table design", "schema design")),
    ("external_api_integration", ("api integration", "external api", "third party api",
                                   "oauth", "payment gateway", "webhook", "calendar api",
                                   "provider fallback", "integrate with")),
    ("security", ("security", "authentication", "authorization", "encryption",
                  "access control", "penetration test", "vulnerability", "threat model")),
    ("user_testing", ("user testing", "usability testing", "user acceptance", "beta test",
                       "pilot test", "supervisor validation", "domain expert validation")),
    ("functional_testing", ("test", "testing", "qa ", "bug fix", "debug", "stabiliz",
                             "failure case", "regression test")),
    ("deployment", ("deployment", "deploy", "production release", "submission package",
                     "hosting", "publish", "release build")),
    ("documentation_presentation", ("documentation", "user guide", "technical report",
                                     "presentation", "demo script", "write up", "final report")),
    ("setup_config", ("setup", "configure", "initialize", "install", "environment setup",
                       "project structure", "wire up", "register", "scaffold")),
    ("complex_workflow", ("business logic", "core feature", "main feature",
                           "recommendation engine", "booking logic", "multi step process",
                           "core workflow", "primary workflow", "end to end workflow")),
    ("crud_api", ("endpoint", "api route", "crud", "rest api", "controller", "service layer")),
    ("simple_ui", ("page", "form", "screen", "layout", "view", "ui", "wireframe")),
)


def classify_task_type(title: str, phase_name: str = "") -> str:
    combined = f"{title} {phase_name}"

    for type_key, keywords in _TYPE_KEYWORDS:
        if lexicon.contains_any(combined, keywords):
            return type_key

    return _DEFAULT_TASK_TYPE


def task_stage(type_key: str) -> int:
    return TASK_TYPES.get(type_key, TASK_TYPES[_DEFAULT_TASK_TYPE]).stage


def classify_task_complexity(title: str, phase_name: str = "") -> str:
    type_key = classify_task_type(title, phase_name)
    return TASK_TYPES[type_key].legacy_complexity


# ─────────────────────────────────────────────────────────────────────────
# Explainable PERT effort estimation
# ─────────────────────────────────────────────────────────────────────────

# Small, documented, capped multipliers -- applied on top of the task
# type's own PERT expected value, never replacing it. Kept intentionally
# few: everything else that would plausibly justify "more hours" (higher
# inherent complexity, external dependency risk) is already baked into the
# task-type's own (min, likely, max) range above, so it is not multiplied
# again here.
_DIFFICULTY_MULTIPLIER: dict[str, float] = {
    "beginner": 0.90,
    "easy": 0.90,
    "medium": 1.00,
    "intermediate": 1.00,
    "advanced": 1.15,
    "hard": 1.15,
    "expert": 1.25,
}
_SAFETY_SENSITIVE_MULTIPLIER = 1.10
_SECURITY_SENSITIVE_MULTIPLIER = 1.10
# Widened from the original 1.35 to make room for the scope multiplier
# below (up to x1.6 on its own) stacking with difficulty/safety/security --
# still a hard, documented ceiling/floor so no combination of signals can
# produce an unbounded estimate.
_MAX_COMBINED_MULTIPLIER = 2.0
_MIN_COMBINED_MULTIPLIER = 0.7

_SAFETY_RELEVANT_TYPES = {"user_testing", "functional_testing", "model_evaluation", "security"}
_SECURITY_RELEVANT_TYPES = {"security"}


def pert_expected_hours(min_hours: float, likely_hours: float, max_hours: float) -> float:
    """Standard PERT expected-value formula: (min + 4*likely + max) / 6."""
    return (min_hours + 4 * likely_hours + max_hours) / 6


# ─────────────────────────────────────────────────────────────────────────
# Scope-sensitive effort: a task's effort depends on measurable scope
# (record counts, page counts, endpoint counts, role counts, model-variant
# counts), not only its category. Detected from an explicit scope
# description if one is available (see task_metadata.py), otherwise scanned
# directly from the task title -- so this applies even to fallback/
# rewrite-produced tasks that carry no structured metadata at all.
# ─────────────────────────────────────────────────────────────────────────

# (label, pattern, ascending (upper_bound, multiplier) bands -- first band
# whose upper_bound the detected number falls at-or-under wins). "[\s-]*"
# (not just whitespace) so hyphenated forms like "12-page" also match.
_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern, tuple[tuple[float, float], ...]], ...] = (
    (
        "records",
        re.compile(r"(\d[\d,]*)\+?[\s-]*(?:records?|rows?|samples?|entries|data[\s-]*points?)\b"),
        ((500, 0.85), (2000, 1.0), (10000, 1.3), (float("inf"), 1.6)),
    ),
    (
        "pages",
        re.compile(r"(\d[\d,]*)\+?[\s-]*(?:pages?|screens?)\b"),
        ((3, 0.85), (8, 1.0), (15, 1.3), (float("inf"), 1.6)),
    ),
    (
        "endpoints",
        re.compile(r"(\d[\d,]*)\+?[\s-]*(?:endpoints?|routes?|api[\s-]*routes?)\b"),
        ((2, 0.85), (5, 1.0), (10, 1.3), (float("inf"), 1.6)),
    ),
    (
        "roles",
        re.compile(r"(\d[\d,]*)\+?[\s-]*(?:user[\s-]*roles?|roles?)\b"),
        ((1, 0.85), (3, 1.0), (float("inf"), 1.25)),
    ),
    (
        "models",
        re.compile(r"(\d[\d,]*)\+?[\s-]*(?:models?|model[\s-]*architectures?|model[\s-]*families)\b"),
        ((1, 1.0), (3, 1.3), (float("inf"), 1.6)),
    ),
)

_QUALITATIVE_MULTI_MODEL_PHRASES = (
    "multiple model families", "several models", "multiple models",
    "several model families", "compare several architectures",
    "compare multiple architectures",
)

# Small English number words spelled out in prose (e.g. "four roles") --
# substituted for their digit form before scanning _SCOPE_PATTERNS, since
# "one endpoint" and "1 endpoint" must be treated identically.
_WORD_NUMBERS: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "dozen": "12",
}
_WORD_NUMBER_PATTERN = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS.keys()) + r")\b"
)


def _spell_out_numbers_as_digits(text: str) -> str:
    return _WORD_NUMBER_PATTERN.sub(lambda m: _WORD_NUMBERS[m.group(1)], text)


def _band_multiplier(value: float, bands: tuple[tuple[float, float], ...]) -> float:
    for upper_bound, multiplier in bands:
        if value <= upper_bound:
            return multiplier
    return bands[-1][1]


def detect_scope_multiplier(scope_hint: str, title: str) -> tuple[float, str]:
    """
    Deterministic scope-sensitivity multiplier. Scans `scope_hint` (an
    explicit scopeSize description, if available) first, then the task
    title itself, for a number + unit pattern (records, pages, endpoints,
    roles, model variants) and maps it to a documented multiplier band --
    e.g. annotating 200 records is not the same task as annotating 10,000.
    Returns (multiplier, explanation); multiplier is 1.0 with an explicit
    "no scope indicator" explanation when nothing is found, so callers can
    record an honest assumption instead of silently guessing.
    """
    for source in (scope_hint, title):
        if not source:
            continue

        lowered = _spell_out_numbers_as_digits(source.lower())

        for _label, pattern, bands in _SCOPE_PATTERNS:
            match = pattern.search(lowered)
            if match:
                value = float(match.group(1).replace(",", ""))
                multiplier = _band_multiplier(value, bands)
                return multiplier, f"scope detected ({match.group(0).strip()}) -> x{multiplier}"

        if lexicon.contains_any(source, _QUALITATIVE_MULTI_MODEL_PHRASES):
            return 1.3, "scope detected (multiple model variants mentioned) -> x1.3"

    return 1.0, "no explicit scope indicator found; used the standard estimate for this task type"


# ─────────────────────────────────────────────────────────────────────────
# Validate-and-clamp: the ONLY way an LLM-proposed effort estimate can
# influence the final hours. Python never uses the model's numbers as-is.
# ─────────────────────────────────────────────────────────────────────────

# How far an LLM's proposed (min, likely, max) may deviate from the task
# type's own canonical range before Python clamps it back -- generous
# enough that a genuinely large- or small-scope task can still read as
# bigger/smaller, bounded enough that no single proposal can be wildly
# unrealistic for its declared type.
_LLM_EFFORT_CLAMP_LOW_FACTOR = 0.4
_LLM_EFFORT_CLAMP_HIGH_FACTOR = 2.2


def validate_and_clamp_effort(
    type_key: str,
    proposed_min: float | None,
    proposed_likely: float | None,
    proposed_max: float | None,
) -> tuple[int, int, int]:
    """
    Validates an LLM-proposed (min, likely, max) hour estimate against the
    task type's own defensible canonical range and clamps it into a bounded
    envelope around that range. Falls back entirely to the canonical range
    -- never partially trusting the proposal -- when it is missing,
    non-numeric, or has any non-positive value. A simple ordering mistake
    (min > max) is repaired by sorting rather than rejected outright.
    """
    canonical = TASK_TYPES.get(type_key, TASK_TYPES[_DEFAULT_TASK_TYPE]).hours
    canonical_min, _canonical_likely, canonical_max = canonical

    try:
        values = [float(v) for v in (proposed_min, proposed_likely, proposed_max)]
    except (TypeError, ValueError):
        values = None

    if values is None or any(v <= 0 for v in values):
        return canonical

    lo, likely, hi = values if values[0] <= values[1] <= values[2] else sorted(values)

    envelope_lo = canonical_min * _LLM_EFFORT_CLAMP_LOW_FACTOR
    envelope_hi = canonical_max * _LLM_EFFORT_CLAMP_HIGH_FACTOR

    lo = max(envelope_lo, min(lo, envelope_hi))
    hi = max(envelope_lo, min(hi, envelope_hi))
    likely = max(lo, min(likely, hi))

    return round(lo), round(likely), round(hi)


def estimate_task_hours(
    title: str,
    phase_name: str = "",
    *,
    difficulty_level: str = "medium",
    is_safety_sensitive: bool = False,
    is_security_sensitive: bool = False,
    effort_override: tuple[int, int, int] | None = None,
    scope_hint: str = "",
    type_key_override: str | None = None,
) -> tuple[int, int]:
    """
    Deterministic (hours, working_days) estimate. No hashing of task text.

    By default (no override), driven entirely by the task's TEXT-classified
    type -- same title (or any wording that classifies to the same type)
    always yields the same base numbers. When `effort_override` is given
    (a validated, already-clamped (min, likely, max) triple -- see
    validate_and_clamp_effort/task_metadata.py), it is used as the PERT
    input instead of the type's own canonical range; `type_key_override`
    likewise lets a validated type take priority over text classification
    for choosing which multipliers apply. Both are no-ops when omitted, so
    every existing caller's behavior is unchanged.
    """
    type_key = (
        type_key_override if type_key_override in TASK_TYPES else classify_task_type(title, phase_name)
    )

    if effort_override is not None:
        min_h, likely_h, max_h = effort_override
    else:
        min_h, likely_h, max_h = TASK_TYPES[type_key].hours

    expected = pert_expected_hours(min_h, likely_h, max_h)

    multiplier = _DIFFICULTY_MULTIPLIER.get((difficulty_level or "medium").strip().lower(), 1.0)

    if is_safety_sensitive and type_key in _SAFETY_RELEVANT_TYPES:
        multiplier *= _SAFETY_SENSITIVE_MULTIPLIER

    if is_security_sensitive and type_key in _SECURITY_RELEVANT_TYPES:
        multiplier *= _SECURITY_SENSITIVE_MULTIPLIER

    scope_multiplier, _explanation = detect_scope_multiplier(scope_hint, title)
    multiplier *= scope_multiplier

    multiplier = max(_MIN_COMBINED_MULTIPLIER, min(multiplier, _MAX_COMBINED_MULTIPLIER))

    hours = max(1, round(expected * multiplier))
    working_days = compute_working_days(hours)

    return hours, working_days


# ─────────────────────────────────────────────────────────────────────────
# Priority classification
# ─────────────────────────────────────────────────────────────────────────

_MANDATORY_OVERRIDE_KEYWORDS = ("mandatory", "must have", "essential", "required")

_CRITICAL_KEYWORDS = (
    "database schema", "database design", "core backend", "backend logic",
    "core workflow", "core feature", "authentication", "login system",
    "authorization", "core ai", "ai integration", "central business logic",
    "required api", "core api", "primary business logic", "security",
)

_HIGH_KEYWORDS = (
    "essential ui", "integration test", "deployment configuration",
    "deployment prep", "required documentation", "core ui", "deployment",
)

_MEDIUM_KEYWORDS = (
    "usability improvement", "additional report", "secondary validation",
    "minor validation", "extra report", "usability",
)

_OPTIONAL_KEYWORDS = (
    "extra dashboard", "additional dashboard", "advanced visual polish",
    "visual polish", "non-required analytic", "non-essential analytic",
    "stretch integration", "stretch feature", "enhancement",
    "advanced analytics", "nice to have", "bonus feature", "optional", "polish",
)


def classify_task_priority(
    title: str,
    phase_name: str = "",
    *,
    mandatory_hint: bool | None = None,
) -> str:
    """
    Deterministic critical/high/medium/optional classification. A task or
    phase explicitly described as mandatory/required/essential is NEVER
    classified as optional, even if it also matches an optional-sounding
    keyword.

    The default (when nothing matches any keyword bucket) is "high", not
    "medium" -- a task only becomes deferrable by matching an EXPLICIT
    optional/medium-sounding phrase. Priority must come from structured
    signal, never from a keyword-matching gap: a project-specific task
    written in vocabulary this generic keyword list doesn't recognize (e.g.
    "Train the baseline triage model on the prepared dataset" for an AI
    project) must not be silently deferred just because it isn't phrased
    like a typical web-app task.

    `mandatory_hint` is a validated (see task_metadata.py) LLM-declared
    mandatory/optional signal, when available -- treated as equivalent to
    an explicit mandatory keyword when True, or an explicit optional
    keyword when False. A "critical" keyword match, or an explicit
    mandatory/required/essential phrase, still always wins over
    `mandatory_hint=False` -- Python never lets a single declared flag
    override an unambiguous textual signal that the work is essential.
    """
    combined = f"{title} {phase_name}"
    is_explicitly_mandatory = (
        lexicon.contains_any(combined, _MANDATORY_OVERRIDE_KEYWORDS) or mandatory_hint is True
    )

    if lexicon.contains_any(combined, _CRITICAL_KEYWORDS):
        return "critical"

    if not is_explicitly_mandatory and (
        mandatory_hint is False or lexicon.contains_any(combined, _OPTIONAL_KEYWORDS)
    ):
        return "optional"

    if lexicon.contains_any(combined, _HIGH_KEYWORDS):
        return "high"

    if not is_explicitly_mandatory and lexicon.contains_any(combined, _MEDIUM_KEYWORDS):
        return "medium"

    return "high"


# ─────────────────────────────────────────────────────────────────────────
# Required-skill extraction
# ─────────────────────────────────────────────────────────────────────────

_SKILL_VOCABULARY = (
    "PostgreSQL", "ASP.NET Core", "Razor Pages", "FastAPI", "Python",
    "JavaScript", "Bootstrap", "Ollama", "Groq", "Gemini", "OAuth",
    "Google Calendar", "REST API", "Entity Framework", "SQL", "JSON",
    "Machine Learning", "NLP", "PyTorch", "scikit-learn", "Docker",
)


def extract_required_skills(title: str) -> list[str]:
    return [skill for skill in _SKILL_VOCABULARY if lexicon.contains_phrase(title, skill)]


# ─────────────────────────────────────────────────────────────────────────
# Padding / vague-task detection
# ─────────────────────────────────────────────────────────────────────────

_PADDING_PREFIXES = ("review and refine:", "test and verify:")


def is_padding_task(title: str) -> bool:
    lowered = title.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _PADDING_PREFIXES)


_VAGUE_TASK_PHRASES = (
    "learn nlp", "study postgresql", "work on the backend", "implement features",
    "continue development", "improve the system", "complete the model",
    "handle testing", "work on the planned tasks", "develop core features",
    "work on the project", "do the tasks", "complete the phase",
    "work on the planned", "learn arabic nlp", "study the database",
)


def is_vague_task(title: str) -> bool:
    """
    True if the task is a banned generic phrase, OR carries too little
    concrete content to be independently estimable/testable -- fewer than
    2 meaningful (non-stopword, non-bare-verb) tokens after normalization.
    """
    if lexicon.contains_any(title, _VAGUE_TASK_PHRASES):
        return True

    return len(_core_tokens(title)) < 2


# ─────────────────────────────────────────────────────────────────────────
# Duplicate detection
# ─────────────────────────────────────────────────────────────────────────

_STOPWORDS = {
    "a", "an", "the", "for", "and", "of", "to", "in", "on", "with", "this",
    "that", "system", "application", "app", "module", "feature",
    "functionality", "using", "via", "into", "from", "as",
}

_ACTION_VERBS = {
    "implement", "implementing", "develop", "developing", "create",
    "creating", "build", "building", "design", "designing", "add", "adding",
    "perform", "performing", "complete", "completing", "work", "test",
    "testing", "review", "refine", "verify", "establish", "set", "setup",
    "prepare", "preparing", "write", "writing", "construct", "constructing",
    "define", "defining",
}


def _core_tokens(title: str) -> set[str]:
    tokens = lexicon.normalize(title).split()

    core = []
    for token in tokens:
        stemmed = token[:-1] if token.endswith("s") and len(token) > 3 else token
        if stemmed in _STOPWORDS or stemmed in _ACTION_VERBS:
            continue
        core.append(stemmed)

    return set(core)


def are_duplicate_tasks(a: str, b: str) -> bool:
    """
    True if two task descriptions describe the same underlying deliverable.
    An exact (case/whitespace-insensitive) match is always a duplicate,
    regardless of core-token content -- otherwise two literally identical
    titles that happen to consist only of stopwords/verbs (empty core-token
    sets) would never be recognized as duplicates of each other. Beyond
    that, strips action verbs and filler words, then compares remaining
    core tokens: exact match, subset, or >=50% Jaccard overlap.
    """
    if a.strip().lower() == b.strip().lower():
        return True

    core_a, core_b = _core_tokens(a), _core_tokens(b)

    if not core_a or not core_b:
        return False

    if core_a == core_b:
        return True

    smaller, larger = (core_a, core_b) if len(core_a) <= len(core_b) else (core_b, core_a)

    if smaller.issubset(larger):
        return True

    union = core_a | core_b
    intersection = core_a & core_b

    return len(intersection) / len(union) >= 0.5


def deduplicate_tasks(titles: list[str]) -> list[str]:
    """Keep first-occurrence order; when a later task duplicates an earlier
    one, keep whichever phrasing is more specific (longer)."""
    kept: list[str] = []

    for title in titles:
        clean = title.strip()

        if not clean:
            continue

        merged = False

        for index, existing in enumerate(kept):
            if are_duplicate_tasks(clean, existing):
                if len(clean) > len(existing):
                    kept[index] = clean
                merged = True
                break

        if not merged:
            kept.append(clean)

    return kept


# ─────────────────────────────────────────────────────────────────────────
# Task-density guidance (lifecycle/task-count validation)
# ─────────────────────────────────────────────────────────────────────────


def min_tasks_for_duration(duration_weeks: int, phase_name: str = "") -> int:
    """
    Minimum distinct, non-padding tasks a phase of this length should carry:
    1 week -> 3, 2 weeks -> 5, 3 weeks -> 7, 4 weeks -> 9 (2*weeks + 1).
    Final-presentation/small-deployment phases get a lower floor -- they are
    legitimately allowed to be light on task count.
    """
    is_wrap_up_phase = lexicon.contains_any(
        phase_name, ("presentation", "demo", "final delivery", "deployment", "submission")
    )

    if is_wrap_up_phase:
        return max(2, duration_weeks + 1)

    return max(3, 2 * duration_weeks + 1)
