import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hotnews import translate


def item(title, source="weibo"):
    return {"title": title, "source_id": source}


def event(title, *items):
    return {"title": title, "items": list(items)}


class TitleTranslationTests(unittest.TestCase):
    def test_cross_source_dedup_cache_titles_and_keep_evidence(self):
        first = "中国发布新政策"
        second = "国际新闻"
        mixed = event(first, item(first), item("China announces a new policy", "bbc-world"),
                      item(first, "thepaper"))
        other = event(second, item(second, "ifanr"))
        cache = {}
        translator = Mock(return_value=["China announces a new policy", "World news"])
        result = translate.enrich_titles([mixed, other], cache, translator)
        self.assertEqual(result, {"eligible": 2, "cached": 0, "translated": 2, "remaining": 0})
        translator.assert_called_once_with([first, second])
        self.assertEqual(first, mixed["title"])
        self.assertEqual(first, mixed["items"][0]["title"])
        self.assertEqual(mixed["title_en"], mixed["items"][0]["title_en"])
        self.assertEqual(mixed["title_en"], mixed["items"][2]["title_en"])
        self.assertNotIn("title_en", mixed["items"][1])
        translator.reset_mock()
        result = translate.enrich_titles([mixed], cache, translator)
        translator.assert_not_called()
        self.assertEqual(len(cache), 1)
        self.assertEqual(result["cached"], 1)

    def test_edited_title_is_new_and_prunes_old_translation(self):
        post = event("中国新闻", item("中国新闻"))
        cache = {}
        translate.enrich_titles([post], cache, lambda _: ["Chinese news"])
        post["title"] = post["items"][0]["title"] = "今日中国新闻"
        translator = Mock(return_value=["Chinese news today"])
        translate.enrich_titles([post], cache, translator)
        translator.assert_called_once_with(["今日中国新闻"])
        self.assertEqual(post["title_en"], "Chinese news today")
        self.assertEqual(list(cache.values()), ["Chinese news today"])

    def test_cold_restore_checks_model_revision_and_uses_member_original(self):
        old = event("Chinese news", item("中国新闻"))
        translated = {}
        translate.enrich_titles([old], translated, lambda _: ["China news"])
        feed = {"translation_version": translate.TRANSLATION_VERSION, "events": [old]}
        self.assertEqual(translate.recover_translations(feed), translated)
        feed["translation_version"] = "old model"
        self.assertEqual(translate.recover_translations(feed), {})

    def test_english_sources_and_kana_headlines_keep_originals(self):
        titles = [("China News 中国", "cgtn-world"), ("English news", "thepaper"),
                  ("日本のニュース", "weibo"), ("科技 news", "zhihu")]
        stories = [event(title, item(title, source)) for title, source in titles]
        translator = Mock(return_value=["Tech news"])
        translate.enrich_titles(stories, {}, translator)
        translator.assert_called_once_with(["科技 news"])
        self.assertTrue(all("title_en" not in entry for entry in stories[:3]))

    def test_model_unavailable_reuses_cache_and_does_not_lose_new_stories(self):
        old = event("旧新闻", item("旧新闻"))
        new = event("新新闻", item("新新闻"))
        cache = {}
        translate.enrich_titles([old], cache, lambda _: ["Old news"])
        with patch.dict(os.environ, HOTNEWS_TRANSLATION_MODEL="missing"), \
             patch.object(translate, "_local_translator", side_effect=RuntimeError("unavailable")), \
             self.assertLogs(translate.LOG, level="WARNING"):
            result = translate.enrich_titles([old, new], cache)
        self.assertEqual(result, {"eligible": 2, "cached": 1, "translated": 0, "remaining": 1})
        self.assertEqual(old["title_en"], "Old news")
        self.assertNotIn("title_en", new)
        self.assertEqual(new["items"][0]["title"], "新新闻")
        self.assertEqual(translate.enrich_titles([old, new], cache, lambda _: ["New news"])["translated"], 1)

    def test_invalid_outputs_never_replace_originals(self):
        stories = [event(title, item(title)) for title in ["中文", "第三条", "第四条"]]
        cache = {}
        result = translate.enrich_titles(stories, cache, lambda _: ["", "中文", "123"])
        self.assertFalse(cache)
        self.assertEqual(result["remaining"], 3)
        with self.assertLogs(translate.LOG, level="WARNING"):
            result = translate.enrich_titles(stories, cache, lambda _: ["only one"])
        self.assertEqual(result["remaining"], 3)

    def test_prepare_checks_existing_files_and_never_accepts_partial_downloads(self):
        good = b"local model"
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(translate, "FILES", {"model.bin": hashlib.sha256(good).hexdigest()}), \
             patch.object(translate.time, "sleep"):
            root = Path(tmp)
            with patch.object(translate.urllib.request, "urlopen", side_effect=[
                io.BytesIO(b"partial"), io.BytesIO(good),
            ]) as download:
                translate.prepare_model(root)
                self.assertEqual(download.call_count, 2)
                translate.prepare_model(root)
                self.assertEqual(download.call_count, 2)
            self.assertEqual((root / "model.bin").read_bytes(), good)
            self.assertEqual([p.name for p in root.iterdir()], ["model.bin"])
