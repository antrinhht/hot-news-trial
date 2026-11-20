"""Collect trusted Actions evidence, or replay a downloaded trial archive offline."""

import argparse
import gzip
import io
import itertools
import json
import math
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zipfile import ZIP_STORED, ZipFile

from .artifacts import github, list_artifacts
from .events import canonical_url, update_events
from .pipeline import instant, new_state, registry, stamp, write


def percentile95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1] if values else None


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def seconds(start, end):
    return (instant(end) - instant(start)).total_seconds() if start and end else None


def paged(path, field, params):
    page = 1
    while True:
        response = github(path + "?" + urlencode(dict(params, per_page=100, page=page)))
        rows = response[field]
        yield from rows
        if len(rows) < 100:
            return
        page += 1


def load_run(directory, run, artifact, sizes):
    run_id = str(run["id"])
    file = directory / (run_id + ".json.gz")
    if file.exists():
        with gzip.open(file, "rt", encoding="utf-8") as inp:
            old = json.load(inp)
        if (
            old["run"].get("conclusion")
            and old.get("artifact_sizes") is not None
            and old.get("artifact_id") == (artifact or {}).get("id")
        ):
            return
    repo = os.environ["GITHUB_REPOSITORY"]
    jobs = list(
        paged(f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs", "jobs", {})
    )
    entry = {
        "run": {
            k: run.get(k)
            for k in (
                "id",
                "event",
                "created_at",
                "status",
                "conclusion",
                "html_url",
                "head_sha",
            )
        },
        "jobs": [
            {k: j.get(k) for k in ("name", "started_at", "completed_at", "conclusion")}
            for j in jobs
        ],
        "artifact_id": (artifact or {}).get("id"),
        "artifact_bytes": (artifact or {}).get("size_in_bytes", 0),
        "artifact_sizes": sizes,
    }
    if artifact:
        raw = github(
            f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", binary=True
        )
        with ZipFile(io.BytesIO(raw)) as bundle:
            observation = json.loads(bundle.read("observation.json"))
            if (
                str(observation.get("run_id")) != run_id
                or observation.get("schema_version") != 1
                or observation.get("trigger") != run["event"]
                or observation.get("commit") != run["head_sha"]
            ):
                raise ValueError("Observation metadata does not match run " + run_id)
            entry["observation"] = observation
            if "observer.json" in bundle.namelist():
                entry["observer"] = json.loads(bundle.read("observer.json"))
    tmp = file.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=5) as out:
        json.dump(entry, out, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(file)


def sync(directory, now):
    directory.mkdir(parents=True, exist_ok=True)
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("DEFAULT_BRANCH", "main")
    artifacts = {
        str(a["workflow_run"]["id"]): a
        for a in list_artifacts("trial-observation-v1", pages=20)
        if a["workflow_run"]["head_branch"] == branch
    }
    sizes = defaultdict(dict)
    for name in ("checkpoint-v1", "github-pages"):
        for artifact in list_artifacts(name, pages=12):
            sizes[str(artifact["workflow_run"]["id"])][name] = artifact["size_in_bytes"]
    # Per-day queries stay below the REST maximum of 1000 results per filtered query.
    jobs = {}
    for ago in range(10):
        day = (now - timedelta(days=ago)).date().isoformat()
        path = f"repos/{repo}/actions/workflows/publish.yml/runs"
        for run in paged(path, "workflow_runs", {"branch": branch, "created": day}):
            if (
                run["path"] == ".github/workflows/publish.yml"
                and run["run_attempt"] == 1
                and run["event"] in {"schedule", "workflow_dispatch"}
                and run["head_branch"] == branch
                and (run.get("head_repository") or {}).get("full_name", "").lower()
                == repo.lower()
            ):
                jobs[str(run["id"])] = run
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(
            pool.map(
                lambda pair: load_run(
                    directory, pair[1], artifacts.get(pair[0]), sizes[pair[0]]
                ),
                jobs.items(),
            )
        )
    print(f"Synced metadata for {len(jobs)} workflow runs")


def replay(observations, sources):
    state = new_state()
    allowed = {s["source_id"] for s in sources}
    catalog = {}
    for sample in observations:
        clock = sample["as_of"]
        results = {s: r for s, r in sample["results"].items() if s in allowed}
        view = update_events(state, results, sources, clock)
        for event in view["events"]:
            event_id = event["event_id"]
            saved = catalog.setdefault(
                event_id,
                {
                    "event_id": event_id,
                    "items": {},
                    "first_qualified_at": None,
                    "cross_market": False,
                },
            )
            saved["title"] = event["title"]
            changed_provenance = set()
            for item in event["items"]:
                iid = item["item_id"]
                previous = saved["items"].get(iid)
                if previous and previous["participant_id"] != item["participant_id"]:
                    changed_provenance.add(iid)
                saved["items"][iid] = {
                    k: item[k]
                    for k in (
                        "source_id",
                        "url",
                        "first_seen_at",
                        "editorial",
                        "participant_id",
                        "owner_id",
                        "market",
                    )
                }
            if saved["first_qualified_at"] and changed_provenance.intersection(
                saved["qualification_items"]
            ):
                corrected = {
                    saved["items"][iid]["participant_id"]
                    for iid in saved["qualification_items"]
                    if saved["items"][iid]["editorial"]
                }
                if len(corrected) < 2:
                    # A corrected byline can reveal that both links were the same wire report.
                    saved["first_qualified_at"] = None
                    saved.pop("qualification_items")
            if len(event["markets"]) > 1:
                saved["cross_market"] = True
            if (
                not saved["first_qualified_at"]
                and len(set(event["editorial_owners"])) >= 2
            ):
                saved["first_qualified_at"] = clock
                # Values are replaced, never mutated. This retains the exact contemporaneous evidence.
                saved["qualification_items"] = {
                    item["item_id"]: saved["items"][item["item_id"]]
                    for item in event["items"]
                    if item["editorial"]
                }
    return catalog


def evidence(event):
    seen = set()
    links = []
    for item in event["items"].values():
        key = (item["source_id"], item["participant_id"], item["url"])
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "source_id": key[0],
                "owner_id": key[1],
                "url": key[2],
                "market": item["market"],
                "first_seen_at": item["first_seen_at"],
                "editorial": item["editorial"],
            }
        )
    return {
        "event_id": event["event_id"],
        "title": event["title"],
        "first_qualified_at": event["first_qualified_at"],
        "links": links,
        "qualification_links": list(event.get("qualification_items", {}).values()),
    }


def compare(observations, sources):
    base_sources = [s for s in sources if s["baseline"]]
    added = {s["source_id"] for s in sources if not s["baseline"]}
    base = replay(observations, base_sources)
    full = replay(observations, sources)
    base_verified_ids = {
        iid for e in base.values() if e["first_qualified_at"] for iid in e["items"]
    }
    contributions = {s: Counter() for s in sorted(added)}
    additional, review, cross = [], [], []
    for e in full.values():
        members = list(e["items"].values())
        earliest = min(i["first_seen_at"] for i in members)
        first = {i["source_id"] for i in members if i["first_seen_at"] == earliest}
        for sid in first & added:
            contributions[sid]["first" if len(first) == 1 else "joint_first"] += 1
        for i in members:
            if (
                i["source_id"] in added
                and i["editorial"]
                and i["owner_id"] != i["participant_id"]
            ):
                contributions[i["source_id"]]["known_wire_copies"] += 1
        if e["cross_market"] and len(cross) < 20:
            cross.append(evidence(e))
        if not e["first_qualified_at"]:
            continue
        first_owner = {}
        at_threshold = e["qualification_items"].values()
        for i in at_threshold:
            owner = i["participant_id"]
            first_owner[owner] = min(
                first_owner.get(owner, i["first_seen_at"]), i["first_seen_at"]
            )
        threshold_time = sorted(first_owner.values())[1]
        second = {
            i["source_id"]
            for i in at_threshold
            if i["editorial"]
            and i["first_seen_at"] == threshold_time
            and first_owner.get(i["participant_id"]) == threshold_time
        }
        for sid in second & added:
            contributions[sid]["second" if len(second) == 1 else "joint_second"] += 1
        row = evidence(e)
        if base_verified_ids.isdisjoint(e["items"]) and any(
            i["source_id"] in added for i in members
        ):
            additional.append(row)
        else:
            review.append(row)
    proofs = additional + review
    return {
        "baseline": {
            "events": len(base),
            "corroborated": sum(bool(e["first_qualified_at"]) for e in base.values()),
            "cross_market": sum(e["cross_market"] for e in base.values()),
        },
        "full": {
            "events": len(full),
            "corroborated": len(proofs),
            "cross_market": sum(e["cross_market"] for e in full.values()),
        },
        "additional": additional,
        "review_candidates": proofs[:20],
        "cross_market_examples": cross,
        "source_contributions": {s: dict(n) for s, n in contributions.items()},
    }


def operational(entries, start, end, now, sources):
    slots, queue, builds, deploys = set(), [], [], []
    statuses = {s["source_id"]: Counter() for s in sources}
    good = defaultdict(list)
    blocked_until = {}
    candidate = {}
    published = {}
    observed = []
    run_links = []
    commits = set()
    polls = observation_bytes = observer_attempted = poll_observer = deploy_count = (
        missing_artifacts
    ) = 0
    archive_bytes = Counter()
    for entry in entries:
        run = entry["run"]
        if run.get("head_sha"):
            commits.add(run["head_sha"])
        observation = entry.get("observation") or {}
        jobs = {j["name"]: j for j in entry.get("jobs", [])}
        build, deploy = jobs.get("build", {}), jobs.get("deploy", {})
        started = build.get("started_at")
        if started and start <= instant(started) < end and instant(started) <= now:
            if run["event"] == "schedule":
                slots.add(int((instant(started) - start).total_seconds() // 600))
            run_links.append(run.get("html_url"))
            if not observation:
                missing_artifacts += 1
            duration = seconds(started, build.get("completed_at"))
            if duration is not None:
                builds.append(duration)
            lag = seconds(run.get("created_at"), started)
            if lag is not None:
                queue.append(lag)
            if deploy.get("conclusion") not in (None, "skipped"):
                deploy_count += 1
                d = seconds(deploy.get("started_at"), deploy.get("completed_at"))
                if d is not None:
                    deploys.append(d)
            observation_bytes += entry.get("artifact_bytes", 0)
            archive_bytes.update(entry.get("artifact_sizes", {}))
        observer = entry.get("observer", {})
        if observer.get("observed_at"):
            ts = instant(observer["observed_at"])
            if start <= ts < end + timedelta(minutes=20) and ts <= now:
                observed.append((ts, observer))
        if (
            not observation
            or not start <= instant(observation["as_of"]) < end
            or instant(observation["as_of"]) > now
        ):
            continue
        ts = instant(observation["as_of"])
        for row in observation["statuses"]:
            sid = row["source_id"]
            what = row["status"]
            statuses[sid][what] += 1
            if what == "not_due":
                if sid in blocked_until and ts < blocked_until[sid]:
                    statuses[sid]["cooldown"] += 1
                continue
            polls += 1
            if what in {"changed", "unchanged"}:
                good[sid].append(ts)
                blocked_until.pop(sid, None)
            elif row.get("next_due_at"):
                blocked_until[sid] = instant(row["next_due_at"])
    for ts, observer in sorted(observed, key=lambda pair: pair[0]):
        if start <= ts < end:
            observer_attempted += (
                len(observer.get("polls", []))
                if "polls" in observer
                else int("source_id" in observer)
            )
            poll_observer += (
                sum(p["status"] == "ok" for p in observer["polls"])
                if "polls" in observer
                else int("source_id" in observer)
            )
            for item in observer.get("candidates", []):
                url = canonical_url(item["url"])
                witnessed = (
                    instant(item["observed_at"]) if item.get("observed_at") else ts
                )
                if url and start <= witnessed < end and witnessed <= now:
                    candidate.setdefault((item["source_id"], url), (witnessed, item))
        if "public_items" in observer:
            public = {
                (item["source_id"], canonical_url(item["url"]))
                for item in observer["public_items"]
            }
        else:
            urls = {canonical_url(raw) for raw in observer.get("public_ids", [])}
            public = {key for key in candidate if key[1] in urls}
        witnessed = (
            instant(observer["public_observed_at"])
            if observer.get("public_observed_at")
            else ts
        )
        for key in public & candidate.keys():
            if key not in published and candidate[key][0] <= witnessed <= now:
                published[key] = witnessed
    source_metrics = {}
    for src in sources:
        sid = src["source_id"]
        counts = statuses[sid]
        success = counts["changed"] + counts["unchanged"]
        attempted = success + counts["failed"] + counts["empty"]
        dates = sorted(good[sid])
        interval_end = min(end, now)
        boundaries = [start] + dates + [max(start, interval_end)]
        source_metrics[sid] = {
            "counts": dict(counts),
            "attempted": attempted,
            "success_rate": ratio(success, attempted),
            "max_gap_including_edges_minutes": round(
                max((b - a).total_seconds() for a, b in itertools.pairwise(boundaries))
                / 60,
                1,
            ),
        }
    latency = []
    cadences = {s["source_id"]: s["interval_minutes"] for s in sources}
    for key, (ts, item) in candidate.items():
        mature = now >= ts + timedelta(minutes=20)
        measured = (
            (published[key] - ts).total_seconds() / 60 if key in published else None
        )
        latency.append(
            {
                "source_id": item["source_id"],
                "url": key[1],
                "interval_minutes": cadences[item["source_id"]],
                "observed_at": stamp(ts),
                "first_public_observation": stamp(published[key])
                if key in published
                else None,
                "minutes": measured,
                "mature": mature,
                "within_20m": (measured is not None and measured <= 20)
                if mature
                else None,
            }
        )
    total_slots = math.ceil((end - start).total_seconds() / 600)
    expected = min(total_slots, max(0, int((now - start).total_seconds() // 600) + 1))
    fast = [row for row in latency if row["interval_minutes"] == 10]
    mature_fast = [row for row in fast if row["mature"]]
    latency_groups = {}
    for minutes in sorted(set(cadences.values())):
        samples = [row for row in latency if row["interval_minutes"] == minutes]
        latency_groups[minutes] = {
            "sample_count": len(samples),
            "pending": sum(not row["mature"] for row in samples),
            "missed": sum(row["minutes"] is None and row["mature"] for row in samples),
            "p95_visible_minutes": percentile95(
                [row["minutes"] for row in samples if row["minutes"] is not None]
            ),
        }
    return {
        "slots": len(slots),
        "expected_slots": expected,
        "planned_slots": total_slots,
        "schedule_rate": ratio(len(slots), expected),
        "queue_seconds_p95": percentile95(queue),
        "build_seconds_p95": percentile95(builds),
        "deploy_seconds_p95": percentile95(deploys),
        "source_polls": polls,
        "source_poll_unit": "logical source attempts before retries and redirects",
        "observer_attempts": observer_attempted,
        "observer_successful_polls": poll_observer,
        "artifact_bytes": observation_bytes,
        "deploy_attempts": deploy_count,
        "missing_observation_runs": missing_artifacts,
        "observed_artifact_bytes_by_type": dict(archive_bytes),
        "run_links": [u for u in run_links if u],
        "commits": sorted(commits),
        "sources": source_metrics,
        "latency_samples": latency,
        "latency_cohorts": latency_groups,
        "fast_sample_count": len(fast),
        "mature_fast_sample_count": len(mature_fast),
        "latency_within_20m": ratio(
            sum(i["within_20m"] for i in mature_fast), len(mature_fast)
        ),
        "billing_actual_usd": None,
    }


def report(entries, hours, now, sources, reviews=None):
    entries = sorted(
        entries,
        key=lambda x: x.get("observation", {}).get("as_of") or x["run"]["created_at"],
    )
    starts = sorted(
        {
            i["observation"]["trial_start"]
            for i in entries
            if i.get("observation", {}).get("trial_start")
            and i["observation"].get("trial_basis") == "public-v1"
        }
    )
    if not starts:
        evidence_by_source = {}
        for e in entries:
            for s in e.get("observation", {}).get("statuses", []):
                if s.get("last_good_at"):
                    evidence_by_source[s["source_id"]] = s["last_good_at"]
        clocks = [instant(e["run"]["created_at"]) for e in entries]
        probe_start = min(clocks) if clocks else None
        operations = None
        if probe_start:
            probe_start = probe_start.replace(second=0, microsecond=0) - timedelta(
                minutes=(probe_start.minute - 3) % 10
            )
            operations = operational(
                entries,
                probe_start,
                probe_start + timedelta(hours=hours),
                now,
                sources,
            )
        return {
            "status": "preflight_pending",
            "observed_good": evidence_by_source,
            "probe_start": stamp(probe_start) if probe_start else None,
            "operations": operations,
            "trial_start": None,
            "missing_sources": [
                s["source_id"]
                for s in sources
                if s["source_id"] not in evidence_by_source
            ],
        }
    start = instant(starts[0])
    end = start + timedelta(hours=hours)
    observations = [
        e["observation"]
        for e in entries
        if e.get("observation")
        and start <= instant(e["observation"]["as_of"]) < end
        and instant(e["observation"]["as_of"]) <= now
    ]
    operations = operational(entries, start, end, now, sources)
    ab = compare(observations, sources)
    decisions = reviews or {}
    validated = [
        e["event_id"]
        for e in ab["additional"]
        if decisions.get(e["event_id"]) == "correct"
    ]
    audited = sum(
        decisions.get(e["event_id"])
        in {"correct", "false_merge", "wire_duplicate", "photo_credit"}
        for e in ab["review_candidates"]
    )
    state = (
        "observing" if now < end + timedelta(minutes=20) else "manual_review_pending"
    )
    if now >= end + timedelta(minutes=20) and len(validated) >= 5 and audited >= 20:
        state = "confirmed_gain"
    elif now >= end + timedelta(minutes=20) and (
        len(ab["additional"]) < 5 or len(ab["review_candidates"]) < 20
    ):
        state = "insufficient_data"
    complete = now >= end + timedelta(minutes=20)
    rate = operations["latency_within_20m"]
    gates = {
        "grid_95_percent": operations["schedule_rate"] >= 0.95 if complete else None,
        "all_sources_98_percent": all(
            m["success_rate"] is not None and m["success_rate"] >= 0.98
            for m in operations["sources"].values()
        )
        if complete
        else None,
        "build_p95_below_5min": operations["build_seconds_p95"] < 300
        if complete and operations["build_seconds_p95"] is not None
        else None,
        "fast_latency_95_percent_within_20min": rate >= 0.95
        if complete and operations["mature_fast_sample_count"] >= 20
        else None,
        "manual_20_clusters_and_5_incremental": audited >= 20 and len(validated) >= 5
        if complete
        else None,
    }
    return {
        "status": state,
        "start": stamp(start),
        "end": stamp(end),
        "collected_through": stamp(now),
        "hours": hours,
        "snapshot_count": len(observations),
        "operations": operations,
        "comparison": ab,
        "acceptance": gates,
        "pages_url": os.environ.get("PAGE_URL"),
        "replay_commit": os.environ.get("GITHUB_SHA"),
        "validated_additional": validated,
        "reviewed_clusters": audited,
        "review_guide": "Review independent article bylines and occurrence; use correct, false_merge, wire_duplicate, or photo_credit.",
    }


def markdown(r):
    if r["status"] == "preflight_pending":
        missing = ", ".join(r["missing_sources"]) or "không"
        heading = f"# Chờ đủ nguồn và bản Pages công khai\n\nĐã có dữ liệu từ **{len(r['observed_good'])}/14** nguồn. Nguồn còn thiếu: {missing}.\n"
        if r["operations"]:
            ops = r["operations"]
            heading += f"\nLượt thăm dò từ {r['probe_start']}: {ops['slots']}/{ops['expected_slots']} khung lịch đã đến hạn, {ops['source_polls']} lượt thu nguồn (trước retry/redirect), {ops['observer_attempts']} lượt thăm dò nguồn độc lập.\n"
        return heading
    met, ab = r["operations"], r["comparison"]
    show = lambda value: "chưa đo" if value is None else str(round(value, 2))
    lines = [
        f"# Đánh giá {r['hours']} giờ",
        "",
        f"**{r['status']}** · {r['start']} → {r['end']}.",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---:|",
        f"| Khung có job / đã đến hạn (kế hoạch {met['planned_slots']}) | {met['slots']}/{met['expected_slots']} |",
        f"| p95 chờ runner / build / deploy (giây) | {show(met['queue_seconds_p95'])} / {show(met['build_seconds_p95'])} / {show(met['deploy_seconds_p95'])} |",
        f"| Lượt thu nguồn / thăm dò nguồn độc lập (trước retry/redirect) | {met['source_polls']} / {met['observer_attempts']} |",
        f"| Lần thử deploy / byte artifact quan sát | {met['deploy_attempts']} / {met['artifact_bytes']} |",
        f"| Mẫu nhóm 10 phút hiển thị đúng hạn / đã đủ 20 phút | {sum(x['within_20m'] is True for x in met['latency_samples'] if x['interval_minutes'] == 10)}/{met['mature_fast_sample_count']} (còn {met['fast_sample_count'] - met['mature_fast_sample_count']} mẫu đang chờ) |",
        f"| Sự kiện ≥2 bên biên tập (7 / 14 nguồn) | {ab['baseline']['corroborated']} / {ab['full']['corroborated']} |",
        f"| Sự kiện ≥2 thị trường (7 / 14 nguồn) | {ab['baseline']['cross_market']} / {ab['full']['cross_market']} |",
        f"| Sự kiện đối chiếu thêm / QA xác nhận | {len(ab['additional'])} / {len(r['validated_additional'])} |",
        "",
        "| Nguồn | Thành công / đã gọi | Rỗng | Lỗi | Cooldown | Khoảng trống tối đa (phút) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for sid, v in met["sources"].items():
        c = v["counts"]
        lines.append(
            f"| {sid} | {c.get('changed', 0) + c.get('unchanged', 0)}/{v['attempted']} | {c.get('empty', 0)} | {c.get('failed', 0)} | {c.get('cooldown', 0)} | {v['max_gap_including_edges_minutes']} |"
        )
    lines.extend(["", "## Các cụm cần đối chiếu", ""])
    for e in ab["review_candidates"]:
        title = e["title"].replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
        links = " · ".join(f"[{i['source_id']}](<{i['url']}>)" for i in e["links"])
        lines.append(f"- \x60{e['event_id']}\x60 {title} — {links}")
    lines.extend(
        [
            "",
            "JSON kèm theo chứa toàn bộ link, lịch sử mẫu, SHA, run và đóng góp từng nguồn. Số tiền thực tế cần đối chiếu với tài khoản thanh toán.",
        ]
    )
    return "\n".join(lines) + "\n"


def archive_window(directory, destination, start, end):
    """Bundle original snapshots plus trailing observer checks for an offline rerun."""
    with ZipFile(destination, "w", compression=ZIP_STORED) as bundle:
        for path in sorted(directory.glob("*.json.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as source:
                row = json.load(source)
            at = instant(
                row.get("observation", {}).get("as_of") or row["run"]["created_at"]
            )
            # Include one pre-window sample to retain the first authoritative trial clock.
            if start - timedelta(minutes=20) <= at < end + timedelta(minutes=20):
                bundle.write(path, path.name)


def main():
    argv = argparse.ArgumentParser(description=__doc__)
    argv.add_argument(
        "--sync", action="store_true", help="download GitHub artifacts before replay"
    )
    argv.add_argument("--archive", type=Path, default=Path(".runtime/trial-archive"))
    argv.add_argument("--output", type=Path, default=Path(".runtime/evaluation"))
    argv.add_argument("--reviews", type=Path, help="JSON of event_id -> QA decision")
    args = argv.parse_args()
    now = datetime.now(UTC)
    if args.sync:
        sync(args.archive, now)
    entries = []
    for path in sorted(args.archive.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as inp:
            entries.append(json.load(inp))
    reviews = (
        json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else {}
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for hours in (48, 168):
        result = report(entries, hours, now, registry(), reviews)
        write(args.output / f"report-{hours}h.json", result)
        (args.output / f"report-{hours}h.md").write_text(
            markdown(result), encoding="utf-8"
        )
        if (
            args.sync
            and result.get("end")
            and now >= instant(result["end"]) + timedelta(minutes=20)
        ):
            # Each completed duration gets its own standalone downloadable evidence artifact.
            # A recent copy already on GitHub can be reused across repeated evaluations.
            suffix = instant(result["start"]).strftime("%Y%m%d-%H%M")
            name = f"trial-evidence-{hours}h-{suffix}"
            if not any(list_artifacts(name)):
                archive_window(
                    args.archive,
                    args.output / f"{name}.zip",
                    instant(result["start"]),
                    instant(result["end"]),
                )
                if "GITHUB_OUTPUT" in os.environ:
                    with open(
                        os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8"
                    ) as out:
                        out.write(f"evidence_{hours}={name}\n")
        print(f"{hours} hours: {result['status']}")
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
                out.write(markdown(result)[:90000] + "\n")


if __name__ == "__main__":
    main()
