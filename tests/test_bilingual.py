import json
import unittest
from pathlib import Path

from hotnews.events import update_events
from hotnews.pipeline import registry


def score_pairs():
    examples = json.loads((Path(__file__).parent / "fixtures/bilingual.json").read_text(encoding="utf-8"))["pairs"]
    counts = {"positive":0, "false_merge":0}
    errors = []
    for i, (topic, match, en, zh) in enumerate(examples):
        results = {}
        for sid, title, host in [("nyt-world", en, "nytimes.com"), ("thepaper", zh, "thepaper.cn")]:
            url = f"https://{host}/example/{i}"
            results[sid] = {"status":"ok", "items":[{"title":title, "url":url, "guid":url, "source_time":"2026-11-20T12:00:00Z"}]}
        output = update_events({}, results, registry(), "2026-11-20T12:03:00Z")
        merged = len(output["events"]) == 1
        if merged:
            counts["positive" if match else "false_merge"] += 1
        if merged != bool(match):
            errors.append((topic, i, en))
    return counts, errors


class BilingualAcceptance(unittest.TestCase):
    def test_frozen_pairs(self):
        counts, errors = score_pairs()
        self.assertEqual(counts["false_merge"], 0, errors)
        self.assertGreaterEqual(counts["positive"], 8, errors)


if __name__ == "__main__":
    print(score_pairs())
