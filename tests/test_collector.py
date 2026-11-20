import json
import os
from pathlib import Path
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from xml.etree import ElementTree as ET

from hotnews import collector


def source(identifier, provider="rss", endpoint="https://example.org/feed"):
    return {"source_id": identifier, "name": identifier, "kind": "editorial",
            "provider": provider, "endpoint": endpoint}


CAIXIN_XML = b"""<rss version="2.0"><channel><title>Fixture</title>
<item><source url="https://www.caixinglobal.com">Caixin Global</source>
<title>Confirmed report</title><link>https://news.google.com/a/1</link></item>
<item><source url="https://notcaixinglobal.com">Caixin Global</source>
<title>Other host</title><link>https://news.google.com/a/2</link></item>
<item><source url="https://www.caixinglobal.com">Other publisher</source>
<title>Other label</title><link>https://news.google.com/a/3</link></item></channel></rss>"""


class CollectorTests(unittest.TestCase):
    def test_rss_only_keeps_upstream_crawler_enabled(self):
        cfg = collector._config([source("dw-world")], Path("out"))
        self.assertTrue(cfg["platforms"]["enabled"])
        self.assertEqual(cfg["platforms"]["sources"], [])
        self.assertEqual([f["id"] for f in cfg["rss"]["feeds"]], ["dw-world"])
        self.assertFalse(cfg["notification"]["enabled"])

    def test_caixin_requires_publisher_label_and_domain(self):
        root = ET.fromstring(collector._caixin_xml(CAIXIN_XML))
        self.assertEqual([n.text for n in root.findall("./channel/item/link")],
                         ["https://news.google.com/a/1"])
        with self.assertRaises(ValueError):
            collector._caixin_xml(b"<html>unavailable</html>")

    def test_wire_byline_and_image_credit(self):
        raw = {"title": "Report", "url": "https://example.org/1",
               "author": "Jane Doe, Reuters", "published_at": "2026-11-20T10:15:00"}
        self.assertEqual(collector._article(source("npr-world"), raw)["origin_owner_id"], "reuters")
        self.assertEqual(collector._article(source("npr-world"), raw)["source_time"],
                         "2026-11-20T10:15:00Z")
        raw["author"] = "© Photo, Reuters"
        self.assertNotIn("origin_owner_id", collector._article(source("npr-world"), raw))
        raw["url"] = "javascript:alert(1)"
        self.assertIsNone(collector._article(source("npr-world"), raw))

    def test_ap_post_without_title_uses_cleaned_excerpt(self):
        summary = "Editors published the official bulletin this morning."
        item = collector._article(source("ap-bluesky"), {
            "url": "https://bsky.app/profile/example/post/1", "title": summary[:20] + "...",
            "summary": summary})
        self.assertEqual(item["title"], summary)
        self.assertIsNone(item["source_time"])

    def test_distinct_empty_failed_and_missing_status_across_midnight(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "rss").mkdir()
            for day in ["2026-11-20", "2026-11-21"]:
                with sqlite3.connect(base / "rss" / f"{day}.db") as db:
                    db.executescript("""
                      CREATE TABLE rss_crawl_status (crawl_record_id INTEGER, feed_id, status, error_message);
                      CREATE TABLE rss_items (id INTEGER, feed_id, title, url, guid, published_at, summary, author);
                    """)
                    if day.endswith("20"):
                        db.executemany("INSERT INTO rss_crawl_status VALUES (1,?,?,?)",
                                       [("empty", "success", ""), ("failed", "failed", "timeout"),
                                        ("populated", "success", "")])
                        db.execute("INSERT INTO rss_items VALUES (1,'populated','Reported','https://example.org/r','','','','')")
                db.close()
            for key, status in (("empty", "empty"), ("missing", "failed"),
                                ("failed", "failed"), ("populated", "ok")):
                self.assertEqual(collector._read_source(base, source(key), "")["status"], status)
            self.assertEqual(collector._read_source(base, source("failed"), "")["error"], "timeout")

    def test_hotlist_stores_rank_and_never_uses_crawl_time_as_publication(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "news").mkdir()
            with sqlite3.connect(base / "news" / "2026-11-20.db") as db:
                db.executescript("""
                    CREATE TABLE crawl_source_status (crawl_record_id INTEGER, platform_id, status);
                    CREATE TABLE news_items (id INTEGER, platform_id, title, url, mobile_url, rank);
                    INSERT INTO crawl_source_status VALUES(1,'weibo','success');
                    INSERT INTO news_items VALUES(10,'weibo','Observed topic','','https://weibo.com/t/1',3);
                """)
            db.close()
            observed = collector._read_source(base, source("weibo", "newsnow"), "")
            self.assertEqual(observed["items"], [{"title": "Observed topic", "url": "https://weibo.com/t/1",
                                                  "rank": 3, "guid": None, "source_time": None}])

    @unittest.skipUnless(os.environ.get("TRENDRADAR_DIR"), "requires pinned checkout + locked venv")
    def test_pinned_cli_and_caixin_on_local_fixtures(self):
        data = {
            "/caixin": CAIXIN_XML,
            "/empty": b'<rss version="2.0"><channel><title>Empty</title></channel></rss>',
            "/rss": b"""<rss version="2.0"><channel><title>HN</title><item>
                         <title>Community report</title><link>https://example.org/report</link>
                         <pubDate>Fri, 20 Nov 2026 10:15:00 +0000</pubDate></item></channel></rss>""",
            "/atom": b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
                         <entry><title>Video</title><id>urn:video:1</id>
                         <link href="https://example.org/video"/><updated>2026-11-20T10:15:00Z</updated>
                         </entry></feed>""",
            "/rdf": b"""<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                        xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
                        <item rdf:about="https://example.org/dw"><title>DW report</title>
                        <link>https://example.org/dw</link><dc:date>2026-11-20T10:15:00Z</dc:date>
                        </item></rdf:RDF>""",
        }
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(self.path)
                payload = data.get(self.path)
                if payload is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/xml; charset=utf-8")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            prefix = f"http://127.0.0.1:{server.server_port}"
            manifest = [source(k, "google_news" if k == "caixin" else "rss", prefix + "/" + k)
                        for k in ["rss", "atom", "rdf", "caixin", "empty", "absent"]]
            try:
                result = collector.collect(manifest, Path(os.environ["TRENDRADAR_DIR"]),
                                           "2026-11-20T11:00:00Z")
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual({k: v["status"] for k, v in result.items()},
                         {"rss": "ok", "atom": "ok", "rdf": "ok", "caixin": "ok",
                          "empty": "empty", "absent": "failed"}, json.dumps(result))
        self.assertEqual(result["rdf"]["items"][0]["source_time"], "2026-11-20T10:15:00Z")
        self.assertEqual(result["rss"]["items"][0]["guid"], "https://example.org/report")
        self.assertEqual([i["url"] for i in result["caixin"]["items"]], ["https://news.google.com/a/1"])
        self.assertEqual(calls.count("/caixin"), 1)
