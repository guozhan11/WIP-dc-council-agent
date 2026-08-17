import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from digest import filter_items_for_interests  # noqa: E402
from interest_matching import extract_interest_terms, text_matches_interest_terms  # noqa: E402


class InterestMatchingTests(unittest.TestCase):
    def test_environment_matches_energy_policy_without_literal_environment(self):
        items = [
            {
                "title": "DC Buildings Face a Costly Choice: Cut Energy Use or Pay Up",
                "summary": "The Council adjusted compliance cycles for covered buildings.",
            }
        ]

        self.assertEqual(filter_items_for_interests(items, "environment"), items)

    def test_subscriber_topics_expand_to_policy_specific_keywords(self):
        cases = {
            "Budget": "The FY 2027 appropriations package changes District revenue.",
            "Public safety": "MPD announced a new carjacking prevention initiative.",
            "Housing": "The bill adds tenant protections against eviction.",
            "Transportation": "WMATA plans more frequent bus service.",
            "Education": "DCPS will hire additional teachers for public schools.",
            "Health": "The measure expands Medicaid behavioral health coverage.",
            "Environment": "The BEPS proposal strengthens building energy efficiency.",
        }

        for topic, text in cases.items():
            with self.subTest(topic=topic):
                terms = extract_interest_terms(topic)
                self.assertTrue(text_matches_interest_terms(text, terms))

    def test_free_form_interest_terms_are_preserved(self):
        terms = extract_interest_terms("Pepco oversight")

        self.assertIn("pepco", terms)
        self.assertIn("oversight", terms)
        self.assertTrue(text_matches_interest_terms("The Council questioned Pepco executives.", terms))

    def test_short_aliases_use_word_boundaries(self):
        transportation_terms = extract_interest_terms("Transportation")

        self.assertTrue(text_matches_interest_terms("The Council discussed bus service.", transportation_terms))
        self.assertFalse(text_matches_interest_terms("The Council discussed business licensing.", transportation_terms))

    def test_keyword_does_not_match_inside_an_unrelated_word(self):
        terms = {"rent"}

        self.assertTrue(text_matches_interest_terms("Tenant rent stabilization", terms))
        self.assertFalse(text_matches_interest_terms("Current committee assignments", terms))


if __name__ == "__main__":
    unittest.main()
