"""A single collection cycle. All time values are UTC."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def registry():
    rows = read(ROOT / "config/sources.yaml")  # JSON is also valid YAML 1.2.
    ids = {s["source_id"] for s in rows}
    baseline = {s["source_id"] for s in rows if s["baseline"]}
    assert len(ids) == len(rows) == 14
    assert baseline == {"weibo", "thepaper", "nyt-world", "npr-world", "ap-youtube", "ap-bluesky", "hackernews"}
    assert Counter(s["market"] for s in rows) == {"CN": 6, "US": 5, "GB": 1, "DE": 1, "QA": 1}
    return rows


def fetch_public(base, file):
    if not base:
        return None
    url = base.rstrip("/") + "/" + file + "?check=" + str(time.time_ns())
    req = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "HotNewsTrial/1.0"})
    try:
        with urlopen(req, timeout=12) as res:
            return json.load(res)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def new_state():
    return {"schema_version": SCHEMA, "sources": {}, "engine": {}, "run_number": 0}


def valid_state(state):
    if not (isinstance(state, dict) and state.get("schema_version") == SCHEMA
            and isinstance(state.get("sources"), dict) and isinstance(state.get("engine"), dict)
            and isinstance(state.get("run_number"), int)):
        return False
    try:
        for info in state["sources"].values():
            if not isinstance(info, dict):
                return False
            for key in ("last_good_at", "next_due_at"):
                if info.get(key):
                    instant(info[key])
        engine = state["engine"]
        if not engine:
            return not any(x.get("last_good_at") for x in state["sources"].values())
        if not all(isinstance(engine.get(k), dict) for k in ("items", "aliases", "clusters", "last_good")):
            return False
        for key, item in engine["items"].items():
            if key != item["item_id"] or item["event_id"] not in engine["clusters"]:
                return False
            instant(item["first_seen_at"])
            instant(item["last_seen_at"])
        for anchor in engine["clusters"].values():
            if not isinstance(anchor["features"], dict) or not isinstance(anchor["links"], list):
                return False
        return all(i in engine["items"] for i in engine["aliases"].values())
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def next_slot(now, minutes):
    offset = 3 * 60
    size = minutes * 60
    boundary = (int(now.timestamp()) - offset) // size * size + size + offset
    return stamp(datetime.fromtimestamp(boundary, timezone.utc))


def due_sources(sources, state, now):
    return [s for s in sources if not state["sources"].get(s["source_id"], {}).get("next_due_at")
            or instant(state["sources"][s["source_id"]]["next_due_at"]) <= now]


def update_health(sources, state, results, now):
    statuses, public = [], []
    for src in sources:
        sid = src["source_id"]
        info = state["sources"].setdefault(sid, {})
        result = results.get(sid)
        items = (result or {}).get("items", [])
        status = "not_due"
        if result is not None:
            status = result["status"]
            if status == "ok" and items:
                snapshot = sorted((str(x.get("guid") or x.get("url")), x["title"], x["url"], str(x.get("rank") or "")) for x in items)
                signature = digest(snapshot)
                status = "unchanged" if signature == info.get("digest") else "changed"
                info.update(digest=signature, last_good_at=stamp(now))
            elif status == "ok":
                status = "empty"
            info["status"] = status
            message = str(result.get("error", ""))
            info["issue"] = "rate_limited" if "429" in message else status
            info["next_due_at"] = (next_slot(now, src["interval_minutes"]) if status in {"changed", "unchanged"}
                                   else stamp(now + timedelta(seconds=max(src["interval_minutes"] * 60, 1800,
                                                                         result.get("retry_after_seconds", 0)))))
        statuses.append({"source_id": sid, "status": status, "count": len(items),
                         "error": (result or {}).get("error", ""), "sample_url": items[0]["url"] if items else None,
                         "time_kind": ("source" if items[0].get("source_time") else "first_seen") if items else None,
                         "last_good_at": info.get("last_good_at"), "next_due_at": info.get("next_due_at")})
        # Both successful dispositions have the same visible health.
        health = info.get("status", "not_due")
        rendered = {**src, "status": "unchanged" if health in ("changed", "unchanged") else health}
        if health in ("empty", "failed"):
            rendered.update(issue=info.get("issue", health), retry_at=info.get("next_due_at"))
        public.append(rendered)
    return statuses, public


def build_cycle(state, sources, results, now, dist, public_manifest, release_id):
    from .events import update_events
    from .translate import TRANSLATION_VERSION, enrich_titles
    statuses, public_sources = update_health(sources, state, results, now)
    projection = update_events(state, results, sources, stamp(now))
    stats = enrich_titles(projection["events"], state.setdefault("translations", {}),
                          chinese_sources={s["source_id"] for s in sources if s["language"] == "zh"})
    print("English headline coverage:", stats)
    for event in projection["events"]:
        event.pop("heat", None)
    content = dict(schema_version=SCHEMA, translation_version=TRANSLATION_VERSION, sources=public_sources, **projection)
    ui = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "site").rglob("*")) if p.is_file()}
    value = digest([ui, content])
    good = any(row["status"] in {"changed", "unchanged"} for row in statuses)
    if results:
        state["last_batch_good"] = good
    # A deploy-only tick can publish a changed UI or grouping rule from saved data.
    from_checkpoint = not results and state.get("last_batch_good", True) and bool(projection["events"])
    different = value != (public_manifest or {}).get("publish_hash")
    changed = (good or from_checkpoint) and different
    if changed:
        shutil.copytree(ROOT / "site", dist, dirs_exist_ok=True)
        ui_hash = digest(ui)
        index = dist / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace("__UI_HASH__", ui_hash), encoding="utf-8")
        write(dist / "feed.json", {**content, "published_at": stamp(now), "release_id": release_id, "publish_hash": value})
        write(dist / "manifest.json", {"schema_version": SCHEMA, "release_id": release_id, "publish_hash": value,
                                       "ui_hash": ui_hash})
    return changed, statuses, value


def main():
    from .collector import collect
    from .events import recover_engine
    from .translate import recover_translations
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path(".runtime"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--page", default="")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    start = time.monotonic()
    now = datetime.now(timezone.utc)
    src = registry()
    state_file = args.work / "checkpoint/state.json"
    state = read(state_file) if state_file.exists() else new_state()
    if not valid_state(state):
        raise ValueError("Invalid checkpoint")
    run_id = os.environ.get("GITHUB_RUN_ID", "local-" + str(int(now.timestamp())))
    number = int(os.environ.get("GITHUB_RUN_NUMBER", "0"))
    if number and number < state["run_number"]:
        raise RuntimeError("Stale run would replace a newer checkpoint")
    manifest_known = True
    try:
        public_manifest = fetch_public(args.page, "manifest.json")
    except (OSError, ValueError) as exc:
        manifest_known, public_manifest = False, None
        print("Pages manifest temporarily unavailable:", type(exc).__name__)
    if not state_file.exists() and not manifest_known:
        raise RuntimeError("Cold recovery deferred until the public manifest can be checked")
    if not state_file.exists() and public_manifest:
        public_feed = fetch_public(args.page, "feed.json")
        if (not public_feed or public_feed.get("schema_version") != SCHEMA
                or public_feed.get("publish_hash", public_manifest["publish_hash"]) != public_manifest["publish_hash"]):
            raise RuntimeError("Cold recovery deferred until the matching public feed is available")
        state["engine"] = recover_engine(public_feed)
        state["translations"] = recover_translations(public_feed)
        if public_manifest.get("trial_basis") == "public-v1" and public_manifest.get("trial_start"):
            state.update(trial_basis="public-v1", trial_start=stamp(instant(public_manifest["trial_start"])))
    due = due_sources(src, state, now)
    results = collect(due, args.upstream.resolve(), stamp(now)) if due else {}
    for s in due:
        results.setdefault(s["source_id"], {"status": "failed", "items": [], "error": "missing result"})
    changed, statuses, fingerprint = build_cycle(state, src, results, now, args.dist, public_manifest, run_id)
    ready = all(s.get("last_good_at") for s in state["sources"].values())
    if state.get("trial_basis") != "public-v1":
        state.pop("trial_start", None)
        state["trial_basis"] = "public-v1"
    if args.page and public_manifest and ready and not args.preflight and not state.get("trial_start"):
        # Start at the next full schedule slot, after all sources have delivered at least once.
        state["trial_start"] = next_slot(now, 10)
    if changed and state.get("trial_start"):
        release = read(args.dist / "manifest.json")
        release.update(trial_basis=state["trial_basis"], trial_start=state["trial_start"])
        write(args.dist / "manifest.json", release)
    state["run_number"] = number
    write(state_file, state)
    elapsed = round(time.monotonic() - start, 2)
    observation = {"schema_version": SCHEMA, "as_of": stamp(now), "run_id": run_id, "run_number": number,
                   "trigger": os.environ.get("GITHUB_EVENT_NAME", "local"), "trial_start": state.get("trial_start"),
                   "trial_basis": state["trial_basis"],
                   "statuses": statuses, "results": results, "build_seconds": elapsed, "publish_hash": fingerprint,
                   "preflight": args.preflight, "publish_requested": changed and manifest_known and not args.preflight,
                   "commit": os.environ.get("GITHUB_SHA")}
    write(args.work / "sample/observation.json", observation)
    print("Nguồn từng lấy thành công:", sum(bool(x.get("last_good_at")) for x in statuses), "/", len(src))
    report = "\n".join(["| Nguồn | Kết quả | Số bài | Loại giờ | Link mẫu / lỗi |", "|---|---|---:|---|---|"]
                       + [f'| {r["source_id"]} | {r["status"]} | {r["count"]} | {r["time_kind"] or ""} | {r["sample_url"] or r["error"] or ""} |' for r in statuses])
    print(report)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(report + "\n")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write("changed=" + str(changed and manifest_known).lower() + "\n")
            out.write("publish_hash=" + fingerprint + "\n")


if __name__ == "__main__":
    main()
