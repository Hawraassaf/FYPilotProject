"""
Project similarity / originality checker.
Uses TF-IDF cosine similarity to detect overlapping project descriptions.
"""
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from app.database import execute_query
from app.models.schemas import SimilarProject, SimilarityResponse


def _clean_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def check_similarity(project_id: int) -> SimilarityResponse:
    target_projects = execute_query("SELECT * FROM projects WHERE id = :pid", {"pid": project_id})
    if not target_projects:
        raise ValueError(f"Project {project_id} not found")
    target = target_projects[0]

    all_projects = execute_query(
        "SELECT id, title, description, technologies FROM projects WHERE id != :pid",
        {"pid": project_id}
    )

    if not all_projects:
        return SimilarityResponse(
            project_id=project_id,
            similarity_results=[],
            originality_score=100.0,
            verdict="No other projects to compare against.",
        )

    target_text = _clean_text(f"{target.get('title', '')} {target.get('description', '')} {target.get('technologies', '')}")
    other_texts = [
        _clean_text(f"{p.get('title', '')} {p.get('description', '')} {p.get('technologies', '')}")
        for p in all_projects
    ]

    all_texts = [target_text] + other_texts
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, stop_words="english")

    try:
        tfidf_matrix = vectorizer.fit_transform(all_texts)
        cosine_scores = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
    except Exception:
        cosine_scores = np.zeros(len(all_projects))

    feature_names = vectorizer.get_feature_names_out() if hasattr(vectorizer, "get_feature_names_out") else []

    similar_projects: list[SimilarProject] = []
    THRESHOLD = 0.15

    for i, score in enumerate(cosine_scores):
        if score >= THRESHOLD:
            proj = all_projects[i]
            # Extract top shared n-grams as "matched phrases"
            matched_phrases: list[str] = []
            try:
                target_vec = tfidf_matrix[0].toarray().flatten()
                other_vec = tfidf_matrix[i + 1].toarray().flatten()
                overlap = np.minimum(target_vec, other_vec)
                top_indices = overlap.argsort()[-5:][::-1]
                matched_phrases = [str(feature_names[idx]) for idx in top_indices if overlap[idx] > 0]
            except Exception:
                pass

            similar_projects.append(SimilarProject(
                project_id=proj["id"],
                title=proj.get("title", "Unknown"),
                similarity_score=round(float(score) * 100, 1),
                matched_phrases=matched_phrases[:5],
            ))

    similar_projects.sort(key=lambda x: x.similarity_score, reverse=True)

    max_similarity = max((s.similarity_score for s in similar_projects), default=0.0)
    originality_score = max(0.0, 100.0 - max_similarity)

    if originality_score >= 80:
        verdict = "Highly original. Your project has a distinct topic and approach."
    elif originality_score >= 60:
        verdict = "Mostly original, but shares some thematic overlap with existing projects. Consider differentiating your approach."
    elif originality_score >= 40:
        verdict = "Moderate similarity detected. Revise your description to emphasise what makes your project unique."
    else:
        verdict = "High similarity with existing projects. Consult your supervisor about differentiating your work."

    return SimilarityResponse(
        project_id=project_id,
        similarity_results=similar_projects[:5],
        originality_score=round(originality_score, 1),
        verdict=verdict,
    )
