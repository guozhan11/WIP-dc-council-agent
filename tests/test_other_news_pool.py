import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from digest import build_other_news_pool, build_plain_text  # noqa: E402


def item(title, url, source="google_alerts"):
    return {"title": title, "url": url, "source": source}


BEPS = item("DC Buildings Face a Costly Choice", "https://example.com/beps")
CRASHES = item("When DC adds bus and bike lanes, crashes go down", "https://example.com/lanes")
GUARD = item("DOJ slams DC Council over National Guard", "https://example.com/guard")
KENNEDY = item("Kennedy Center Board Votes to Add Trump's Name", "https://example.com/kennedy")


class OtherNewsPoolTests(unittest.TestCase):
    def test_interest_matches_come_before_the_rest_of_the_week(self):
        pool = build_other_news_pool([CRASHES], [GUARD, KENNEDY, CRASHES], [])

        self.assertEqual(pool[0], CRASHES)
        self.assertIn(GUARD, pool)
        self.assertIn(KENNEDY, pool)

    def test_already_cited_sources_are_dropped(self):
        cited = [{"n": 1, "url": "https://example.com/beps"}]

        pool = build_other_news_pool([BEPS, CRASHES], [BEPS, GUARD], cited)

        self.assertNotIn(BEPS, pool)
        self.assertIn(CRASHES, pool)
        self.assertIn(GUARD, pool)

    def test_an_item_in_both_groups_appears_once(self):
        pool = build_other_news_pool([CRASHES], [CRASHES, GUARD], [])

        self.assertEqual(pool.count(CRASHES), 1)

    def test_pool_still_has_material_when_nothing_matched_interests(self):
        pool = build_other_news_pool([], [GUARD, KENNEDY], [])

        self.assertEqual(pool, [GUARD, KENNEDY])


class ExtraSourceCountRenderingTests(unittest.TestCase):
    def _text(self, extra):
        summary = {
            "bullets": [
                {
                    "headline": "DOJ Rebukes DC Council",
                    "short_summary": "Short.",
                    "long_summary": "Long.",
                    "sources": [1, 2, 3, 4],
                    "extra_source_count": extra,
                }
            ],
            "sources": [{"n": 1, "title": "The Hill", "url": "https://example.com/hill"}],
        }
        return build_plain_text("Subject", [], {}, "https://example.com/unsub", summary)

    def test_dropped_syndications_are_reported_as_a_count(self):
        self.assertIn("Sources: [1], [2], [3], [4] and 17 more outlets", self._text(17))

    def test_a_single_dropped_outlet_is_singular(self):
        self.assertIn("and 1 more outlet\n", self._text(1))

    def test_nothing_is_added_when_no_sources_were_dropped(self):
        text = self._text(0)

        self.assertIn("Sources: [1], [2], [3], [4]", text)
        self.assertNotIn("more outlet", text)


if __name__ == "__main__":
    unittest.main()
