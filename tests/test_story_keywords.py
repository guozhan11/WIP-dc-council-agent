import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from digest import drop_unsupported_interest_keywords, enrich_story_cards  # noqa: E402


GUARD_STORY = (
    "DOJ Rebukes DC Council Over National Guard Withdrawal Demand "
    "The Justice Department condemned the Council's letters to governors, "
    "calling them impotent theatrics and politically motivated demands."
)

BEPS_STORY = (
    "DC Council Delays Energy Benchmarking Compliance "
    "The Council approved a one-year delay to the building energy performance standards cycle."
)

INTERESTS = "BEPS, Benchmarking, Energy, Pepco, Washington Gas"


class DropUnsupportedInterestKeywordsTests(unittest.TestCase):
    def test_interest_terms_absent_from_the_story_are_dropped(self):
        kept = drop_unsupported_interest_keywords(
            ["DC Council", "National Guard", "Energy", "Pepco"], GUARD_STORY, INTERESTS
        )

        self.assertEqual(kept, ["DC Council", "National Guard"])

    def test_interest_terms_the_story_actually_covers_are_kept(self):
        kept = drop_unsupported_interest_keywords(
            ["Energy", "Benchmarking", "BEPS"], BEPS_STORY, INTERESTS
        )

        self.assertEqual(kept, ["Energy", "Benchmarking", "BEPS"])

    def test_coined_keywords_survive_even_when_absent_verbatim(self):
        # "climate policy" is the model's own description, not an echo of the
        # subscriber's interests, so it is not subject to the check.
        kept = drop_unsupported_interest_keywords(
            ["climate policy", "DC buildings"], BEPS_STORY, INTERESTS
        )

        self.assertEqual(kept, ["climate policy", "DC buildings"])

    def test_a_broad_topic_matches_through_its_aliases(self):
        story = "WMATA plans more frequent bus service across the District."

        kept = drop_unsupported_interest_keywords(["Transportation"], story, "Transportation")

        self.assertEqual(kept, ["Transportation"])

    def test_subscribers_without_interests_are_untouched(self):
        keywords = ["Energy", "Pepco"]

        self.assertEqual(drop_unsupported_interest_keywords(keywords, GUARD_STORY, None), keywords)


class EnrichStoryCardsTests(unittest.TestCase):
    def test_both_sections_are_filtered(self):
        summary = {
            "bullets": [
                {
                    "headline": "DC Council Delays Energy Benchmarking Compliance",
                    "long_summary": BEPS_STORY,
                    "keywords": ["Energy", "BEPS"],
                }
            ],
            "other_news_bullets": [
                {
                    "headline": "DOJ Rebukes DC Council Over National Guard",
                    "long_summary": GUARD_STORY,
                    "keywords": ["National Guard", "Energy", "Pepco"],
                }
            ],
        }

        enrich_story_cards(summary, interests=INTERESTS)

        self.assertEqual(summary["bullets"][0]["keywords"], ["Energy", "BEPS"])
        self.assertEqual(summary["other_news_bullets"][0]["keywords"], ["National Guard"])


if __name__ == "__main__":
    unittest.main()
