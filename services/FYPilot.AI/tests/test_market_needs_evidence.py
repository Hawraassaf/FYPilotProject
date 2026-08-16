"""
Regression tests for app/agents/market_needs_evidence.py -- the deterministic
source-type classifier and relevance scorer that replaced MarketNeedsAgent's
hardcoded `relevanceScore=65` default and 4-bucket source-type classifier.

Root defect confirmed LIVE (2026-08-13, two unmocked /analyze-market-demand
runs): every real Brave-sourced result was scored exactly 65/100 (a fake,
never-computed constant, since raw search-result dicts never carry a
"relevanceScore" key), and genuinely credible sources (arxiv.org,
researchgate.net, frontiersin.org, grandviewresearch.com-style market-report
publishers) were all classified "External"/unverified because
_is_recognized_domain's ~19-domain allowlist never named them.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

from __future__ import annotations

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.agents.market_needs_evidence import (  # noqa: E402
    classify_source_type,
    compute_relevance_score,
    domain_of,
    is_high_authority,
    project_concept_terms,
)

_PROJECT_TERMS = project_concept_terms(
    project_title="Arabic Medical Symptom Triage Assistant",
    domain="Digital Health / Clinical Decision Support",
    technologies="Python FastAPI, PostgreSQL, NLP symptom classifier",
    problem_statement=(
        "Patients in Lebanon struggle to know whether their symptoms "
        "require urgent care, a regular doctor visit, or self-care, "
        "especially when Arabic-language digital health tools are scarce."
    ),
    target_users="Arabic-speaking patients and primary care clinics in Lebanon",
)


class SourceTypeClassificationTests(unittest.TestCase):
    def test_government_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("who.int", "WHO report", ""),
            "government_or_international_org",
        )
        self.assertEqual(
            classify_source_type("moph.gov.lb", "Ministry report", ""),
            "government_or_international_org",
        )

    def test_peer_reviewed_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("arxiv.org", "A study", ""),
            "peer_reviewed_research",
        )
        self.assertEqual(
            classify_source_type("frontiersin.org", "A study", ""),
            "peer_reviewed_research",
        )
        self.assertEqual(
            classify_source_type("researchgate.net", "A paper", ""),
            "peer_reviewed_research",
        )

    def test_university_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("aub.edu.lb", "AUB study", ""),
            "university",
        )

    def test_named_market_research_firm_classified_correctly(self):
        self.assertEqual(
            classify_source_type("grandviewresearch.com", "Market report", ""),
            "market_research_firm",
        )

    def test_unnamed_market_research_firm_recognized_by_naming_pattern(self):
        """
        Confirms the pattern-based fallback the task explicitly required
        instead of only a flat allowlist -- a market-research publisher
        never individually named still classifies correctly because its
        domain follows the category's real naming convention.
        """
        self.assertEqual(
            classify_source_type("somenewmarketresearchfirm.com", "Report", ""),
            "market_research_firm",
        )
        self.assertEqual(
            classify_source_type("dailymarketinsights.co", "Report", ""),
            "market_research_firm",
        )

    def test_industry_association_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("healthtechassociation.org", "Report", ""),
            "industry_association",
        )

    def test_financial_press_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("reuters.com", "News", ""),
            "financial_business_press",
        )

    def test_technology_press_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("healthcareitnews.com", "News", ""),
            "technology_press",
        )

    def test_commercial_marketplace_domain_classified_correctly(self):
        self.assertEqual(
            classify_source_type("kashipara.com", "Free FYP source code", ""),
            "commercial_marketplace",
        )

    def test_commercial_marketplace_text_pattern_catches_unnamed_domain(self):
        self.assertEqual(
            classify_source_type(
                "randomsite.example", "Final year project topics with source code", "",
            ),
            "commercial_marketplace",
        )

    def test_unknown_domain_falls_through_to_unknown(self):
        self.assertEqual(
            classify_source_type("someobscuresite.example", "Nothing special", ""),
            "unknown",
        )

    def test_high_authority_types_include_market_research_and_peer_reviewed(self):
        self.assertTrue(is_high_authority("peer_reviewed_research"))
        self.assertTrue(is_high_authority("market_research_firm"))
        self.assertTrue(is_high_authority("government_or_international_org"))
        self.assertTrue(is_high_authority("university"))
        self.assertTrue(is_high_authority("industry_association"))

    def test_low_authority_types_excluded(self):
        self.assertFalse(is_high_authority("commercial_marketplace"))
        self.assertFalse(is_high_authority("blog_or_seo_content"))
        self.assertFalse(is_high_authority("unknown"))
        self.assertFalse(is_high_authority("technology_press"))

    def test_domain_of_strips_www_and_scheme(self):
        self.assertEqual(domain_of("https://www.example.com/page"), "example.com")


class RelevanceScoringTests(unittest.TestCase):
    def test_never_returns_a_constant_regardless_of_input(self):
        """The exact defect this module replaces: relevanceScore must vary
        with actual content, not be a fixed number for every source."""
        scores = {
            compute_relevance_score(
                _PROJECT_TERMS, "market_research_firm",
                "AI Symptom Checker Market Size, Share & Trends",
                "digital health symptom checker adoption market size trends 2033",
            ),
            compute_relevance_score(
                _PROJECT_TERMS, "commercial_marketplace",
                "Free FYP source code download", "download project with source code",
            ),
            compute_relevance_score(
                _PROJECT_TERMS, "blog_or_seo_content",
                "Best pizza recipes for 2026", "toppings and dough tips",
            ),
        }
        self.assertGreater(len(scores), 1, "Every source scored identically -- the hardcoded-65 defect regressed.")

    def test_low_literal_word_overlap_does_not_tank_an_obviously_related_source(self):
        """
        Regression test for the word-overlap-over-sensitivity defect:
        "AI Symptom Checker Market Size, Share & Trends" shares almost no
        exact tokens with the project's own wording, but is obviously
        on-topic and from a credible market-research publisher -- it must
        not be scored as if it were irrelevant.
        """
        score = compute_relevance_score(
            _PROJECT_TERMS,
            "market_research_firm",
            "AI Symptom Checker Market Size, Share & Trends",
            "digital health symptom checker adoption market size trends 2033",
        )
        self.assertGreaterEqual(
            score, 50,
            f"Expected a clearly on-topic, credible source to score at least "
            f"50/100, got {score}.",
        )

    def test_off_topic_source_scores_low_even_from_a_generic_domain(self):
        score = compute_relevance_score(
            _PROJECT_TERMS, "unknown",
            "Best pizza recipes for 2026", "toppings and dough tips",
        )
        self.assertLess(score, 25)

    def test_commercial_marketplace_scores_very_low(self):
        score = compute_relevance_score(
            _PROJECT_TERMS, "commercial_marketplace",
            "Free FYP source code download", "download project with source code",
        )
        self.assertLess(score, 15)

    def test_strong_exact_term_overlap_scores_highest(self):
        strong = compute_relevance_score(
            _PROJECT_TERMS, "peer_reviewed_research",
            "Arabic symptom triage assistant for digital health clinics",
            "A clinical decision support tool for Arabic-speaking patients "
            "needing urgent care triage in Lebanon.",
        )
        weak = compute_relevance_score(
            _PROJECT_TERMS, "peer_reviewed_research",
            "A study", "",
        )
        self.assertGreater(strong, weak)
        self.assertGreaterEqual(strong, 80)

    def test_empty_project_terms_returns_neutral_score_not_zero(self):
        self.assertEqual(compute_relevance_score(set(), "unknown", "x", "y"), 50)

    def test_score_always_bounded_0_to_100(self):
        for source_type in (
            "government_or_international_org", "peer_reviewed_research",
            "market_research_firm", "commercial_marketplace", "unknown",
        ):
            score = compute_relevance_score(
                _PROJECT_TERMS, source_type,  # type: ignore[arg-type]
                "Arabic medical symptom triage digital health clinical decision",
                "Arabic-speaking patients primary care clinics Lebanon urgent",
            )
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


class ProjectConceptTermsTests(unittest.TestCase):
    def test_extracts_meaningful_terms_from_all_fields(self):
        terms = project_concept_terms(
            project_title="Smart Irrigation Scheduler",
            domain="AgriTech",
            technologies="Python, IoT sensors",
            problem_statement="Farmers waste water because irrigation timing is guessed rather than measured.",
            target_users="Small-scale farmers",
        )
        self.assertIn("irrigation", terms)
        self.assertIn("agritech", terms)
        self.assertIn("farmers", terms)

    def test_stopwords_and_generic_project_words_excluded(self):
        terms = project_concept_terms(
            project_title="A Project For Students",
            domain="General Software System",
            technologies="",
            problem_statement="This is a final year project about market demand for students.",
            target_users="",
        )
        for stopword in ("the", "a", "for", "final", "year", "project", "market", "demand", "students"):
            self.assertNotIn(stopword, terms)


if __name__ == "__main__":
    unittest.main()
