"""Independent, bounded observation of when new entries reach the public site."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

from .pipeline import fetch_public, registry, stamp, write


def sample(base):
    now = datetime.now(timezone.utc)
    feed = fetch_public(base, "feed.json")
    if not feed:
        return {"observed_at": stamp(now), "public_ids": [], "candidates": []}
    public_urls = {item["url"] for event in feed["events"] for item in event["items"]}
    # Fixed order and slot, independent of whether the collector succeeded.
    ids = ["hackernews","npr-world","aljazeera-en","cgtn-world"]
    sid = ids[(int(now.timestamp()) // 600) % len(ids)]
    source = next(s for s in registry() if s["source_id"] == sid)
    with urlopen(Request(source["endpoint"], headers={"User-Agent": "HotNewsTrial-Observation/1.0"}), timeout=10) as res:
        root = ET.fromstring(res.read(2_000_001))
    candidates = []
    for entry in root.findall("./channel/item"):
        url = entry.findtext("link")
        if url and url not in public_urls:
            candidates.append({"source_id": sid, "url": url, "title": entry.findtext("title")})
            break
    return {"observed_at": stamp(datetime.now(timezone.utc)), "public_ids": sorted(public_urls),
            "candidates": candidates, "source_id": sid}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", required=True)
    args = parser.parse_args()
    try:
        result = sample(args.page)
    except (OSError, ValueError, ET.ParseError) as exc:
        result = {"observed_at": stamp(datetime.now(timezone.utc)), "error": str(exc)}
    write(Path(".runtime/sample/observer.json"), result)
