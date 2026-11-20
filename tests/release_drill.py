"""Isolated release drills. The workflow carries a real Actions artifact across jobs."""
import argparse
import os
from pathlib import Path

from hotnews.events import recover_engine
from hotnews.pipeline import (build_cycle, instant, new_state, read, registry,
                              valid_state, write)


def run(phase, work):
    checkpoint = work / "state.json"
    exported = work / "dist"
    now = instant("2026-11-20T12:03:00Z")
    sources = registry()
    url = "https://example.org/acceptance/announcement"
    data = {"bbc-world":{"status":"ok","items":[{"url":url,"title":"An example announcement",
            "guid":"example", "source_time":"2026-11-20T11:45:00Z"}]},
            "ap-bluesky":{"status":"failed","items":[],"error":"simulated network timeout"}}
    if phase == "prepare":
        state = new_state()
        changed, statuses, _ = build_cycle(state, sources, data, now, exported, None, "control")
        assert changed
        assert next(x for x in statuses if x["source_id"] == "ap-bluesky")["status"] == "failed"
        assert read(exported / "feed.json")["events"]
        write(checkpoint, state)
        return
    state = read(checkpoint)
    assert valid_state(state)
    manifest = read(exported / "manifest.json")
    original = read(exported / "feed.json")
    # Unchanged public release; this branch must not publish again.
    changed, _, _ = build_cycle(state, sources, data, now, exported, manifest, "unchanged")
    assert not changed
    # The preceding job deliberately rejected the deployment after saving the real artifact.
    changed, _, fingerprint = build_cycle(state, sources, data, now, exported, None, "retry")
    assert changed and fingerprint == manifest["publish_hash"]
    # Recovery from only the public JSON retains IDs and the initial detection clock.
    recovered = new_state()
    recovered["engine"] = recover_engine(original)
    build_cycle(recovered, sources, data, now, exported, None, "restored")
    restored = read(exported / "feed.json")
    assert original["latest"] == restored["latest"]
    assert original["events"][0]["items"][0]["first_seen_at"] == restored["events"][0]["items"][0]["first_seen_at"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare","verify"])
    parser.add_argument("--work", type=Path, default=Path(".runtime/drill"))
    args = parser.parse_args()
    run(args.phase, args.work)
