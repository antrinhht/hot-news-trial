import copy
import json
import unittest

from hotnews.events import canonical_url, recover_engine, update_events


NOW = "2026-11-20T12:00:00Z"
DAY_AGO = "2026-11-19T12:00:00Z"
SOURCES = [
    {"source_id": sid, "owner_id": owner, "kind": kind, "market": market, "provider": provider}
    for sid, owner, kind, market, provider in [
        ("ap-youtube", "ap", "editorial", "US", "rss"),
        ("ap-bluesky", "ap", "editorial", "US", "rss"),
        ("npr-world", "npr", "editorial", "US", "rss"),
        ("bbc-world", "bbc", "editorial", "GB", "rss"),
        ("weibo", "weibo", "community", "CN", "newsnow"),
        ("zhihu", "zhihu", "community", "CN", "newsnow"),
        ("thepaper", "thepaper", "editorial", "CN", "newsnow"),
        ("hackernews", "hn", "community", "US", "rss"),
    ]
]


def article(title, url="https://example.org/story/42", *, when=NOW, **extra):
    return {"title": title, "url": url, "source_time": when, **extra}


def data(**sources):
    return {key.replace("_", "-"): {"status": "ok", "items": items}
            for key, items in sources.items()}


class EventsTest(unittest.TestCase):
    def test_replay_and_heat_owners(self):
        rows = data(ap_youtube=[article("OpenAI releases GPT-7")],
                    ap_bluesky=[article("OpenAI releases GPT-7")],
                    npr_world=[article("OpenAI releases GPT-7", when=DAY_AGO)])
        state = {}
        feed = update_events(state, rows, SOURCES, NOW)
        self.assertEqual(feed, update_events(state, rows, SOURCES, NOW))
        event, = feed["events"]
        self.assertEqual(event["owners"], ["ap", "npr"])
        self.assertEqual(event["heat"], 1.5)
        self.assertEqual(feed["hot"], [event["event_id"]])
        self.assertTrue(feed["selected"])
        self.assertEqual(len(event["items"]), 3)

    def test_syndication_and_community_are_distinct(self):
        rows = data(ap_youtube=[article("Two sides sign agreement")],
                    npr_world=[article("Two sides sign agreement", origin_owner_id="ap")],
                    hackernews=[article("A discussion of the signed agreement")])
        event, = update_events({}, rows, SOURCES, NOW)["events"]
        self.assertEqual(event["owners"], ["ap", "hn"])
        self.assertEqual(event["editorial_owners"], ["ap"])
        self.assertEqual(event["selected_reason"], "")

    def test_photo_credit_is_not_a_byline(self):
        rows = data(npr_world=[article("Earthquake in Japan (photo: AP)")],
                    bbc_world=[article("Earthquake in Japan (photo: AP)")])
        event, = update_events({}, rows, SOURCES, NOW)["events"]
        self.assertEqual(event["editorial_owners"], ["bbc", "npr"])

    def test_cross_language_signature_and_conflicts(self):
        usd = article("Federal Reserve cuts rates by 25 basis points", "https://example.org/one")
        cn = article("美联储降息25个基点", "https://example.net/two")
        for right, expected in [
            (cn, 1),
            ({**cn, "title": "市场预计美联储降息25个基点"}, 2),
            ({**cn, "title": "美联储加息25个基点"}, 2),
            ({**cn, "title": "美联储降息50个基点"}, 2),
            ({**cn, "title": "美联储否认降息25个基点"}, 2),
            ({**cn, "title": "美联储降息25个基点相关新闻汇总"}, 2),
        ]:
            with self.subTest(title=right["title"]):
                feed = update_events({}, data(npr_world=[usd], thepaper=[right]), SOURCES, NOW)
                self.assertEqual(len(feed["events"]), expected)

    def test_cross_language_versions_and_explicit_days(self):
        def run(left, right):
            return len(update_events({}, data(
                npr_world=[article(left, "https://a.example/post/x")],
                thepaper=[article(right, "https://b.example/post/y")]), SOURCES, NOW)["events"])
        self.assertEqual(run("Apple unveils iPhone 18 on November 18", "苹果于11月18日发布iPhone 18"), 1)
        self.assertEqual(run("Apple unveils iPhone 18 on November 18", "苹果于11月19日发布iPhone 18"), 2)
        self.assertEqual(run("Apple unveils iPhone 18", "苹果发布iPhone 19"), 2)
        self.assertEqual(run("Apple unveils new devices", "苹果发布新设备"), 2)

    def test_unsafe_links_and_shared_homepage(self):
        self.assertEqual(canonical_url("javascript:alert(1)"), "")
        self.assertEqual(canonical_url("https://bad:abc/42"), "")
        self.assertEqual(canonical_url("https://user:pass@example.org/path"), "")
        self.assertEqual(canonical_url("https://Example.org/story?id=4&uTm_Source=ad#slide"),
                         "https://example.org/story?id=4#slide")
        self.assertNotEqual(canonical_url("https://x.org/?id=42"), canonical_url("https://x.org/?id=43"))
        rows = data(npr_world=[article("Update from Washington", "https://example.org")],
                    bbc_world=[article("A different report", "https://example.org")])
        self.assertEqual(len(update_events({}, rows, SOURCES, NOW)["events"]), 2)

    def test_cold_start_and_social_participant_cap(self):
        state = {}
        rows = data(weibo=[article("Old topic", when=None)], zhihu=[article("Old topic", when=None)],
                    thepaper=[article("Original editorial", when=None)])
        first = update_events(state, rows, SOURCES, NOW)
        self.assertFalse(first["hot"])
        later = "2026-11-20T12:10:00Z"
        second_rows = copy.deepcopy(rows)
        for src in second_rows:
            second_rows[src]["items"].append(article("Latest topic", url="https://example.org/story/new", when=None))
        second = update_events(state, second_rows, SOURCES, later)
        self.assertEqual(len(second["hot"]), 1)
        hot = next(e for e in second["events"] if e["event_id"] in second["hot"])
        self.assertEqual(hot["owners"], ["cn_social_hotlist", "thepaper"])
        self.assertEqual(hot["time_kind"], "first_seen")
        self.assertEqual(hot["heat"], 2.0)

    def test_ids_persist_after_edits_deployment_failure_and_recovery(self):
        first = article("Title first observed", guid="abc-12")
        state = {}
        original = update_events(state, data(bbc_world=[first]), SOURCES, NOW)["events"][0]
        later = "2026-11-20T12:10:00Z"
        edited = {**first, "title": "Title corrected", "url": "https://example.org/story/final",
                  "source_time": later}
        for checkpoint in [json.loads(json.dumps(state)), {"engine": recover_engine({"events": [original]})}]:
            with self.subTest(recovery=bool(checkpoint)):
                event, = update_events(checkpoint, data(bbc_world=[edited]), SOURCES, later)["events"]
                self.assertEqual(event["event_id"], original["event_id"])
                self.assertEqual(event["items"][0]["item_id"], original["items"][0]["item_id"])
                self.assertEqual(event["items"][0]["first_seen_at"], NOW)
                self.assertEqual(event["latest_at"], NOW)
                self.assertEqual(event["title"], "Title corrected")
                self.assertEqual(event["title"], update_events(checkpoint, {"bbc-world": {"status": "failed"}},
                                                             SOURCES, later)["events"][0]["title"])

    def test_window_boundaries_and_future(self):
        rows = data(bbc_world=[article("Too old", "https://x.org/old", when="2026-11-18T12:00:00Z"),
                               article("One second inside", "https://x.org/in", when="2026-11-18T12:00:01Z"),
                               article("Future", "https://x.org/future", when="2026-11-20T12:01:00Z")])
        state = {}
        feed = update_events(state, rows, SOURCES, NOW)
        self.assertEqual([x["title"] for x in feed["events"]], ["One second inside"])
        later = update_events(state, {}, SOURCES, "2026-11-20T12:01:00Z")
        self.assertEqual([x["title"] for x in later["events"]], ["Future"])

    def test_crowded_title_cannot_bridge(self):
        rows = data(bbc_world=[article("Fed cuts rates by 25 basis points", "https://a.org/post1"),
                              article("OpenAI releases GPT-7", "https://a.org/post2")],
                    thepaper=[article("新闻汇总：美联储降息25个基点，OpenAI发布GPT-7", "https://b.org/roundup")])
        self.assertEqual(len(update_events({}, rows, SOURCES, NOW)["events"]), 3)


if __name__ == "__main__":
    unittest.main()
