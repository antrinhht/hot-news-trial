"""Sample dated upstream entries separately from the collection that publishes them."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from .collector import _caixin_xml
from .events import canonical_url
from .pipeline import fetch_public, registry, stamp, write


ATOM = "{http://www.w3.org/2005/Atom}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC = "{http://purl.org/dc/elements/1.1/}"
MAX_FEED_BYTES = 4 * 1024 * 1024
# At most one extra slow-feed request per scheduled tick. Over an hour each
# period meets or exceeds its publisher interval (20, 30, 60, 60 minutes).
SLOW_ROTATION = ("bbc-world", "dw-world", "caixin-global", "bbc-world", "ifanr", "caixin-global")


def _now():
    return datetime.now(timezone.utc)


def _targets(sources, now):
    by_id = {s["source_id"]: s for s in sources}
    slot = (int(now.timestamp()) - 3 * 60) // 600
    # NewsNow hotlists do not carry trustworthy per-item publication dates.
    fast = sorted(sid for sid, s in by_id.items() if s["interval_minutes"] == 10 and s["provider"] == "rss")
    slow = SLOW_ROTATION[slot % len(SLOW_ROTATION)]
    return [(by_id[fast[slot % len(fast)]], len(fast) * 10),
            (by_id[slow], 60 // SLOW_ROTATION.count(slow))]


def _source_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _entries(payload, source):
    if len(payload) > MAX_FEED_BYTES or b"<!DOCTYPE" in payload.upper():
        raise ValueError("Unexpected feed size or document type")
    if source["provider"] == "google_news":
        payload = _caixin_xml(payload)
    root = ET.fromstring(payload)
    if root.tag == "rss":
        for node in root.findall("./channel/item"):
            yield (node.findtext("link"), node.findtext("title") or node.findtext("description"),
                   node.findtext("pubDate") or node.findtext(DC + "date"))
    elif root.tag == RDF + "RDF":
        for node in root.findall(RSS1 + "item"):
            yield (node.findtext(RSS1 + "link") or node.get(RDF + "about"), node.findtext(RSS1 + "title"),
                   node.findtext(DC + "date"))
    elif root.tag == ATOM + "feed":
        for node in root.findall(ATOM + "entry"):
            link = next((el.get("href") for el in node.findall(ATOM + "link")
                         if el.get("rel", "alternate") == "alternate" and el.get("href")), None)
            yield link, node.findtext(ATOM + "title"), node.findtext(ATOM + "published") or node.findtext(ATOM + "updated")
    else:
        raise ValueError("Unsupported observer feed format")


def _newest_missing(payload, source, public_keys, as_of):
    earliest = as_of - timedelta(hours=48)
    eligible = []
    sid = source["source_id"]
    for url, title, date in _entries(payload, source):
        normalized = canonical_url(url)
        published = _source_date(date)
        if normalized and published and earliest < published <= as_of and (sid, normalized) not in public_keys:
            eligible.append((published, url.strip(), (title or url).strip()))
    if not eligible:
        return None
    published, url, title = max(eligible)
    return {"source_id": sid, "url": url, "title": title[:240], "source_time": stamp(published),
            "observed_at": stamp(as_of)}


def _read_source(source):
    request = Request(source["endpoint"], headers={"User-Agent": "HotNewsTrial-Observation/1.0 RSS"})
    with urlopen(request, timeout=10) as res:
        return res.read(MAX_FEED_BYTES + 1)


def sample(base, *, as_of=None, source_health=None):
    now = as_of or _now()
    result = {"observed_at": stamp(now), "public_items": [], "public_ids": [], "public_available": False,
              "candidates": [], "polls": []}
    try:
        feed = fetch_public(base, "feed.json")
        if not isinstance(feed, dict) or not isinstance(feed.get("events"), list):
            return result
        public = {(i["source_id"], i["url"]) for event in feed["events"] for i in event["items"]}
        result["public_observed_at"] = stamp(as_of or _now())
        result["public_available"] = True
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result["error"] = str(exc)[:200]
        return result
    result["public_items"] = [{"source_id": s, "url": url} for s, url in sorted(public)]
    result["public_ids"] = sorted({url for _, url in public})
    public_keys = {(s, canonical_url(url)) for s, url in public}
    for source, frequency in _targets(registry(), now):
        sid = source["source_id"]
        poll = {"source_id": sid, "observer_interval_minutes": frequency,
                "source_interval_minutes": source["interval_minutes"]}
        previous = (source_health or {}).get(sid, {})
        if (previous.get("status") in {"failed", "empty"}
                and previous.get("next_due_at", "") > stamp(now)):
            poll["status"] = "cooldown"
            result["polls"].append(poll)
            continue
        try:
            payload = _read_source(source)
            observed = as_of or _now()
            candidate = _newest_missing(payload, source, public_keys, observed)
            poll.update(status="ok", observed_at=stamp(observed))
            result.setdefault("source_id", sid)  # Older readers count successful observation ticks.
            if candidate:
                result["candidates"].append(candidate)
        except (OSError, ValueError, ET.ParseError) as exc:
            poll.update(status="failed", error=str(exc)[:200], observed_at=stamp(as_of or _now()))
        result["polls"].append(poll)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", required=True)
    args = parser.parse_args()
    checkpoint = Path(".runtime/checkpoint/state.json")
    import json
    health = json.loads(checkpoint.read_text(encoding="utf-8"))["sources"] if checkpoint.exists() else {}
    write(Path(".runtime/sample/observer.json"), sample(args.page, source_health=health))
