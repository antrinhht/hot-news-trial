"""Paraphrased regression fixtures for four recurring headline constructions."""

import copy
import unittest

from hotnews.events import update_events
from hotnews.pipeline import registry


NOW = "2026-11-20T12:00:00Z"
SOURCES = registry()


def row(title, url, **fields):
    return {"title": title, "url": url, "source_time": NOW, **fields}


def result(*rows):
    return {"status": "ok", "items": list(rows)}


def pair(left, right):
    return {"bbc-world": result(row(left, "https://bbc.example/article/1")),
            "thepaper": result(row(right, "https://thepaper.example/article/2"))}


def old_state(partitions):
    # v1 checkpoints may have only singleton anchors even for independently reported facts.
    combined = {key: {} for key in ("items", "aliases", "last_good", "clusters")}
    for partition in partitions:
        state = {}
        update_events(state, partition, SOURCES, NOW)
        for eid, anchor in state["engine"]["clusters"].items():
            anchor.pop("title", None)
            anchor.pop("clock", None)
        for key in combined:
            combined[key].update(state["engine"][key])
    return {"engine": combined}


class LiveConstructions(unittest.TestCase):
    def test_positive_specific_occurrences(self):
        for left, right in [
            ("Global court rules Myanmar committed genocide against Rohingya",
             "Myanmar committed genocide against the Rohingya, top court finds"),
            ("Anthropic's Nasdaq debut", "Anthropic 敲钟上市"),
            ("Panda Lulu dies after illness", "Lulu the panda has died"),
            ("Trump grants Alex Reed a posthumous pardon", "Trump posthumously pardons athlete Alex Reed"),
        ]:
            with self.subTest(left=left):
                projection = update_events({}, pair(left, right), SOURCES, NOW)
                self.assertEqual(len(projection["selected"]), 1)
                self.assertEqual(len(projection["hot"]), 1)

    def test_hard_negatives(self):
        for left, right in [
            ("Court rules Myanmar committed genocide against the Rohingya",
             "Rohingya families react to court ruling"),
            ("Trump grants Alex Reed a posthumous pardon", "Trump posthumously pardons athlete Paul Bird"),
            ("Trump grants Alex Reed a posthumous pardon", "Trump has not posthumously pardoned Alex Reed"),
            ("Panda Lulu dies at wildlife park", "Panda Lulu undergoes surgery"),
            ("Panda Lulu dies in care", "Panda Lola dies in care"),
            ("Anthropic's Nasdaq debut", "How did Anthropic's stock market debut unfold?"),
            ("Anthropic's Nasdaq debut", "Anthropic's NYSE debut"),
            ("OpenAI's Nasdaq debut", "Anthropic's Nasdaq debut"),
            ("Unknown company Sunrise celebrates US stock market debut",
             "Timber celebrates US stock market debut"),
            ("Xi meets Trump in Beijing", "Xi meets Trump and Putin in Beijing"),
        ]:
            with self.subTest(left=left, right=right):
                projection = update_events({}, pair(left, right), SOURCES, NOW)
                self.assertEqual(len(projection["events"]), 2)

    def test_migration_preserves_ids_clocks_and_prefers_established_group(self):
        existing = {"bbc-world": result(row("Anthropic's Nasdaq debut", "https://example.org/report/1")),
                    "hackernews": result(row("Anthropic public trading", "https://example.org/report/1"))}
        late = {"thepaper": result(row("Anthropic 敲钟上市", "https://example.cn/story/2"))}
        state = old_state([existing, late])
        items = state["engine"]["items"]
        prior = {k: (v["first_seen_at"], v["source_time"]) for k, v in items.items()}
        survivor = next(i["event_id"] for i in items.values() if i["source_id"] == "bbc-world")
        first = update_events(state, {}, SOURCES, NOW)
        self.assertEqual(first["latest"], [survivor])
        self.assertEqual(prior, {k: (v["first_seen_at"], v["source_time"]) for k, v in items.items()})
        frozen = copy.deepcopy(state)
        self.assertEqual(first, update_events(state, {}, SOURCES, NOW))
        self.assertEqual(state, frozen)

    def test_ambiguous_bridge_keeps_all_saved_clusters(self):
        a, b, c = "https://example.org/report/a", "https://example.org/report/b", "https://example.org/report/c"
        state = old_state([{"bbc-world": result(row("First", a))},
                           {"thepaper": result(row("Second", b, primary_url=a))},
                           {"npr-world": result(row("Third", c, primary_url=b))}])
        old = set(state["engine"]["clusters"])
        after = update_events(state, {}, SOURCES, NOW)
        self.assertEqual(set(after["latest"]), old)
