"""
POST /check-similarity
Checks whether a new project idea is original vs a corpus of known FYP titles.
Uses TF-IDF cosine similarity when scikit-learn is available, otherwise Jaccard fallback.
"""
from __future__ import annotations

import re
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

KNOWN_FYPS = [
    "Smart Hospital Management System",
    "AI-Based Face Recognition Attendance",
    "E-Commerce Platform with Recommendation Engine",
    "Online Learning Management System",
    "Student Grading and Result Management",
    "Restaurant Ordering System",
    "IoT Smart Home Automation",
    "Library Management System",
    "Blockchain-Based Certificate Verification",
    "Ride Sharing Application",
    "Job Portal Platform",
    "Social Media Analytics Dashboard",
    "Real-Time Chat Application",
    "Budget and Expense Tracker",
    "Employee Performance Management",
    "Online Voting System",
    "Medical Records Management System",
    "Tourism and Travel Booking Platform",
    "Event Management System",
    "Inventory Management for SMEs",
]


class SimilarProject(BaseModel):
    title:            str
    similarity_score: int


class SimilarityRequest(BaseModel):
    title:       str
    description: str


class SimilarityResponse(BaseModel):
    similarity_score:        int
    originality_score:       int
    similar_projects:        list[SimilarProject]
    improvement_suggestions: list[str]


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r'\b[a-z]{3,}\b', text.lower()))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tfidf_similarities(query: str, corpus: list[str]) -> list[float]:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        all_texts = [query] + corpus
        vec   = TfidfVectorizer(stop_words="english")
        tfidf = vec.fit_transform(all_texts)
        sims  = cosine_similarity(tfidf[0:1], tfidf[1:])[0]
        return sims.tolist()
    except ImportError:
        tokens = _tokenize(query)
        return [_jaccard(tokens, _tokenize(c)) for c in corpus]


@router.post("/check-similarity", response_model=SimilarityResponse)
def check_similarity(req: SimilarityRequest) -> SimilarityResponse:
    combined_query = f"{req.title} {req.description}"
    sims = _tfidf_similarities(combined_query, KNOWN_FYPS)

    top_matches = sorted(
        zip(KNOWN_FYPS, sims), key=lambda x: x[1], reverse=True
    )[:3]

    max_sim     = max(sims) if sims else 0.0
    sim_score   = int(max_sim * 100)
    orig_score  = 100 - sim_score

    similar: list[SimilarProject] = [
        SimilarProject(title=t, similarity_score=int(s * 100))
        for t, s in top_matches if s > 0.05
    ]

    suggestions: list[str] = []
    if sim_score > 60:
        suggestions += [
            "Add a highly specific domain twist to differentiate (e.g. 'for Lebanese SMEs')",
            "Incorporate a novel AI/ML component not present in similar projects",
            "Focus on an underserved sub-population as your target users",
        ]
    elif sim_score > 30:
        suggestions += [
            "Emphasise your unique methodology in the proposal",
            "Highlight the specific local context that differentiates your work",
        ]
    else:
        suggestions.append("Your idea appears highly original — maintain this uniqueness in your proposal.")

    return SimilarityResponse(
        similarity_score=sim_score,
        originality_score=orig_score,
        similar_projects=similar,
        improvement_suggestions=suggestions,
    )
