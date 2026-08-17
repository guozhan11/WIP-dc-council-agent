import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from digest import build_plain_text  # noqa: E402


AI_SUMMARY = {
    "bullets": [
        {
            "headline": "DC Council Delays Energy Benchmarking Compliance",
            "keywords": ["BEPS", "Energy"],
            "short_summary": "Compliance cycles slip by one year.",
            "long_summary": "The Council agreed to the mayor's proposal.",
            "sources": [1],
        }
    ],
    "sources": [
        {"n": 1, "title": "DC Buildings Face a Costly Choice", "url": "https://example.com/beps"},
    ],
}

# Mirrors what digest.py puts in `sections`: every weekly item, unranked and
# unfiltered by interest, with feed markup still in the titles.
SECTIONS = {
    "News mentions & other sources": [
        {
            "title": "Seattle Mariners celebrate 50 seasons with fans",
            "source": "google_alerts",
            "url": "https://example.com/mariners",
        },
        {
            "title": "Ex-detective rips <b>DC Council</b> push to withdraw National Guard",
            "source": "google_alerts",
            "url": "https://example.com/guard",
        },
    ]
}


class BuildPlainTextTests(unittest.TestCase):
    def test_unfiltered_sections_are_not_appended(self):
        text = build_plain_text("Subject", [], SECTIONS, "https://example.com/unsub", AI_SUMMARY)

        self.assertNotIn("News mentions & other sources", text)
        self.assertNotIn("Mariners", text)
        self.assertNotIn("example.com/mariners", text)

    def test_no_raw_markup_reaches_the_reader(self):
        text = build_plain_text("Subject", [], SECTIONS, "https://example.com/unsub", AI_SUMMARY)

        self.assertNotIn("<b>", text)
        self.assertNotIn("</b>", text)

    def test_summary_and_unsubscribe_survive(self):
        text = build_plain_text("Subject", [], SECTIONS, "https://example.com/unsub", AI_SUMMARY)

        self.assertIn("DC Council Delays Energy Benchmarking Compliance", text)
        self.assertIn("[1] DC Buildings Face a Costly Choice: https://example.com/beps", text)
        self.assertIn("Unsubscribe: https://example.com/unsub", text)

    def test_highlight_titles_are_sanitized_in_the_no_ai_fallback(self):
        highlights = [
            {
                "title": "Ex-detective rips <b>DC Council</b> push",
                "source": "google_alerts",
                "url": "https://example.com/guard",
            }
        ]

        text = build_plain_text("Subject", highlights, SECTIONS, "https://example.com/unsub", None)

        self.assertIn("Ex-detective rips DC Council push", text)
        self.assertNotIn("<b>", text)


if __name__ == "__main__":
    unittest.main()
