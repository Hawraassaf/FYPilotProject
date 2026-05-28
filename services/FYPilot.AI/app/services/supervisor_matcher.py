"""
Supervisor matching engine.
Matches student projects to supervisors based on expertise, workload, and topic similarity.
"""
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from app.database import execute_query
from app.models.schemas import SupervisorMatchRequest, SupervisorMatch, SupervisorMatchResponse


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()


def match_supervisors(req: SupervisorMatchRequest) -> SupervisorMatchResponse:
    supervisors = execute_query(
        """SELECT u.id, u.full_name, sp.department
           FROM users u
           LEFT JOIN supervisor_profiles sp ON sp.user_id = u.id
           WHERE u.role = 'supervisor'"""
    )

    if not supervisors:
        return SupervisorMatchResponse(matches=[])

    # Count current supervised projects per supervisor
    workloads = execute_query(
        "SELECT supervisor_id, COUNT(*) as cnt FROM projects WHERE supervisor_id IS NOT NULL GROUP BY supervisor_id"
    )
    workload_map = {w["supervisor_id"]: w["cnt"] for w in workloads}

    project_text = _clean(f"{req.project_title} {req.project_description} {req.project_technologies}")

    # Build supervisor "expertise" from their supervised project titles/techs
    supervisor_texts = []
    for sup in supervisors:
        proj_info = execute_query(
            "SELECT title, technologies FROM projects WHERE supervisor_id = :sid",
            {"sid": sup["id"]}
        )
        expertise = sup.get("department") or ""
        proj_summary = " ".join([f"{p.get('title','')} {p.get('technologies','')}" for p in proj_info])
        supervisor_texts.append(_clean(f"{expertise} {proj_summary}"))

    all_texts = [project_text] + supervisor_texts

    try:
        vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, stop_words="english")
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        similarity_scores = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
    except Exception:
        similarity_scores = np.ones(len(supervisors)) * 0.5

    matches: list[SupervisorMatch] = []
    MAX_WORKLOAD = 8  # max recommended students per supervisor

    for i, sup in enumerate(supervisors):
        sim_score = float(similarity_scores[i])
        workload = int(workload_map.get(sup["id"], 0))

        # Penalise overloaded supervisors
        workload_penalty = min(workload / MAX_WORKLOAD, 1.0) * 0.3
        final_score = max(0.0, min(1.0, sim_score * 0.7 + (1 - workload_penalty) * 0.3))

        reasons = []
        if sim_score > 0.3:
            reasons.append("Strong topic alignment with their supervised projects")
        if workload < 3:
            reasons.append("Currently has low supervision workload — more availability")
        elif workload < 6:
            reasons.append("Moderate workload — reasonable availability")
        else:
            reasons.append("High workload — may have limited availability")
        if sup.get("department"):
            reasons.append(f"Department: {sup['department']}")

        matches.append(SupervisorMatch(
            supervisor_id=sup["id"],
            supervisor_name=sup.get("full_name", "Unknown"),
            match_score=round(final_score * 100, 1),
            workload=workload,
            reasons=reasons,
        ))

    matches.sort(key=lambda m: m.match_score, reverse=True)
    return SupervisorMatchResponse(matches=matches[:5])
