import sys
import unittest
from pathlib import Path

import yaml


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from summarizer_openai import _rank_sources_by_authority, url_domain  # noqa: E402


CONFIG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())
DOMAIN_WEIGHT = CONFIG["ranking"]["citation_domain_weight"]

# The order the model returned them in for the DOJ story, mirrors first.
SYNDICATED = [
    {"source": "google_alerts", "url": "https://ca.news.yahoo.com/ex-detective-rips-dc-council"},
    {"source": "google_alerts", "url": "https://www.aol.com/articles/justice-department-slams"},
    {"source": "google_alerts", "url": "https://streamlinefeed.co.ke/news/justice-department-rebukes"},
    {"source": "google_alerts", "url": "https://wfin.com/fox-political-news/justice-department-slams"},
    {"source": "google_alerts", "url": "https://thehill.com/homenews/administration/6026345"},
    {"source": "google_alerts", "url": "https://www.washingtonpost.com/dc-md-va/2026/08/12/justice-department"},
    {"source": "google_alerts", "url": "https://wamu.org/story/26/08/13/national-guard-dc-politics"},
]


class UrlDomainTests(unittest.TestCase):
    def test_www_is_stripped(self):
        self.assertEqual(url_domain("https://www.washingtonpost.com/dc-md-va/x"), "washingtonpost.com")

    def test_subdomains_are_preserved(self):
        self.assertEqual(url_domain("https://ca.news.yahoo.com/x"), "ca.news.yahoo.com")

    def test_missing_url_is_empty(self):
        self.assertEqual(url_domain(""), "")
        self.assertEqual(url_domain(None), "")


class RankSourcesByAuthorityTests(unittest.TestCase):
    def test_originals_outrank_syndication_mirrors(self):
        ids = list(range(1, len(SYNDICATED) + 1))

        ranked = _rank_sources_by_authority(ids, SYNDICATED, DOMAIN_WEIGHT)

        # washingtonpost, wamu, thehill lead; yahoo/aol/streamlinefeed/wfin trail.
        self.assertEqual(ranked[:3], [6, 7, 5])
        self.assertEqual(sorted(ranked[3:]), [1, 2, 3, 4])

    def test_capping_after_ranking_keeps_the_originals(self):
        ids = list(range(1, len(SYNDICATED) + 1))

        kept = _rank_sources_by_authority(ids, SYNDICATED, DOMAIN_WEIGHT)[:4]

        domains = {url_domain(SYNDICATED[i - 1]["url"]) for i in kept}
        self.assertIn("washingtonpost.com", domains)
        self.assertIn("wamu.org", domains)
        self.assertNotIn("streamlinefeed.co.ke", domains)
        self.assertNotIn("aol.com", domains)

    def test_model_order_breaks_ties_between_equal_domains(self):
        items = [
            {"url": "https://ggwash.org/view/1"},
            {"url": "https://ggwash.org/view/2"},
        ]

        self.assertEqual(_rank_sources_by_authority([2, 1], items, DOMAIN_WEIGHT), [2, 1])

    def test_unknown_domains_take_the_default_weight(self):
        items = [
            {"url": "https://some-new-outlet.example/story"},
            {"url": "https://aol.com/story"},
            {"url": "https://washingtonpost.com/story"},
        ]

        self.assertEqual(_rank_sources_by_authority([1, 2, 3], items, DOMAIN_WEIGHT), [3, 1, 2])

    def test_no_config_leaves_the_model_order_alone(self):
        self.assertEqual(_rank_sources_by_authority([3, 1, 2], SYNDICATED, None), [3, 1, 2])


if __name__ == "__main__":
    unittest.main()
