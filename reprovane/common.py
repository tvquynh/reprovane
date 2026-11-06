from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_doi(doi: Optional[str]) -> str:
    if not doi:
        return ""
    value = doi.strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value.strip()


def short_openalex_id(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.rstrip("/").split("/")[-1]


def normalize_issn(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9Xx]", "", value).upper()


def split_issns(value: Optional[str]) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[;,|]", value)
    result: List[str] = []
    seen = set()
    for part in parts:
        normalized = normalize_issn(part)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def title_exact_key(value: Optional[str]) -> str:
    text = safe_str(value).casefold().strip()
    return re.sub(r"\s+", " ", text)


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def title_normalized_key(value: Optional[str]) -> str:
    # "&" means "and" (OpenAlex "Computers & Security" vs SCImago "Computers and Security"); it was
    # previously deleted like other punctuation, so such titles never matched without an ISSN.
    text = safe_str(value).replace("&", " and ")
    if not text.isascii():
        # NFKD + combining-mark removal is a no-op for ASCII, so it only runs when needed.
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return _NON_ALNUM_RE.sub("", text)


STATUS_SUFFIX_RE = re.compile(r"\s*\((?:discontinued|ceased)\)\s*$", re.IGNORECASE)


def strip_status_suffix(value: Optional[str]) -> str:
    """Remove SCImago status labels such as ' (discontinued)' from the end of a title."""
    return STATUS_SUFFIX_RE.sub("", safe_str(value))


def has_status_suffix(value: Optional[str]) -> bool:
    return bool(STATUS_SUFFIX_RE.search(safe_str(value)))


def title_status_normalized_key(value: Optional[str]) -> str:
    return title_normalized_key(strip_status_suffix(value))


VALID_QUARTILES = {"Q1", "Q2", "Q3", "Q4"}


def normalize_quartile(value: Any) -> str:
    """Return Q1-Q4, or "" for SCImago placeholders such as "-" (no quartile assigned)."""
    text = safe_str(value).strip().upper()
    return text if text in VALID_QUARTILES else ""


_SECRET_PARAM_RE = re.compile(r"(?i)(api_key|apikey|access_token|token)=([^&\s'\"<>]+)")


def redact_secrets(text: Any, *secrets: str) -> str:
    """Mask credential query parameters and any literal secret values in a message."""
    result = _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=REDACTED", safe_str(text))
    for secret in secrets:
        if secret:
            result = result.replace(secret, "REDACTED")
    return result


def locale_float(value: Any) -> Optional[float]:
    text = safe_str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def int_or_none(value: Any) -> Optional[int]:
    text = safe_str(value).strip().replace(" ", "")
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return None


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def union_fieldnames(rows: Iterable[Dict[str, Any]]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = union_fieldnames(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
