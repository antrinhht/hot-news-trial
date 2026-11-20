import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from hotnews.pipeline import (ROOT, build_cycle, due_sources, instant, new_state,
                              registry, update_health, valid_state)


NOW = instant("2026-11-20T12:03:00Z")


def run_result(source, title="New report", url="https://example.org/report"):
    return {source: {"status": "ok", "items": [{"title": title, "url": url, "guid": url,
                                                "source_time": "2026-11-20T12:02:00Z"}]}}


class WorkflowBehavior(unittest.TestCase):
    def setUp(self):
        self.src = registry()
        self.state = new_state()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name) / "pages"

    def cycle(self, result, clock=NOW, public=None):
        return build_cycle(self.state, self.src, result, clock, self.dist, public, "sample-run")

    def test_registry_and_clock(self):
        self.assertEqual(sum(1440 // s["interval_minutes"] for s in self.src), 1608)
        update_health(self.src, self.state, run_result("bbc-world"), NOW)
        self.assertNotIn("bbc-world", [x["source_id"] for x in due_sources(self.src, self.state, NOW + timedelta(minutes=10))])
        self.assertIn("bbc-world", [x["source_id"] for x in due_sources(self.src, self.state, NOW + timedelta(minutes=20))])

    def test_duplicate_and_failed_deploy_retry(self):
        results = run_result("bbc-world")
        changed, _, sha = self.cycle(results)
        self.assertTrue(changed)
        previous = json.loads((self.dist / "manifest.json").read_text(encoding="utf-8"))
        saved = json.loads(json.dumps(self.state))
        self.assertTrue(valid_state(saved))
        self.assertFalse(self.cycle(results, NOW + timedelta(minutes=20), previous)[0])
        # Simulated remote deployment rejection; validated ingestion survives.
        self.state = saved
        retry, _, sha2 = self.cycle(results, NOW + timedelta(minutes=20), {"publish_hash":"older-release"})
        self.assertTrue(retry)
        self.assertEqual(sha, sha2)

    def test_errors_and_recovery_from_public_json(self):
        from hotnews.events import recover_engine
        results = {**run_result("nyt-world"), "ap-bluesky": {"status":"failed", "items":[], "error":"connection timeout"}}
        changed, rows, sha = self.cycle(results)
        self.assertTrue(changed)
        self.assertEqual(next(r for r in rows if r["source_id"] == "ap-bluesky")["status"], "failed")
        feed = json.loads((self.dist / "feed.json").read_text(encoding="utf-8"))
        unsuccessful, _, _ = self.cycle({"nyt-world":{"status":"empty","items":[]}}, NOW + timedelta(minutes=10), {"publish_hash":sha})
        self.assertFalse(unsuccessful)
        self.assertFalse(self.cycle({}, NOW + timedelta(minutes=15), {"publish_hash":sha})[0])
        self.state = new_state()
        self.state["engine"] = recover_engine(copy.deepcopy(feed))
        self.cycle(results, NOW + timedelta(minutes=30), None)
        recovered = json.loads((self.dist / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(feed["latest"], recovered["latest"])
        self.assertEqual(feed["events"][0]["items"][0]["first_seen_at"], recovered["events"][0]["items"][0]["first_seen_at"])

    def test_deploy_only_tick_uses_checkpoint_for_a_changed_release(self):
        self.cycle(run_result("bbc-world"))
        self.assertTrue(self.cycle({}, NOW, {"publish_hash":"previous-ui"})[0])
        current = json.loads((self.dist / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(self.cycle({}, NOW, current)[0])

    def test_recovered_translation_and_versioned_assets_are_published(self):
        from hotnews.events import recover_engine
        from hotnews.translate import TRANSLATION_VERSION, recover_translations
        self.cycle(run_result("weibo", "中国发布新的贸易政策"))
        feed = json.loads((self.dist / "feed.json").read_text(encoding="utf-8"))
        feed["events"][0]["items"][0]["title_en"] = "China announces a new trade policy"
        self.assertEqual(feed["translation_version"], TRANSLATION_VERSION)
        self.state = new_state()
        self.state["engine"] = recover_engine(feed)
        self.state["translations"] = recover_translations(feed)
        self.assertTrue(self.cycle({}, NOW, {"publish_hash": "previous"})[0])
        published = json.loads((self.dist / "feed.json").read_text(encoding="utf-8"))
        self.assertEqual(published["latest"], feed["latest"])
        self.assertEqual(published["events"][0]["title_en"], "China announces a new trade policy")
        self.assertEqual(published["events"][0]["title"], "中国发布新的贸易政策")
        manifest = json.loads((self.dist / "manifest.json").read_text(encoding="utf-8"))
        html = (self.dist / "index.html").read_text(encoding="utf-8")
        self.assertIn("app.js?v=" + manifest["ui_hash"], html)
        self.assertNotIn("__UI_HASH__", html)
