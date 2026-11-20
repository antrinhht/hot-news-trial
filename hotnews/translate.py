"""Translate published Chinese headlines with Helsinki-NLP OPUS-MT on CPU.

Model: https://huggingface.co/Helsinki-NLP/opus-mt-zh-en (CC BY 4.0).
The gaudi conversion uses the same model in CTranslate2's inference format.
Only --prepare accesses the network. Inference reads local files.
"""

import argparse
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

REVISION = "05d8fc158397bae0c65b8d46c858b6c18e094c12"
TRANSLATION_VERSION = f"opus-mt-zh-en:{REVISION}:1"
MODEL_URL = f"https://huggingface.co/gaudi/opus-mt-zh-en-ctranslate2/resolve/{REVISION}"
FILES = {
    "config.json": "ce02c0c0d02f285d2ff34c80b0867ccb5c4a3b250a275e6d1d2884f5499a6e46",
    "shared_vocabulary.json": "acbf13516c56af2bb78ebdb770c2e4648fd8232ddd17af75298119cbfa7ee33d",
    "source.spm": "e27a3a1b539f4959ec72ea60e453f49156289f95d4e6000b29332efc45616203",
    "target.spm": "6a881f4717cd7265f53fea54fd3dc689c767c05338fac7a4590f3088cb2d7855",
    "model.bin": "a188bc45bce24635a1eb0cc42ad0b43afdad806b86af9beb3065bba11f3b212c",
}
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]")
KANA_HANGUL = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
LOG = logging.getLogger(__name__)
BATCH_SIZE = 16


def _checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_model(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        destination = directory / name
        if destination.is_file() and _checksum(destination) == expected:
            continue
        for attempt in range(3):
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temp:
                temporary = Path(temp.name)
            try:
                # A fresh signed redirect also works if the CDN cached an expired one.
                url = f"{MODEL_URL}/{name}?download=true&request={time.time_ns()}"
                with urllib.request.urlopen(url, timeout=40) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if _checksum(temporary) != expected:
                    raise OSError(f"Invalid SHA256 for {name}")
                temporary.replace(destination)
                break
            except (OSError, http.client.HTTPException):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
            finally:
                temporary.unlink(missing_ok=True)


def _local_translator(directory: str):
    import ctranslate2
    import sentencepiece

    root = Path(directory)
    source = sentencepiece.SentencePieceProcessor(model_file=str(root / "source.spm"))
    target = sentencepiece.SentencePieceProcessor(model_file=str(root / "target.spm"))
    translator = ctranslate2.Translator(
        str(root), device="cpu", compute_type="int8", intra_threads=min(os.cpu_count() or 1, 4)
    )

    def translate(titles):
        # This Transformers-converted model expects an explicit end-of-source token.
        tokenized = [source.encode(title, out_type=str)[:511] + ["</s>"] for title in titles]
        results = translator.translate_batch(
            tokenized, beam_size=3, max_decoding_length=128, no_repeat_ngram_size=3,
        )
        return [target.decode(result.hypotheses[0]).strip() for result in results]

    return translate


def _default_chinese_sources():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    sources = json.loads(path.read_text(encoding="utf-8"))
    return {source["source_id"] for source in sources if source["language"] == "zh"}


def _targets(events, chinese_sources):
    targets = defaultdict(list)
    for event in events:
        event.pop("title_en", None)
        eligible = set()
        for item in event["items"]:
            item.pop("title_en", None)
            title = " ".join(item["title"].split())
            if item["source_id"] in chinese_sources and HAN.search(title) and not KANA_HANGUL.search(title):
                targets[title].append(item)
                eligible.add(title)
        title = " ".join(event["title"].split())
        if title in eligible:
            targets[title].append(event)
    return targets


def _cache_key(title: str) -> str:
    return hashlib.sha256(f"{TRANSLATION_VERSION}\n{title}".encode()).hexdigest()


def _usable(translation) -> bool:
    return (isinstance(translation, str) and bool(re.search("[A-Za-z]", translation))
            and not HAN.search(translation) and not KANA_HANGUL.search(translation))


def recover_translations(public_feed):
    """Seed a missing checkpoint from the published, matching model revision."""
    if public_feed.get("translation_version") != TRANSLATION_VERSION:
        return {}
    cache = {}
    for event in public_feed.get("events", []):
        for item in event.get("items", []):
            title = item.get("title")
            translation = item.get("title_en")
            if isinstance(title, str) and HAN.search(title) and _usable(translation):
                cache[_cache_key(" ".join(title.split()))] = translation.strip()
    return cache


def enrich_titles(events, cache, translate_batch=None, *, chinese_sources=None):
    """Enrich the public projection only. Cache entries expire with its 48h window."""
    if chinese_sources is None:
        chinese_sources = _default_chinese_sources()
    targets = _targets(events, chinese_sources)
    keys = {title: _cache_key(title) for title in targets}
    active = set(keys.values())
    for key in list(cache):
        if key not in active or not _usable(cache[key]):
            del cache[key]
    missing = [title for title, key in keys.items() if key not in cache]
    cached = len(targets) - len(missing)
    directory = os.getenv("HOTNEWS_TRANSLATION_MODEL")
    if missing and translate_batch is None and directory:
        try:
            translate_batch = _local_translator(directory)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            LOG.warning("OPUS-MT unavailable; retaining original titles: %s", exc)
    if translate_batch is not None:
        for offset in range(0, len(missing), BATCH_SIZE):
            batch = missing[offset:offset + BATCH_SIZE]
            try:
                translations = translate_batch(batch)
                if len(translations) != len(batch):
                    raise ValueError("Model output count differs from input count")
                for title, translation in zip(batch, translations):
                    if _usable(translation):
                        cache[keys[title]] = translation.strip()
            except (OSError, RuntimeError, ValueError) as exc:
                LOG.warning("OPUS-MT batch failed; retaining original titles: %s", exc)
    for title, records in targets.items():
        if translation := cache.get(keys[title]):
            for record in records:
                record["title_en"] = translation
    return {"eligible": len(targets), "cached": cached, "translated": len(cache) - cached,
            "remaining": len(targets) - len(cache)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare the checksummed OPUS-MT Chinese to English CPU model")
    parser.add_argument("--prepare", type=Path, required=True, metavar="MODEL_DIRECTORY")
    prepare_model(parser.parse_args().prepare)
