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
TRANSLATION_VERSION = f"opus-mt-zh-en:{REVISION}:2"
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
# Single characters like 美 also mean “beautiful”. Expand combinations only when
# the following word explicitly indicates an international relationship.
COUNTRY_NAMES = {"中": "中国", "美": "美国", "俄": "俄罗斯", "乌": "乌克兰"}
COUNTRY_GROUP = re.compile(
    r"(中俄美|中美俄|俄中美|俄美中|美中俄|美俄中|中美|美中|中俄|俄中|美俄|俄美|俄乌|乌俄)"
    r"(?=三方|三国|双方|两国|关系|局势|战争|冲突|谈判|会谈|协议|峰会|边境|领导人|外长|代表)"
)
SPEECH = r"\s*(?:[：:]|表示|强调|指出|宣布|回应|称|说)"
# Deliberately limited to unambiguous source markers. Keep the original when a
# translation drops a named party, an attributed speaker, or a forecast caveat.
MEANING_CHECKS = [
    (r"中国|中方", r"\b(?:China|Chinese|Sino|PRC)\b"),
    (r"美国|美方", r"\b(?:United States|Americ(?:a|ans?)|(?-i:U\.?S(?:\.?A)?))\b"),
    (r"俄罗斯|俄方", r"\b(?:Russia(?:ns?)?|Russo)\b"),
    (r"乌克兰|乌方", r"\bUkrain(?:e|ians?)\b"),
    (r"APEC|亚太经合组织", r"(?<![a-z])APEC(?![a-z])|\bAsia[-‐– ]Pacific Economic Cooperation\b"),
    (r"北约|NATO", r"\b(?:NATO|North Atlantic Treaty Organi[sz]ation)\b"),
    (r"王毅" + SPEECH, r"\bWang[ -]Yi\b"),
    (r"习近平" + SPEECH, r"\bXi(?: Jinping)?\b"),
    (r"(?:特朗普|川普)" + SPEECH, r"\bTrump\b"),
    (r"普京" + SPEECH, r"\bPutin\b"),
    (r"或将|有望|可能|或[涨跌升降]", r"\b(?:may|might|could|possibly|possible|probab\w*|potential\w*|likel\w*|expect\w*|prospect\w*|chance|hope\w*)\b"),
]
MEANING_CHECKS = [(re.compile(original), re.compile(english, re.IGNORECASE)) for original, english in MEANING_CHECKS]


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
        tokenized = [source.encode(_model_input(title), out_type=str)[:511] + ["</s>"] for title in titles]
        results = translator.translate_batch(
            tokenized, beam_size=3, max_decoding_length=128, no_repeat_ngram_size=3,
        )
        return [target.decode(result.hypotheses[0]).strip() for result in results]

    return translate


def _default_chinese_sources():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    sources = json.loads(path.read_text(encoding="utf-8"))
    return {source["source_id"] for source in sources if source["language"] == "zh"}


def _model_input(title):
    def expand(match):
        return "、".join(COUNTRY_NAMES[letter] for letter in match[0])

    return COUNTRY_GROUP.sub(expand, title).replace("｜", "，")


def _targets(events, chinese_sources):
    targets = defaultdict(list)
    for event in events:
        event.pop("title_en", None)
        event.pop("translation_status", None)
        eligible = set()
        for item in event["items"]:
            item.pop("title_en", None)
            item.pop("translation_status", None)
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


def _usable(title, translation) -> bool:
    if (not isinstance(translation, str) or not re.search("[A-Za-z]", translation)
            or HAN.search(translation) or KANA_HANGUL.search(translation)):
        return False
    original = _model_input(title)
    return all(not marker.search(original) or english.search(translation) for marker, english in MEANING_CHECKS)


def recover_translations(public_feed):
    """Seed a missing checkpoint from the published, matching model revision."""
    if public_feed.get("translation_version") != TRANSLATION_VERSION:
        return {}
    cache = {}
    for event in public_feed.get("events", []):
        for item in event.get("items", []):
            title = item.get("title")
            translation = item.get("title_en")
            if not isinstance(title, str) or not HAN.search(title):
                continue
            key = _cache_key(" ".join(title.split()))
            if item.get("translation_status") == "withheld":
                cache[key] = None
            elif _usable(title, translation):
                cache[key] = translation.strip()
    return cache


def enrich_titles(events, cache, translate_batch=None, *, chinese_sources=None):
    """Enrich the public projection only. Cache entries expire with its 48h window."""
    if chinese_sources is None:
        chinese_sources = _default_chinese_sources()
    targets = _targets(events, chinese_sources)
    keys = {title: _cache_key(title) for title in targets}
    active = {key: title for title, key in keys.items()}
    for key in list(cache):
        if key not in active or (cache[key] is not None and not _usable(active[key], cache[key])):
            del cache[key]
    missing = [title for title, key in keys.items() if key not in cache]
    cached = sum(value is not None for value in cache.values())
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
                    cache[keys[title]] = translation.strip() if _usable(title, translation) else None
            except (OSError, RuntimeError, ValueError) as exc:
                LOG.warning("OPUS-MT batch failed; retaining original titles: %s", exc)
    for title, records in targets.items():
        if keys[title] not in cache:
            continue
        translation = cache[keys[title]]
        for record in records:
            if translation is None:
                record["translation_status"] = "withheld"
            else:
                record["title_en"] = translation
    withheld = sum(value is None for value in cache.values())
    return {"eligible": len(targets), "cached": cached, "translated": len(cache) - cached - withheld, "withheld": withheld,
            "remaining": len(targets) - len(cache)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare the checksummed OPUS-MT Chinese to English CPU model")
    parser.add_argument("--prepare", type=Path, required=True, metavar="MODEL_DIRECTORY")
    prepare_model(parser.parse_args().prepare)
