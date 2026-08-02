"""
Unit tests for app/agents/roadmap/lexicon.py -- word-boundary-aware,
alias-normalized keyword matching. Covers the specific false-positive/
false-negative cases substring matching used to cause (see roadmap_scheduler
problem #10 in the redesign brief).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.roadmap import lexicon  # noqa: E402


class WordBoundaryMatchingTests(unittest.TestCase):
    def test_short_keyword_does_not_match_inside_unrelated_word(self):
        self.assertFalse(lexicon.contains_phrase("This is required documentation", "ui"))
        self.assertFalse(lexicon.contains_phrase("Build the login page", "ui"))
        self.assertFalse(lexicon.contains_phrase("The quick machine learns", "ml"))

    def test_short_keyword_matches_as_a_real_token(self):
        self.assertTrue(lexicon.contains_phrase("Design the UI for the dashboard", "ui"))
        self.assertTrue(lexicon.contains_phrase("Retrain the ML model", "ml"))

    def test_setup_and_set_up_are_aliases(self):
        self.assertTrue(lexicon.contains_phrase("Set up the development environment", "setup"))
        self.assertTrue(lexicon.contains_phrase("Setup the development environment", "setup"))
        self.assertTrue(lexicon.contains_phrase("Set-up the environment", "setup"))

    def test_postgres_and_postgresql_are_aliases(self):
        self.assertTrue(lexicon.contains_phrase("Connect to PostgreSQL", "postgres"))
        self.assertTrue(lexicon.contains_phrase("Connect to Postgres", "postgres"))

    def test_plural_wording_still_matches_singular_phrase(self):
        self.assertTrue(lexicon.contains_phrase(
            "Write integration tests for the booking API", "integration test",
        ))
        self.assertTrue(lexicon.contains_phrase("Design the database schemas", "database schema"))

    def test_business_and_access_are_not_mangled_by_plural_stripping(self):
        # Guards the "ss"-ending exception in lexicon._destem.
        self.assertTrue(lexicon.contains_phrase("Define the business rules", "business"))
        self.assertTrue(lexicon.contains_phrase("Configure access control", "access"))

    def test_contains_any_and_matched_phrases(self):
        text = "Implement OAuth integration with Google Calendar"
        self.assertTrue(lexicon.contains_any(text, ("oauth", "unrelated")))
        self.assertEqual(lexicon.matched_phrases(text, ("oauth", "unrelated", "calendar")),
                          ["oauth", "calendar"])

    def test_empty_phrase_never_matches(self):
        self.assertFalse(lexicon.contains_phrase("anything at all", ""))


if __name__ == "__main__":
    unittest.main()
