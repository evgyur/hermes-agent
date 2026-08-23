import unittest

from scripts.seo_scorecard import compute_scorecard


class ScorecardTests(unittest.TestCase):
    def test_greenish_site_scores_high_without_fake_performance(self):
        audit = {
            "sitemap_url_count": 2,
            "ok_html_count": 2,
            "non_200": [],
            "missing_title": [],
            "missing_description": [],
            "missing_canonical": [],
            "bad_h1": [],
            "noindex": [],
            "missing_jsonld": [],
            "thin_pages_under_1500": [],
            "meaningful_image_alt_debt": [],
            "text_chars_median": 2600,
            "discovery": {
                "https://example.com/llms.txt": {"status": 200},
                "https://example.com/.well-known/mcp.json": {"status": 200},
                "https://example.com/sitemap.xml": {"status": 200},
                "https://example.com/robots.txt": {"status": 200},
            },
            "pages": [
                {"status": 200, "internal_links_unique": 20, "jsonld_types": ["Organization", "Person", "WebSite", "AboutPage", "FAQPage"]},
                {"status": 200, "internal_links_unique": 18, "jsonld_types": ["BreadcrumbList", "LearningResource"]},
            ],
        }
        score = compute_scorecard(audit)
        self.assertGreaterEqual(score["score"], 88)
        perf = [x for x in score["scorecard"] if x["criterion"] == "Performance confidence"][0]
        self.assertEqual(perf["score"], 6)

    def test_missing_jsonld_and_thin_content_reduce_score(self):
        audit = {
            "sitemap_url_count": 10,
            "ok_html_count": 10,
            "non_200": [],
            "missing_title": [],
            "missing_description": [],
            "missing_canonical": [],
            "bad_h1": [],
            "noindex": [],
            "missing_jsonld": [f"https://e.test/{i}" for i in range(5)],
            "thin_pages_under_1500": [f"https://e.test/{i}" for i in range(4)],
            "meaningful_image_alt_debt": [{"url": "https://e.test/1"}],
            "text_chars_median": 1200,
            "discovery": {},
            "pages": [{"status": 200, "internal_links_unique": 3, "jsonld_types": []} for _ in range(10)],
        }
        score = compute_scorecard(audit)
        self.assertLess(score["score"], 80)


if __name__ == "__main__":
    unittest.main()
