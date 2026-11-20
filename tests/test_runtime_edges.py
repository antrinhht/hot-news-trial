import unittest

from hotnews.collector import _article
from hotnews.pipeline import instant, new_state, registry, update_health, valid_state


class RuntimeEdges(unittest.TestCase):
    def test_structurally_broken_checkpoint_is_rejected(self):
        self.assertFalse(valid_state(dict(schema_version=1, sources={"nyt-world":42}, engine={"items":True}, run_number=1)))

    def test_primary_document_is_an_explicit_link(self):
        source = next(s for s in registry() if s["source_id"] == "hackernews")
        for url, expected in [
            ("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260506a.htm", True),
            ("https://www.federalreserve.gov/newsevents/pressreleases/", False),
            ("https://example.org/story?via=federalreserve.gov", False),
        ]:
            item = _article(source, {"url": url, "title": "Fed interest rate decision"})
            self.assertEqual(item.get("primary_url") == url, expected)

    def test_retry_after_is_respected(self):
        source = next(s for s in registry() if s["source_id"] == "caixin-global")
        state = new_state()
        status, _ = update_health([source], state, {"caixin-global":{"status":"failed","items":[],
                                   "retry_after_seconds":7200}}, instant("2026-11-20T12:03:00Z"))
        self.assertEqual(status[0]["next_due_at"], "2026-11-20T14:03:00Z")
