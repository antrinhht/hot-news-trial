"""Collect the due registry entries using the pinned TrendRadar runtime."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


UPSTREAM_REV = "86584f5544c66d4e22132f9b87c44a7ea3a32fea"
NEWSNOW = "https://newsnow.busiyi.world/api/s"
HOTLIST_DOMAINS = {"weibo": "weibo.com", "zhihu": "zhihu.com", "thepaper": "thepaper.cn"}
MAX_CAIXIN_BYTES = 4 * 1024 * 1024


def _failed(message):
    return {"status": "failed", "items": [], "error": str(message)[:320]}


def _result(items):
    return {"status": "ok" if items else "empty", "items": items}


def _runtime(upstream):
    revision = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip()
    if revision != UPSTREAM_REV:
        raise ValueError(f"TrendRadar revision {revision} differs from pinned {UPSTREAM_REV}")
    binary = os.environ.get("TRENDRADAR_PYTHON")
    if binary:
        path = Path(binary).absolute()
        if not path.is_file():
            raise FileNotFoundError(f"TrendRadar interpreter missing at {path}")
        return str(path)
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    path = upstream / ".venv" / relative
    if not path.is_file():
        raise FileNotFoundError(f"TrendRadar environment missing at {path}")
    # Do not resolve the symlink inside a POSIX venv; CPython needs its venv path.
    return str(path)


def _environment(upstream):
    # Keep OS, TLS and network configuration; the collector needs no account tokens.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP",
               "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SSL_CERT_FILE",
               "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY",
               "ALL_PROXY", "NO_PROXY", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update({
        "PYTHONPATH": str(upstream), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "GITHUB_ACTIONS": "true", "TIMEZONE": "UTC", "TZ": "UTC",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "FREQUENCY_WORDS_PATH": str(upstream / "config" / "frequency_words.txt"),
    })
    return env


def _config(sources, data):
    platforms = [{"id": s["source_id"], "name": s["name"],
                  "expected_domain": HOTLIST_DOMAINS[s["source_id"]]}
                 for s in sources if s["provider"] == "newsnow"]
    feeds = [{"id": s["source_id"], "name": s["name"], "url": s["endpoint"],
              "max_items": 0} for s in sources if s["provider"] == "rss"]
    return {
        "app": {"timezone": "UTC", "show_version_update": False},
        "platforms": {"enabled": True, "api_url": NEWSNOW, "sources": platforms},
        "rss": {"enabled": True, "feeds": feeds, "freshness_filter": {"enabled": False}},
        "schedule": {"enabled": False}, "notification": {"enabled": False},
        "ai_analysis": {"enabled": False}, "ai_translation": {"enabled": False},
        "filter": {"method": "keyword"},
        "storage": {"backend": "local", "local": {"data_dir": str(data)},
                    "pull": {"enabled": False},
                    "formats": {"sqlite": True, "txt": False, "html": False}},
        "advanced": {"version_check_url": "", "configs_version_check_url": "",
                     "crawler": {"request_interval": 1000},
                     "rss": {"request_interval": 500, "timeout": 12}},
    }


def _safe_url(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        return ""
    try:
        p = urlsplit(value.strip())
        return value.strip() if p.scheme in {"http", "https"} and p.hostname and not p.username and not p.password else ""
    except ValueError:
        return ""


def _utc(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        # The pinned RSS parser formats feedparser's normalized UTC tuple without 'Z'.
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def _article(source, row):
    url = _safe_url(row.get("url")) or _safe_url(row.get("mobile_url"))
    title = " ".join((row.get("title") or "").split())
    if not url or not title:
        return None
    if source["source_id"] == "ap-bluesky":
        summary = " ".join((row.get("summary") or "").split())
        # Upstream synthesizes a title from the first 20 characters when it is missing.
        if summary and (title == url or title == summary[:20] + "..."):
            title = summary[:240] + ("…" if len(summary) > 240 else "")
    item = {"title": title, "url": url, "guid": row.get("guid") or None,
            "source_time": _utc(row.get("published_at"))}
    # Only direct links to identifiable official documents count as primary evidence.
    link = urlsplit(url)
    document_paths = {
        "federalreserve.gov": r"/newsevents/pressreleases/[a-z]+\d{8}[a-z]?\.htm",
        "sec.gov": r"/Archives/edgar/data/\d+/[0-9-]+/[^/]+\.(?:htm|html|pdf)",
        "supremecourt.gov": r"/opinions/\d{2}pdf/[^/]+\.pdf",
        "ec.europa.eu": r"/commission/presscorner/detail/[a-z]{2}/[a-z]+_\d+_\d+",
    }
    pattern = document_paths.get((link.hostname or "").removeprefix("www."))
    if link.scheme == "https" and pattern and re.fullmatch(pattern, link.path, re.I):
        item["primary_url"] = url
    if row.get("rank") is not None:
        item["rank"] = row["rank"]
    if source["kind"] == "editorial":
        author = (row.get("author") or "").strip()
        for label, owner in (("associated press", "ap"), ("reuters", "reuters"),
                             ("agence france-presse", "afp"), ("afp", "afp")):
            # The field must actually be a byline. Image credits are not wire bylines.
            if re.search(r"\b(photo|image|picture|copyright)\b|©", author, re.I):
                break
            if re.search(r"(?:^|[,;|]\s*)(?:the\s+)?" + re.escape(label) + "$", author, re.I):
                item["origin_owner_id"] = owner
                break
    return item


def _read_source(data, source, cli_error):
    hot = source["provider"] == "newsnow"
    folder = "news" if hot else "rss"
    if hot:
        status_sql = """SELECT status, '' FROM crawl_source_status
                        WHERE platform_id=? ORDER BY crawl_record_id DESC LIMIT 1"""
        items_sql = """SELECT title, url, mobile_url, rank FROM news_items
                       WHERE platform_id=? ORDER BY rank, id"""
    else:
        status_sql = """SELECT status, error_message FROM rss_crawl_status
                        WHERE feed_id=? ORDER BY crawl_record_id DESC LIMIT 1"""
        items_sql = """SELECT title, url, guid, published_at, summary, author FROM rss_items
                       WHERE feed_id=? ORDER BY id"""
    key = (source["source_id"],)
    # Fresh private directory for this single CLI invocation. RSS and hotlists may
    # be stored under different dates if acquisition crosses midnight.
    for path in sorted((data / folder).glob("*.db"), reverse=True):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            status = db.execute(status_sql, key).fetchone()
            if status is None:
                continue
            if status[0] != "success":
                return _failed(status[1] or f"TrendRadar reported {status[0]}")
            items = (_article(source, dict(row)) for row in db.execute(items_sql, key))
            return _result([i for i in items if i is not None])
    return _failed(cli_error or "Missing TrendRadar crawl status for due source")


def _cli_sources(sources, upstream, binary, work):
    data = work / "output"
    config = work / "config.yaml"
    # JSON is also valid input to the pinned PyYAML safe loader.
    config.write_text(json.dumps(_config(sources, data), ensure_ascii=False), encoding="utf-8")
    env = _environment(upstream)
    env["CONFIG_PATH"] = str(config)
    error = ""
    try:
        cmd = subprocess.run([binary, "-m", "trendradar"], cwd=work, env=env,
                             capture_output=True, encoding="utf-8", errors="replace",
                             timeout=300)
        if cmd.returncode:
            error = f"TrendRadar exited {cmd.returncode}: {(cmd.stderr or cmd.stdout)[-210:].strip()}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        error = str(exc)
    results = {}
    for source in sources:
        try:
            result = _read_source(data, source, error)
        except (sqlite3.Error, OSError) as exc:
            result = _failed(f"Invalid TrendRadar SQLite result: {exc}")
        results[source["source_id"]] = result
    return results


def _caixin_xml(payload):
    if len(payload) > MAX_CAIXIN_BYTES or b"<!DOCTYPE" in payload.upper():
        raise ValueError("Unexpected Caixin XML size or document type")
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is None or root.tag != "rss":
        raise ValueError("Expected a Google News RSS channel")
    for node in channel.findall("item"):
        source = node.find("source")
        url = _safe_url(source.get("url")) if source is not None else ""
        if not (source is not None and (source.text or "").strip() == "Caixin Global"
                and urlsplit(url).hostname in {"caixinglobal.com", "www.caixinglobal.com"}):
            channel.remove(node)
    return ET.tostring(root, encoding="utf-8")


def _caixin(source, upstream, binary, work):
    try:
        request = Request(source["endpoint"], headers={"User-Agent": "HotNewsTrial/1.0 RSS"})
        with urlopen(request, timeout=15) as response:
            xml = _caixin_xml(response.read(MAX_CAIXIN_BYTES + 1))
        # Reuse the upstream parser in its locked environment, on the same bytes.
        cmd = subprocess.run([binary, str(Path(__file__).resolve()), "--parse-rss"],
                             cwd=work, env=_environment(upstream), input=xml,
                             capture_output=True, check=True, timeout=30)
        rows = json.loads(cmd.stdout)
        items = (_article(source, row) for row in rows)
        return _result([i for i in items if i is not None])
    except HTTPError as exc:
        failure = _failed(f"Caixin Global via Google News: {exc}")
        retry = exc.headers.get("Retry-After", "").strip()
        if retry:
            try:
                if retry.isdecimal():
                    failure["retry_after_seconds"] = int(retry)
                else:
                    at = parsedate_to_datetime(retry)
                    failure["retry_after_seconds"] = max(0, int((at - datetime.now(timezone.utc)).total_seconds()))
            except (ValueError, OverflowError, TypeError):
                pass
        return failure
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
        return _failed(f"Caixin Global via Google News: {exc}")


def collect(sources: list[dict], upstream: Path, as_of: str) -> dict:
    """Collect only the supplied due sources. as_of is the caller's observation clock."""
    if not sources:
        return {}
    upstream = Path(upstream).resolve()
    try:
        binary = _runtime(upstream)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {s["source_id"]: _failed(exc) for s in sources}
    with TemporaryDirectory(prefix="hotnews-crawl-") as directory, ThreadPoolExecutor(max_workers=1) as pool:
        work = Path(directory)
        regular = [s for s in sources if s["provider"] in {"newsnow", "rss"}]
        pending = {s["source_id"]: pool.submit(_caixin, s, upstream, binary, work)
                   for s in sources if s["provider"] == "google_news"}
        results = _cli_sources(regular, upstream, binary, work) if regular else {}
        for name, future in pending.items():
            results[name] = future.result()
        for source in sources:
            results.setdefault(source["source_id"], _failed("Unsupported provider"))
        return results


if __name__ == "__main__" and sys.argv[1:] == ["--parse-rss"]:
    from contextlib import redirect_stdout
    from dataclasses import asdict
    import runpy

    with redirect_stdout(sys.stderr):
        # The pinned parser is standalone. Importing the package root also
        # initializes report/AI dependencies, needless for a single RSS document.
        module = Path(os.environ["PYTHONPATH"]) / "trendradar" / "crawler" / "rss" / "parser.py"
        parser = runpy.run_path(str(module))["RSSParser"]()
        parsed = parser.parse(sys.stdin.buffer.read().decode("utf-8"))
    print(json.dumps([asdict(i) for i in parsed], ensure_ascii=False))
