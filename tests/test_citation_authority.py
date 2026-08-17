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


class OfficialSourcesOutrankMediaTests(unittest.TestCase):
    OFFICIAL = [
        {"url": "https://dc.granicus.com/MediaPlayer.php?view_id=2&clip_id=1"},
        {"url": "https://dccouncil.gov/performance-oversight-2026/"},
        {"url": "https://dcregs.dc.gov/Common/NoticeDetail.aspx?NoticeId=1"},
        {"url": "https://dmv.dc.gov/release/purple-heart-plate"},
    ]

    def test_every_official_domain_beats_the_strongest_media_domain(self):
        best_media = max(DOMAIN_WEIGHT["domains"].values())
        items = self.OFFICIAL + [{"url": "https://www.washingtonpost.com/dc-md-va/x"}]
        ids = list(range(1, len(items) + 1))

        ranked = _rank_sources_by_authority(ids, items, DOMAIN_WEIGHT)

        self.assertEqual(ranked[-1], len(items), "the Washington Post should rank last here")
        self.assertLess(best_media, min(DOMAIN_WEIGHT["domain_suffixes"].values()))

    def test_an_official_record_survives_the_cap_against_many_outlets(self):
        items = SYNDICATED + [{"url": "https://dc.granicus.com/MediaPlayer.php?clip_id=9"}]
        ids = list(range(1, len(items) + 1))

        kept = _rank_sources_by_authority(ids, items, DOMAIN_WEIGHT)[:4]

        self.assertEqual(kept[0], len(items))

    def test_relevance_outranks_a_strong_publisher(self):
        # Observed in a preview: the Post's apple-party feature was promoted
        # into a National Guard card's visible citations purely on domain.
        story = (
            "Justice Department Rebukes DC Council on National Guard Withdrawal Demand. "
            "The DOJ called the letters politically motivated theatrics."
        )
        items = [
            {"url": "https://www.washingtonpost.com/dc-md-va/2026/08/15/hottest-dc-party-eating-apples-park/",
             "title": "He invited people to eat an apple with him. Hundreds showed up."},
            {"url": "https://thehill.com/homenews/administration/6026345",
             "title": "DOJ slams DC City Council for requests to remove National Guard"},
        ]

        self.assertEqual(_rank_sources_by_authority([1, 2], items, DOMAIN_WEIGHT, story), [2, 1])

    def test_publisher_still_decides_between_two_on_topic_sources(self):
        story = "Justice Department rebukes the Council over National Guard withdrawal."
        items = [
            {"url": "https://au.news.yahoo.com/x", "title": "DOJ scolds Council over National Guard"},
            {"url": "https://www.washingtonpost.com/dc-md-va/x", "title": "DOJ slams Council over National Guard"},
        ]

        self.assertEqual(_rank_sources_by_authority([1, 2], items, DOMAIN_WEIGHT, story), [2, 1])

    def test_without_story_text_publisher_alone_decides(self):
        items = [
            {"url": "https://aol.com/x", "title": "Mirror copy"},
            {"url": "https://www.washingtonpost.com/x", "title": "Original"},
        ]

        self.assertEqual(_rank_sources_by_authority([1, 2], items, DOMAIN_WEIGHT, ""), [2, 1])

    def test_unlisted_dc_gov_agency_sites_are_treated_as_official(self):
        items = [
            {"url": "https://www.washingtonpost.com/dc-md-va/x"},
            {"url": "https://doee.dc.gov/release/beps-update"},
        ]

        self.assertEqual(_rank_sources_by_authority([1, 2], items, DOMAIN_WEIGHT), [2, 1])


if __name__ == "__main__":
    unittest.main()
