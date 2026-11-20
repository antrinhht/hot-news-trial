"""Deterministic item identity, occurrence grouping and public news views."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit


ALIASES = Path(__file__).resolve().parents[1] / "config" / "aliases.json"
WINDOW = timedelta(hours=48)
RULE_VERSION = 2
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "at_medium", "at_campaign", "traffic_source"}
ROUNDUP = re.compile(r"\b(?:roundup|live updates|news digest|top stories|news briefing)\b|新闻汇总|要闻汇总|一周回顾|新闻早报|盘点", re.I)
MONTHS = {k.lower(): n for n in range(1, 13) for k in (calendar.month_name[n], calendar.month_abbr[n])}
MONTH_PATTERN = "|".join(MONTHS)
PRODUCT = re.compile(r"(?<![a-z0-9])(?:gpt|chatgpt|iphone|ipad|ios|windows|gemini|rtx|artemis|starship|hunyuan|b[o]?eing|airbus|falcon|a(?=\d{3}))[\s-]*\d+(?:\.\d+)*(?:-[a-z]\d*)?(?![a-z0-9])")
QUANTITY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(basis points?|bps?|percentage points?|%|percent(?:age)?|个?基点|个百分点|级|级地震)")
MAGNITUDE = re.compile(r"(?:magnitude[\s-]*|m\s*)(\d(?:\.\d+)?)(?![\d.])")
GAME_SCORE = re.compile(r"(?<![\d-])(\d{1,2})\s*[-–比:]\s*(\d{1,2})(?![\d-])")
SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9, "trillion": 10**12, "千": 1000, "万": 10000, "亿": 10**8}
AMOUNT = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion|千|万|亿)?\s*"
MONEY = re.compile(r"(us\$|\$|€|usd\s+|eur\s+|cny\s+)" + AMOUNT)
MONEY_SUFFIX = re.compile(AMOUNT + r"(美元|欧元|人民币|元人民币|us dollars?|euros?)")
UNIT_COUNT = re.compile(AMOUNT + r"(cars?\b|vehicles?\b|aircraft\b|planes?\b|辆|架)")
MISSIONS = re.compile(r"(?:artemis\s*|阿尔忒弥斯\s*)([ivx]{1,5}\b|\d+)", re.I)
ROMAN = {x: str(i) for i, x in enumerate(("", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"))}
MONTH_DAY = re.compile(rf"\b(?:{MONTH_PATTERN})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\b")
NON_REPORT = re.compile(r"\?|？|^(why|how|what|analysis|commentary|in depth|inside)\b|\b(?:whether|reacts? to|draws reaction|despite|ahead of|set to|plans? to|to go public|may go public)\b|\.\s+(?:and|meanwhile)[,: ]|早报[｜|：:]|有望|计划|预计|怎么看", re.I)
UNCERTAIN = re.compile(r"\b(?:not|never|would|might|could|may|unlikely|denies|denied|overturned|appeals?)\b|否认|未曾|可能|辟谣", re.I)
ISSUERS = {"anthropic", "openai", "google", "apple", "microsoft", "nvidia", "samsung", "amazon",
           "tesla", "byd", "boeing", "airbus", "tencent", "huawei", "alibaba"}
PUBLIC_LISTING = re.compile(r"\b(?:stock market debut|nasdaq debut|nyse debut)\b|敲钟上市")
COURT_DECISION = re.compile(r"\bcourt (?:has )?(?:finds?|found|rules?|ruled)\b|法院(?:裁定|判定)|法院认定")
PERSON = r"[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){1,3}"
PARDON = re.compile(rf"(?i:posthumously pardon(?:s|ed)?)\s+(?:[A-Z]{{2,4}}\s+)?(?:[a-z-]+\s+){{0,4}}(?P<after>{PERSON})|(?i:grant(?:s|ed)?)\s+(?P<before>{PERSON})\s+(?i:a posthumous pardon)")
SPECIES = r"hippo(?:potamus)?|elephant|giraffe|panda|gorilla|tiger|lion|rhino(?:ceros)?"
ANIMAL_DEATH = re.compile(rf"(?:(?P<first>[A-Z][a-z'-]+)\s+(?i:the)\s+(?P<s1>(?i:{SPECIES}))|(?P<s2>(?i:{SPECIES}))\s+(?P<last>[A-Z][a-z'-]+))\s+(?i:(?:has\s+)?(?:died|dies))\b")


def _timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _iso(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _id(prefix, *parts):
    return prefix + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


def canonical_url(url):
    """Keep the identity of signed, query-based and fragment-based article links."""
    if not isinstance(url, str) or any(ord(c) < 32 for c in url):
        return ""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        scheme = parts.scheme.lower()
        if port and (scheme, port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        kept = []
        for query_part in parts.query.split("&"):
            key = unquote_plus(query_part.partition("=")[0]).lower()
            if not (key.startswith("utm_") or key in TRACKING):
                kept.append(query_part)
        return urlunsplit((scheme, host, parts.path or "/", "&".join(kept), parts.fragment))
    except ValueError:
        return ""


def _specific_link(link):
    if not link:
        return False
    parsed = urlsplit(link)
    path = parsed.path.rstrip("/").casefold()
    if path in {"", "/world", "/news", "/top", "/hot", "/search", "/weibo", "/board", "/feed", "/rss", "/index.html"}:
        return False
    return not (path.startswith(("/search/", "/topics/", "/topic/", "/tags/", "/hashtag/", "/category/"))
                or parsed.hostname in {"s.weibo.com", "top.baidu.com"})


def _normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().replace("’", "'")


@lru_cache(maxsize=1)
def _patterns():
    def expression(word):
        word = _normalized(word)
        # ASCII boundaries let Chinese headlines contain Latin company names.
        return ("(?<![a-z0-9])" if word[0].isascii() and word[0].isalnum() else "") + re.escape(word) + (
            "(?![a-z0-9])" if word[-1].isascii() and word[-1].isalnum() else "")
    raw = json.loads(ALIASES.read_text(encoding="utf-8"))
    return {group: {name: re.compile("|".join(expression(w) for w in aliases)) for name, aliases in entries.items()}
            for group, entries in raw.items()}


def _occurrence_day(text, reference):
    """Infer a year only for an explicit month/day close to the source year."""
    match = re.search(r"(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})(?:日|号)?(?!\d)", text)
    fields = None
    if match:
        fields = tuple(map(int, match.groups()))
    else:
        chinese = re.search(r"(\d{1,2})月(\d{1,2})(?:日|号)", text)
        english = re.search(rf"\b({MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
        reversed_date = re.search(rf"\b(\d{{1,2}})\s+({MONTH_PATTERN})\b", text)
        if chinese:
            month, day = map(int, chinese.groups())
        elif english:
            month, day = MONTHS[english[1]], int(english[2])
        elif reversed_date:
            month, day = MONTHS[reversed_date[2]], int(reversed_date[1])
        else:
            return reference.isoformat(), False
        # December and January headlines often refer to the preceding/coming year.
        year = reference.year + (month < reference.month - 6) - (month > reference.month + 6)
        fields = year, month, day
    try:
        return date(*fields).isoformat(), True
    except ValueError:
        return reference.isoformat(), False


def _reported_fact(title, text, entities):
    """Narrow predicates with a named subject and an occurrence discriminator.

    Keep analysis, roundup and future headlines out of asserted fact matches.
    These keys identify events, not general similarity between headlines.
    """
    if NON_REPORT.search(text) or UNCERTAIN.search(MONTH_DAY.sub("", text)) or ROUNDUP.search(text):
        return None
    animal = ANIMAL_DEATH.search(title)
    if animal:
        species = (animal["s1"] or animal["s2"]).casefold()
        species = {"hippopotamus": "hippo", "rhinoceros": "rhino"}.get(species, species)
        name = (animal["first"] or animal["last"]).casefold()
        return "animal_death", {"animal:" + name}, {"species:" + species}
    pardon = PARDON.search(title)
    if pardon and entities:
        name = (pardon["before"] or pardon["after"]).casefold()
        return "pardon", entities, {"posthumous-recipient:" + name}
    genocide = "committed genocide" in text or "构成种族灭绝" in text or "犯下种族灭绝" in text
    if genocide and COURT_DECISION.search(text) and len(entities) >= 2:
        return "genocide_ruling", entities, {"offence:genocide"}
    listing = PUBLIC_LISTING.search(text) or (
        "shares open for trading" in text and ("initial public offering" in text or "ipo" in text))
    issuers = ISSUERS & entities
    if listing and len(issuers) == 1:
        venues = {"exchange:" + x for x in ("nasdaq", "nyse", "hkex") if re.search(rf"\b{x}\b", text)}
        return "public_listing", issuers, {"transaction:first-listing"} | venues
    return None


def _features(title, clock):
    text = _normalized(title)
    # A calendar month ("May 6") must not be interpreted as a modal ("may cut").
    without_dates = MONTH_DAY.sub(" ", text)
    found = {key: sorted(name for name, pattern in group.items()
                         if pattern.search(without_dates if key == "modalities" else text))
             for key, group in _patterns().items()}
    fact = _reported_fact(title, text, set(found["entities"]))
    if fact and not found["actions"]:
        action, entities, discriminator = fact
        found["actions"] = [action]
        found["entities"] = sorted(entities)
        found["details"] = sorted(set(found["details"]) | discriminator)
    # "recalls ambassador" is one action, not a simultaneous product recall.
    if "recall_ambassador" in found["actions"] and "recalled" in found["actions"]:
        found["actions"].remove("recalled")
    details = set(found["details"])
    year = date.fromisoformat(clock[:10])
    day, explicit = _occurrence_day(text, year)
    for match in PRODUCT.finditer(text):
        details.add("product:" + re.sub(r"[\s-]", "", match[0]))
    for value in MISSIONS.findall(text):
        details.add("mission:" + ROMAN.get(value, value))
    currency_keys = {"us$": "usd", "$": "usd", "美元": "usd", "€": "eur", "欧元": "eur",
                     "人民币": "cny", "元人民币": "cny", "us dollar": "usd", "us dollars": "usd",
                     "euro": "eur", "euros": "eur"}
    for currency, number, scale in MONEY.findall(text):
        key = currency.strip()
        details.add(currency_keys.get(key, key) + ":" + _quantity(number, scale))
    for number, scale, currency in MONEY_SUFFIX.findall(text):
        details.add(currency_keys[currency] + ":" + _quantity(number, scale))
    for number, scale, measure in UNIT_COUNT.findall(text):
        key = "aircraft" if measure in {"aircraft", "plane", "planes", "架"} else "vehicles"
        details.add(key + ":" + _quantity(number, scale))
    for number, unit in QUANTITY.findall(text):
        if "基点" in unit or unit.startswith(("basis", "bp")):
            key = "basis"
        elif unit.startswith("级"):
            key = "magnitude"
        elif "point" in unit or "百分点" in unit:
            key = "pctpoint"
        else:
            key = "pct"
        details.add(key + ":" + format(float(number), "g"))
    if "quake" in found["actions"]:
        details.update("magnitude:" + format(float(x), "g") for x in MAGNITUDE.findall(text))
    if "wins_game" in found["actions"]:
        details.update(f"score:{a}-{b}" for a, b in GAME_SCORE.findall(text))
    return {**found, "details": sorted(details), "day": day, "explicit_day": explicit,
            "roundup": bool(ROUNDUP.search(text))}


def _quantity(number, scale):
    value = Decimal(number.replace(",", "")) * SCALE.get(scale, 1)
    return format(value.normalize(), "f")


def _same_signature(left, right):
    if (left["roundup"] or right["roundup"] or len(left["actions"]) != 1
            or left["actions"] != right["actions"] or left["modalities"] != right["modalities"]):
        return False
    if left["explicit_day"] and right["explicit_day"]:
        if left["day"] != right["day"]:
            return False
    elif abs((date.fromisoformat(left["day"]) - date.fromisoformat(right["day"])).days) > 1:
        return False
    entities_a, entities_b = set(left["entities"]), set(right["entities"])
    common = entities_a & entities_b
    if not common:
        return False
    # A bilateral meeting and a trilateral summit are separate occurrences.
    if left["actions"] == ["meet"]:
        leaders = {"xi", "trump", "putin"}
        if (entities_a & leaders) != (entities_b & leaders):
            return False
    # Court findings about different parties or IPOs of different issuers can
    # share the same date and a generic financial/legal phrase.
    if left["actions"][0] in {"genocide_ruling", "public_listing"}:
        if len(common) < (2 if left["actions"][0] == "genocide_ruling" else 1):
            return False
        if entities_a != entities_b:
            return False
    a, b = set(left["details"]), set(right["details"])
    # A conflicting amount/version blocks a match even if the general subject is shared.
    typed = {s.split(":", 1)[0] for s in a | b if ":" in s}
    for category in typed:
        x = {s for s in a if s.startswith(category + ":")}
        y = {s for s in b if s.startswith(category + ":")}
        if x and y and x != y:
            return False
    if a and b and not a & b:
        return False
    if entities_a - common and entities_b - common:
        return False
    return bool(a & b) or (len(common) >= 2 and left["day"] == right["day"])


def _empty_engine():
    return {"items": {}, "aliases": {}, "clusters": {}, "last_good": {}, "rule_version": RULE_VERSION}


def _aliases(source_id, raw, url):
    values = []
    guid = str(raw.get("guid") or "").strip()
    if guid:
        values.append(_id("i-", source_id, "guid", canonical_url(guid) or guid))
    if url:
        values.append(_id("i-", source_id, "url", url))
    return values


def _report_time(item):
    return item["source_time"] or item["first_seen_at"]


def _anchor(item):
    links = {item["canonical_url"], item["primary_url"]}
    return {"links": sorted(link for link in links if _specific_link(link)),
            "title": item["title"], "clock": _report_time(item),
            "features": _features(item["title"], _report_time(item))}


def _public_item(item):
    return {key: value for key, value in item.items() if key not in {"last_seen_at", "event_id"}}


def recover_engine(feed):
    """Recover IDs and visible membership. Unknown undated input starts cold."""
    engine = _empty_engine()
    # Published records may have been produced by an earlier rule version.
    engine["rule_version"] = feed.get("grouping_version", 0)
    for event in feed.get("events", []):
        eid = event["event_id"]
        for raw in event.get("items", []):
            item = {**raw, "event_id": eid, "last_seen_at": raw["first_seen_at"],
                    "primary_url": raw.get("primary_url", ""), "guid": raw.get("guid", "")}
            iid = item["item_id"]
            for alias in _aliases(item["source_id"], item, item["canonical_url"]):
                engine["aliases"][alias] = iid
            engine["items"][iid] = item
            if eid not in engine["clusters"] or _id("e-", iid) == eid:
                engine["clusters"][eid] = _anchor(item)
    return engine


def _candidate_keys(anchor):
    yield from (("url", url) for url in anchor["links"])
    f = anchor["features"]
    if not f["roundup"] and len(f["actions"]) == 1:
        yield from ((f["actions"][0], name) for name in f["entities"])


def _match_reason(anchor, other):
    if (anchor["features"]["roundup"] == other["features"]["roundup"]
            and set(anchor["links"]).intersection(other["links"])):
        return "Cùng liên kết bài hoặc tài liệu cụ thể"
    if _same_signature(anchor["features"], other["features"]):
        return "Khớp thực thể, hành động, chi tiết và thời điểm"
    return ""


def _find_cluster(anchor, lookup, clusters):
    candidates = {eid for key in _candidate_keys(anchor) for eid in lookup.get(key, ())}
    matches = {eid: reason for eid in candidates if (reason := _match_reason(anchor, clusters[eid]))}
    return next(iter(matches.items())) if len(matches) == 1 else (None, "")


def _upgrade_anchors(engine):
    """Recheck saved anchors exactly once after a grouping-rule update.

    Merge a component only when every pair of anchors agrees directly. Thus an
    ambiguous intermediary cannot bridge separate events during migration.
    """
    if engine.get("rule_version") == RULE_VERSION:
        return
    groups = defaultdict(list)
    for item in engine["items"].values():
        groups[item["event_id"]].append(item)
    neighbors = defaultdict(dict)
    lookup = defaultdict(set)
    clusters = engine["clusters"]
    for eid, members in groups.items():
        old = clusters[eid]
        if "title" in old:
            anchor = {**old, "features": _features(old["title"], old["clock"])}
        else:
            # v1 stored features but no anchor text; find the item which generated its ID.
            first = min(members, key=lambda i: (_id("e-", i["item_id"]) != eid,
                                                i["first_seen_at"], i["item_id"]))
            anchor = _anchor(first)
        clusters[eid] = anchor
        keys = list(_candidate_keys(anchor))
        candidates = {other for key in keys for other in lookup[key]}
        for other in candidates:
            reason = _match_reason(anchor, clusters[other])
            if reason:
                neighbors[eid][other] = neighbors[other][eid] = reason
        for key in keys:
            lookup[key].add(eid)
    remaining = set(neighbors)
    while remaining:
        start = min(remaining)
        component, stack = {start}, [start]
        while stack:
            new = set(neighbors[stack.pop()]) - component
            component.update(new)
            stack.extend(new)
        remaining -= component
        if any(len(neighbors[eid]) != len(component) - 1 for eid in component):
            continue
        def priority(eid):
            return -len(groups[eid]), min(i["first_seen_at"] for i in groups[eid]), eid
        survivor = min(component, key=priority)
        for loser in component - {survivor}:
            for item in groups[loser]:
                item["event_id"] = survivor
                item["merge_reason"] = neighbors[loser][survivor]
            del clusters[loser]
    engine["rule_version"] = RULE_VERSION


def _ingest(engine, results, sources, now):
    clock = _iso(now)
    index = defaultdict(set)
    for eid, anchor in engine["clusters"].items():
        for key in _candidate_keys(anchor):
            index[key].add(eid)
    by_source = {s["source_id"]: s for s in sources}
    ordered = sorted((sid for sid in results if sid in by_source),
                     key=lambda sid: (by_source[sid]["kind"] != "editorial", sid))
    for sid in ordered:
        result, source = results[sid], by_source[sid]
        if result.get("status") != "ok" or not result.get("items"):
            continue
        prev = _timestamp(engine["last_good"].get(sid))
        warm = bool(prev and now - WINDOW < prev <= now)
        editorial = source["kind"] == "editorial"
        for raw in sorted(result["items"], key=lambda row: (str(row.get("guid") or ""), row.get("url") or "", row.get("title") or "")):
            url = canonical_url(raw.get("url"))
            title = " ".join((raw.get("title") or "").split())
            if not title or not url:
                continue
            aliases = _aliases(sid, raw, url)
            iid = next((engine["aliases"][a] for a in aliases if a in engine["aliases"]), aliases[0])
            for alias in aliases:
                engine["aliases"][alias] = iid
            previous = engine["items"].get(iid)
            parsed = _timestamp(raw.get("source_time"))
            # A publisher's update/edit date must not repeatedly reheat one story.
            source_time = (previous or {}).get("source_time") or (_iso(parsed) if parsed else None)
            origin = raw.get("origin_owner_id") or (previous or {}).get("origin_owner_id")
            owner = source["owner_id"]
            participant = origin if editorial and origin else owner
            if not editorial and source.get("market") == "CN" and source.get("provider") == "newsnow":
                participant = "cn_social_hotlist"
            item = {"item_id": iid, "source_id": sid, "owner_id": owner, "participant_id": participant,
                    "origin_owner_id": origin, "editorial": editorial, "market": source["market"],
                    "title": title, "url": raw["url"].strip(), "canonical_url": url,
                    "guid": str(raw.get("guid") or (previous or {}).get("guid") or ""),
                    "primary_url": canonical_url(raw.get("primary_url")) or (previous or {}).get("primary_url", ""),
                    "source_time": source_time, "first_seen_at": (previous or {}).get("first_seen_at", clock),
                    "time_kind": "source" if source_time else "first_seen", "last_seen_at": clock,
                    "hot_eligible": bool(source_time or (previous["hot_eligible"] if previous else warm))}
            if previous:
                item.update(event_id=previous["event_id"], merge_reason=previous.get("merge_reason", ""))
            else:
                anchor = _anchor(item)
                eid, reason = _find_cluster(anchor, index, engine["clusters"])
                if not eid:
                    eid = _id("e-", iid)
                    engine["clusters"][eid] = anchor
                    for key in _candidate_keys(anchor):
                        index[key].add(eid)
                item.update(event_id=eid, merge_reason=reason)
            engine["items"][iid] = item
        engine["last_good"][sid] = clock


def _view(eid, members, anchor, now):
    members.sort(key=lambda i: i["item_id"])
    members.sort(key=_report_time, reverse=True)
    representative = next((i for i in members if i["editorial"]), members[0])
    newest = members[0]
    participants, editorial = set(), set()
    last_signal, editorial_signal = {}, set()
    for item in members:
        pid = item["participant_id"]
        participants.add(pid)
        if item["editorial"]:
            editorial.add(pid)
        if item["hot_eligible"]:
            stamp = _timestamp(_report_time(item))
            last_signal[pid] = max(stamp, last_signal.get(pid, stamp))
            if item["editorial"]:
                editorial_signal.add(pid)
    primary = any(_specific_link(i.get("primary_url")) for i in members)
    facts = anchor["features"]
    concrete = len(facts["actions"]) == 1 and not facts["roundup"]
    # Two distinct original newsrooms pointing to the same specific story are also evidence.
    if not concrete and len(editorial) >= 2 and not facts["roundup"]:
        concrete = any(i.get("merge_reason") == "Cùng liên kết bài hoặc tài liệu cụ thể" for i in members)
    reason = ""
    if concrete and primary:
        reason = "Có tài liệu gốc về sự kiện"
    elif concrete and len(editorial) >= 2:
        reason = f"{len(editorial)} bên biên tập độc lập cùng đưa tin"
    heat = sum(0.5 ** ((now - last_signal[p]).total_seconds() / 86400) for p in sorted(last_signal))
    event = {"event_id": eid, "title": representative["title"], "latest_at": _report_time(newest),
             "time_kind": newest["time_kind"], "markets": sorted({i["market"] for i in members}),
             "owners": sorted(participants), "editorial_owners": sorted(editorial), "selected_reason": reason,
             "merge_reason": next((i["merge_reason"] for i in members if i.get("merge_reason")), ""),
             "heat": heat, "items": [_public_item(i) for i in members]}
    return event, len(last_signal) >= 2 and bool(editorial_signal)


def update_events(state, results, sources, as_of):
    """Apply observations idempotently and project the three 48-hour views."""
    now = _timestamp(as_of)
    if now is None:
        raise ValueError("as_of must be an ISO timestamp")
    if not state.get("engine"):
        state["engine"] = _empty_engine()
    engine = state["engine"]
    _upgrade_anchors(engine)
    _ingest(engine, results, sources, now)
    cutoff = _iso(now - WINDOW)
    clock = _iso(now)
    recent = defaultdict(list)
    for iid, item in list(engine["items"].items()):
        stamp = _report_time(item)
        if cutoff < stamp <= clock:
            recent[item["event_id"]].append(item)
        elif item["last_seen_at"] <= cutoff and stamp <= cutoff:
            del engine["items"][iid]
    live = {i["event_id"] for i in engine["items"].values()}
    engine["clusters"] = {eid: anchor for eid, anchor in engine["clusters"].items() if eid in live}
    engine["aliases"] = {alias: iid for alias, iid in engine["aliases"].items() if iid in engine["items"]}
    views, hot = [], []
    for eid in sorted(recent):
        event, qualifies = _view(eid, recent[eid], engine["clusters"][eid], now)
        views.append(event)
        if qualifies:
            hot.append(event)
    views.sort(key=lambda e: e["latest_at"], reverse=True)
    hot.sort(key=lambda e: e["latest_at"], reverse=True)
    hot.sort(key=lambda e: e["heat"], reverse=True)
    return {"grouping_version": RULE_VERSION, "events": views, "latest": [e["event_id"] for e in views],
            "hot": [e["event_id"] for e in hot[:10]],
            "selected": [e["event_id"] for e in views if e["selected_reason"]]}
