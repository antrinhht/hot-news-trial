import unittest
from datetime import timedelta

from hotnews.evaluate import markdown, report
from hotnews.pipeline import instant, registry, stamp

START = instant("2026-11-20T13:03:00Z")


def sample(index, inputs, *, trigger="schedule", observer=None):
    clock = stamp(START + timedelta(minutes=10 * index))
    return {
        "run": {
            "event": trigger,
            "created_at": clock,
            "id": index + 100,
            "html_url": f"https://github.com/example/runs/{index}",
            "head_sha": "abc",
        },
        "jobs": [
            {
                "name": "build",
                "started_at": clock,
                "completed_at": stamp(START + timedelta(minutes=10 * index + 1)),
                "conclusion": "success",
            }
        ],
        "artifact_bytes": 120,
        "observer": observer or {},
        "observation": {
            "as_of": clock,
            "run_id": str(index + 100),
            "trial_start": stamp(START),
            "trial_basis": "public-v1",
            "results": inputs,
            "statuses": [
                {"source_id": s, "status": "changed", "last_good_at": clock}
                for s in inputs
            ],
        },
    }


def story(title, url):
    return {
        "status": "ok",
        "items": [
            {"title": title, "url": url, "source_time": stamp(START), "guid": url}
        ],
    }


class TrialEvaluationTests(unittest.TestCase):
    def test_elapsed_grid_and_mature_samples_only(self):
        url = "https://example.test/new"
        before = sample(0, {})
        recent = sample(
            1,
            {},
            observer={
                "observed_at": stamp(START + timedelta(minutes=10)),
                "candidates": [{"source_id": "npr-world", "url": url}],
                "public_items": [],
                "polls": [{"status": "failed"}, {"status": "ok"}],
            },
        )
        for entry in (before, recent):
            entry["observation"]["trial_start"] = None
        short = report([before, recent], 48, START + timedelta(minutes=11), registry())[
            "operations"
        ]
        self.assertEqual(
            (short["slots"], short["expected_slots"], short["planned_slots"]),
            (2, 2, 288),
        )
        self.assertEqual(short["schedule_rate"], 1)
        self.assertEqual(short["observer_attempts"], 2)
        self.assertEqual(short["observer_successful_polls"], 1)
        self.assertIsNone(short["latency_within_20m"])
        self.assertIsNone(short["latency_samples"][0]["within_20m"])
        self.assertEqual(short["mature_fast_sample_count"], 0)
        self.assertEqual(short["latency_cohorts"][10]["pending"], 1)
        self.assertEqual(short["latency_cohorts"][10]["missed"], 0)
        mature = report(
            [before, recent], 48, START + timedelta(minutes=30), registry()
        )["operations"]
        self.assertEqual(mature["latency_within_20m"], 0)
        self.assertEqual(mature["latency_cohorts"][10]["missed"], 1)

    def test_qualification_freezes_corrected_bylines_at_observation(self):
        url = "https://example.test/wire/one"
        original = sample(0, {"bbc-world": story("News", url)})
        corrected = story("Revised", url)
        corrected["items"][0]["origin_owner_id"] = "ap"
        addition = sample(
            1, {"bbc-world": corrected, "npr-world": story("New reporting", url)}
        )
        ab = report([original, addition], 48, START + timedelta(hours=49), registry())[
            "comparison"
        ]
        event = ab["additional"][0]
        self.assertEqual(
            event["first_qualified_at"], stamp(START + timedelta(minutes=10))
        )
        self.assertEqual(
            {link["participant_id"] for link in event["qualification_links"]},
            {"ap", "npr"},
        )
        self.assertEqual(
            ab["source_contributions"]["bbc-world"]["known_wire_copies"], 1
        )
        # The same two URLs ultimately both identify a single AP report.
        later = story("Revised attribution", url)
        later["items"][0]["origin_owner_id"] = "ap"
        correction = sample(2, {"npr-world": later})
        adjusted = report(
            [original, addition, correction],
            48,
            START + timedelta(hours=49),
            registry(),
        )["comparison"]
        self.assertEqual(adjusted["additional"], [])
        self.assertEqual(adjusted["full"]["corroborated"], 0)

    def test_new_owner_and_id_drift_between_independent_replays(self):
        base = registry()
        url = "https://example.test/story/12"
        first = sample(0, {"bbc-world": story("An event", url)})
        second = sample(
            1,
            {
                "ap-youtube": story("Same document", url),
                "ap-bluesky": story("Social repost", url),
            },
        )
        result = report([second, first], 48, START + timedelta(hours=49), base)
        facts = result["comparison"]
        self.assertEqual(facts["baseline"]["corroborated"], 0)
        self.assertEqual(facts["full"]["corroborated"], 1)
        self.assertEqual(len(facts["additional"]), 1)
        self.assertEqual(facts["source_contributions"]["bbc-world"]["first"], 1)
        self.assertEqual(facts["full"]["cross_market"], 1)
        # A baseline-newsroom anchor appears after a new-arm anchor. Event IDs differ.
        third = sample(2, {"npr-world": story("Same document", url)})
        out = report([first, second, third], 48, START + timedelta(hours=49), base)
        self.assertEqual(out["comparison"]["baseline"]["corroborated"], 1)
        self.assertEqual(out["comparison"]["additional"], [])
        self.assertEqual(out["reviewed_clusters"], 0)

    def test_window_grid_cooldown_and_observer_denominator(self):
        source = "https://abc.test/item/1"
        never = "https://abc.test/item/2"
        s0 = sample(
            0,
            {},
            observer={
                "observed_at": stamp(START),
                "candidates": [
                    {"url": source, "source_id": "npr-world"},
                    {"url": never, "source_id": "hackernews"},
                ],
                "public_ids": [],
            },
        )
        s0["observation"]["statuses"] = [
            {
                "source_id": "npr-world",
                "status": "failed",
                "next_due_at": stamp(START + timedelta(minutes=30)),
            }
        ]
        s1 = sample(
            1,
            {},
            trigger="workflow_dispatch",
            observer={
                "observed_at": stamp(START + timedelta(minutes=10)),
                "public_ids": [source + "?utm_source=test"],
                "candidates": [],
            },
        )
        s1["observation"]["statuses"] = [
            {"source_id": "npr-world", "status": "not_due"}
        ]
        result = report([s0, s1], 48, START + timedelta(hours=49), registry())
        ops = result["operations"]
        self.assertEqual(ops["expected_slots"], 288)
        self.assertEqual(ops["slots"], 1)
        self.assertEqual(ops["sources"]["npr-world"]["counts"]["cooldown"], 1)
        self.assertEqual(ops["build_seconds_p95"], 60)
        self.assertEqual(ops["latency_within_20m"], 0.5)
        self.assertEqual(result["status"], "insufficient_data")

    def test_no_preflight_no_trial_claim(self):
        entry = sample(0, {})
        entry["observation"]["trial_start"] = None
        result = report([entry], 48, START + timedelta(days=12), registry())
        self.assertEqual(result["status"], "preflight_pending")
        self.assertEqual(len(result["missing_sources"]), 14)
        self.assertIn("0/14", markdown(result))

    def test_incomplete_run_in_grid_counted_even_without_snapshot(self):
        entry = sample(0, {})
        failed = sample(2, {})
        failed.pop("observation")
        ops = report([entry, failed], 48, START + timedelta(hours=1), registry())[
            "operations"
        ]
        self.assertEqual(ops["slots"], 2)
        self.assertEqual(ops["missing_observation_runs"], 1)

    def test_legacy_preflight_and_slow_observer_are_separate(self):
        entry = sample(0, {})
        entry["observation"].pop("trial_basis")
        self.assertEqual(
            report([entry], 48, START + timedelta(hours=48), registry())["status"],
            "preflight_pending",
        )
        url = "https://example.test/shared"
        current = sample(
            1,
            {},
            observer={
                "observed_at": stamp(START + timedelta(minutes=10)),
                "candidates": [
                    {"source_id": "bbc-world", "url": url},
                    {"source_id": "npr-world", "url": url},
                ],
                "public_items": [],
            },
        )
        later = sample(
            2,
            {},
            observer={
                "observed_at": stamp(START + timedelta(minutes=20)),
                "candidates": [],
                "public_items": [{"source_id": "bbc-world", "url": url}],
                "public_ids": [url],
            },
        )
        op = report(
            [entry, current, later], 48, START + timedelta(hours=48), registry()
        )["operations"]
        self.assertEqual(op["fast_sample_count"], 1)
        self.assertEqual(op["latency_within_20m"], 0)
        self.assertEqual(op["latency_cohorts"][20]["missed"], 0)
