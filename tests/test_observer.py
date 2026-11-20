import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hotnews.observer import _newest_missing, _targets, sample
from hotnews.pipeline import registry


NOW = datetime(2026, 11, 20, 12, 3, tzinfo=timezone.utc)
NPR = {"source_id": "npr-world", "provider": "rss"}


def rss(*rows):
    return ("<rss><channel>" + "".join(
        f"<item><title>Entry</title><link>https://example.com/{path}</link>"
        f"<pubDate>{date}</pubDate>{source}</item>" for path, date, source in rows
    ) + "</channel></rss>").encode()


class ObserverTests(unittest.TestCase):
    def test_prospective_window_and_scoped_public_items(self):
        payload = rss(("boundary", "Wed, 18 Nov 2026 12:03:00 GMT", ""),
                      ("future", "2026-11-20T13:00:00+00:00", ""),
                      ("naive", "2026-11-20T11:00:00", ""),
                      ("bad", "broken", ""),
                      ("older", "2026-11-19T11:00:00Z", ""),
                      ("now", "2026-11-20T13:03:00+01:00", ""))
        other_public = {("hackernews", "https://example.com/now")}
        candidate = _newest_missing(payload, NPR, other_public, NOW)
        self.assertEqual(candidate["url"], "https://example.com/now")
        self.assertEqual(candidate["source_time"], "2026-11-20T12:03:00Z")
        self.assertEqual(candidate["observed_at"], candidate["source_time"])
        public = other_public | {("npr-world", "https://example.com/now"), ("npr-world", "https://example.com/older")}
        self.assertIsNone(_newest_missing(payload, NPR, public, NOW))

    def test_rdf_atom_and_caixin_provenance(self):
        rdf = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
                 <item rdf:about="https://dw.com/story"><title>DW</title>
                 <dc:date>2026-11-20T10:00:00Z</dc:date></item></rdf:RDF>'''
        self.assertEqual(_newest_missing(rdf, NPR, set(), NOW)["url"], "https://dw.com/story")
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AP</title>
                 <link rel="self" href="https://api.example/private"/><link rel="alternate" href="https://youtube.com/watch?v=nice"/>
                 <published>2026-11-20T10:00:00Z</published></entry></feed>'''
        self.assertEqual(_newest_missing(atom, NPR, set(), NOW)["url"], "https://youtube.com/watch?v=nice")
        caixin = {"source_id": "caixin-global", "provider": "google_news"}
        payload = rss(("fake", "2026-11-20T12:01:00Z", '<source url="https://caixinglobal.com.example.org">Caixin Global</source>'),
                      ("other", "2026-11-20T12:00:00Z", '<source url="https://caixinglobal.com">Other</source>'),
                      ("real", "2026-11-20T11:00:00Z", '<source url="https://caixinglobal.com">Caixin Global</source>'))
        self.assertEqual(_newest_missing(payload, caixin, set(), NOW)["url"], "https://example.com/real")

    def test_dated_fast_rotation_and_slow_spacing(self):
        sources = registry()
        interval = {s["source_id"]: s["interval_minutes"] for s in sources}
        previous, fast_ids, slow_ids = {}, set(), Counter()
        for i in range(42):
            fast, slow = _targets(sources, NOW + timedelta(minutes=i * 10))
            fast_ids.add(fast[0]["source_id"])
            slow_ids[slow[0]["source_id"]] += 1
            for s, frequency in (fast, slow):
                sid = s["source_id"]
                if sid in previous:
                    gap = (i - previous[sid]) * 10
                    self.assertEqual(gap, frequency)
                    self.assertGreaterEqual(gap, interval[sid])
                previous[sid] = i
        self.assertEqual(len(fast_ids), 7)
        self.assertFalse(fast_ids & {"weibo", "zhihu", "thepaper"})
        self.assertEqual(slow_ids, {"bbc-world": 14, "caixin-global": 14, "ifanr": 7, "dw-world": 7})

    @patch("hotnews.observer._read_source")
    @patch("hotnews.observer.fetch_public")
    def test_failed_upstream_retains_public_observation_and_second_poll(self, page, feed):
        page.return_value = {"events": [{"items": [
            {"source_id": "hackernews", "url": "https://example.com/new"},
            {"source_id": "bbc-world", "url": "https://example.com/old?utm_source=rss"},
        ]}]}
        feed.side_effect = [TimeoutError("timed out"), rss(("new", "2026-11-20T11:00:00Z", ""),
                                                          ("old", "2026-11-20T12:00:00Z", ""))]
        observed = sample("https://test.invalid", as_of=NOW)
        self.assertTrue(observed["public_available"])
        self.assertEqual(len(observed["public_items"]), 2)
        self.assertEqual(len(observed["public_ids"]), 2)
        self.assertEqual([p["status"] for p in observed["polls"]], ["failed", "ok"])
        self.assertEqual(observed["candidates"][0]["source_id"], "bbc-world")
        self.assertEqual(observed["candidates"][0]["url"], "https://example.com/new")

    @patch("hotnews.observer._read_source")
    @patch("hotnews.observer.fetch_public", return_value=None)
    def test_absent_public_feed_does_not_register_absence_as_latency(self, page, source):
        result = sample("", as_of=NOW)
        self.assertFalse(result["public_available"])
        self.assertEqual(result["candidates"], [])
        source.assert_not_called()

    @patch("hotnews.observer._read_source")
    @patch("hotnews.observer.fetch_public", return_value={"events": []})
    def test_only_collector_retries_a_rate_limited_provider(self, page, download):
        caixin = next(s for s in registry() if s["source_id"] == "caixin-global")
        health = {"caixin-global": {"status": "failed", "next_due_at": "2026-11-20T10:00:00Z"}}
        with patch("hotnews.observer._targets", return_value=[(caixin, 30)]):
            result = sample("", as_of=NOW, source_health=health)
        self.assertEqual(result["polls"][0]["status"], "cooldown")
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
