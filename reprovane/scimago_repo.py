from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

import requests

from . import __version__
from .common import read_json, sha256_file, write_json

DEFAULT_MANIFEST_URL = "https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json"
REQUEST_TIMEOUT = 60
MAX_RETRIES = 5
USER_AGENT = f"Reprovane/{__version__}"


class RepositoryError(RuntimeError):
    pass


def _get(session: requests.Session, url: str, binary: bool = False) -> bytes | str:
    delay = 1.5
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= MAX_RETRIES:
                break
            time.sleep(delay)
            delay *= 2
            continue
        if response.status_code == 200:
            return response.content if binary else response.text
        if response.status_code in (429, 500, 502, 503, 504):
            last_error = RepositoryError(f"HTTP {response.status_code}")
            if attempt >= MAX_RETRIES:
                break
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            time.sleep(delay)
            delay *= 2
            continue
        # Other 4xx responses are not transient: report immediately instead of retrying.
        if response.status_code == 404:
            raise RepositoryError(f"Remote repository file not found (HTTP 404): {url}")
        raise RepositoryError(f"Remote repository returned HTTP {response.status_code} for {url}: {response.text[:500]}")
    raise RepositoryError(f"Cannot download {url} (network/server error after {MAX_RETRIES} attempts): {last_error}")


def _is_plain_filename(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name and ":" not in name


def parse_checksums(text: str) -> Dict[str, str]:
    checksums: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if not match:
            raise RepositoryError(f"Invalid SHA256SUMS line: {raw_line!r}")
        checksums[match.group(2)] = match.group(1).lower()
    return checksums


def validate_manifest(manifest: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    required_top = ["schema_version", "base_url", "sjr", "journal_value", "checksums", "policy"]
    for key in required_top:
        if key not in manifest:
            errors.append(f"Missing manifest field: {key}")
    sjr = manifest.get("sjr") or {}
    for key in ["first_year", "last_year", "latest_snapshot_year", "url_pattern", "delimiter", "encoding"]:
        if key not in sjr:
            errors.append(f"Missing manifest.sjr field: {key}")
    checksums = manifest.get("checksums") or {}
    if checksums.get("algorithm", "").lower() != "sha256":
        errors.append("manifest.checksums.algorithm must be sha256")
    if not checksums.get("file"):
        errors.append("manifest.checksums.file is missing")
    if not errors:
        first_year = int(sjr["first_year"])
        last_year = int(sjr["last_year"])
        latest_year = int(sjr["latest_snapshot_year"])
        if first_year > last_year:
            errors.append("sjr.first_year must be <= sjr.last_year")
        if latest_year != last_year:
            errors.append("sjr.latest_snapshot_year should equal sjr.last_year for this repository policy")
        # File names come from a remote manifest and are joined to the cache directory:
        # reject anything that could write outside it.
        names = [str(sjr["url_pattern"]).replace("{year}", "0000"), str((manifest.get("journal_value") or {}).get("file", "")),
                 str(checksums.get("file", ""))]
        for name in names:
            if not _is_plain_filename(name):
                errors.append(f"Unsafe repository file name in manifest: {name!r}")
    return errors


def required_data_files(manifest: Dict[str, Any]) -> List[str]:
    sjr = manifest["sjr"]
    pattern = sjr["url_pattern"]
    files = [pattern.format(year=year) for year in range(int(sjr["first_year"]), int(sjr["last_year"]) + 1)]
    files.append(manifest["journal_value"]["file"])
    return files


def _save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _download_file(session: requests.Session, url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    payload = _get(session, url, binary=True)
    assert isinstance(payload, bytes)
    tmp.write_bytes(payload)
    actual = sha256_file(tmp)
    if actual.lower() != expected_sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RepositoryError(
            f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
        )
    tmp.replace(destination)


def load_repository_metadata(
    cache_dir: Path,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    offline: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, str], str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_cache = cache_dir / "manifest.json"
    checksums_cache = cache_dir / "SHA256SUMS.txt"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})

    source = "remote"
    if offline:
        if not manifest_cache.exists() or not checksums_cache.exists():
            raise RepositoryError("Offline mode requested, but cached manifest/checksum files are missing")
        manifest = read_json(manifest_cache)
        checksum_text = checksums_cache.read_text(encoding="utf-8")
        source = "cache-offline"
    else:
        try:
            separator = "&" if "?" in manifest_url else "?"
            manifest_text = _get(session, manifest_url + separator + f"nocache={int(time.time())}")
            assert isinstance(manifest_text, str)
            import json

            manifest = json.loads(manifest_text)
            errors = validate_manifest(manifest)
            if errors:
                raise RepositoryError("Invalid remote manifest: " + "; ".join(errors))
            checksum_url = urljoin(manifest["base_url"], manifest["checksums"]["file"])
            checksum_text = _get(session, checksum_url + f"?nocache={int(time.time())}")
            assert isinstance(checksum_text, str)
            _save_text(manifest_cache, manifest_text)
            _save_text(checksums_cache, checksum_text)
        except Exception as exc:
            if not manifest_cache.exists() or not checksums_cache.exists():
                raise RepositoryError(f"Remote repository unavailable and no verified cache metadata exists: {exc}")
            print(
                f"WARNING: remote SCImago metadata could not be refreshed ({exc}); "
                "using the cached manifest.json and SHA256SUMS.txt. Data files are still SHA-256 verified."
            )
            manifest = read_json(manifest_cache)
            checksum_text = checksums_cache.read_text(encoding="utf-8")
            source = "cache-fallback"

    errors = validate_manifest(manifest)
    if errors:
        raise RepositoryError("Invalid manifest: " + "; ".join(errors))
    checksums = parse_checksums(checksum_text)
    return manifest, checksums, source


def sync_repository(
    cache_dir: Path,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    offline: bool = False,
    force_redownload: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    manifest, checksums, metadata_source = load_repository_metadata(cache_dir, manifest_url, offline)
    required = required_data_files(manifest)

    expected_count = (manifest.get("files") or {}).get("expected_total_data_files")
    if expected_count is not None and int(expected_count) != len(required):
        raise RepositoryError(
            f"Manifest expects {expected_count} data files, but year/file rules resolve to {len(required)}"
        )

    missing_checksum = [name for name in required if name not in checksums]
    if missing_checksum:
        raise RepositoryError("SHA256SUMS.txt is missing required files: " + ", ".join(missing_checksum))

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    base_url = manifest["base_url"]
    verified = 0
    downloaded = 0
    reused = 0

    for index, filename in enumerate(required, start=1):
        destination = cache_dir / filename
        expected = checksums[filename]
        valid_cache = destination.exists() and sha256_file(destination).lower() == expected.lower()

        if valid_cache and not force_redownload:
            reused += 1
            verified += 1
            if verbose:
                print(f"[SCImago {index}/{len(required)}] OK - local cache verified: {filename}")
            continue

        if offline:
            if destination.exists():
                actual = sha256_file(destination)
                raise RepositoryError(
                    f"Offline cache integrity failure for {filename}: expected {expected}, got {actual}"
                )
            raise RepositoryError(f"Offline cache file missing: {filename}")

        if verbose:
            if force_redownload:
                print(f"[SCImago {index}/{len(required)}] Forced refresh -> downloading from remote: {filename}")
            elif destination.exists():
                print(f"[SCImago {index}/{len(required)}] Local cache checksum mismatch -> re-downloading from remote: {filename}")
            else:
                print(f"[SCImago {index}/{len(required)}] Local cache not found -> downloading from remote: {filename}")
        url = urljoin(base_url, filename)
        _download_file(session, url, destination, expected)
        downloaded += 1
        verified += 1
        if verbose:
            print(f"[SCImago {index}/{len(required)}] OK - downloaded and SHA-256 verified: {filename}")

    state = {
        "manifest_url": manifest_url,
        "metadata_source": metadata_source,
        "schema_version": manifest.get("schema_version"),
        "dataset": manifest.get("dataset"),
        "updated": manifest.get("updated"),
        "base_url": base_url,
        "first_year": manifest["sjr"]["first_year"],
        "last_year": manifest["sjr"]["last_year"],
        "latest_snapshot_year": manifest["sjr"]["latest_snapshot_year"],
        "required_files": len(required),
        "verified_files": verified,
        "reused_files": reused,
        "downloaded_files": downloaded,
        "integrity_verified": verified == len(required),
    }
    write_json(cache_dir / "repository_state.json", state)
    return {"manifest": manifest, "checksums": checksums, "state": state}


def validate_cached_repository(
    cache_dir: Path,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    offline: bool = False,
) -> Dict[str, Any]:
    manifest, checksums, metadata_source = load_repository_metadata(cache_dir, manifest_url, offline)
    required = required_data_files(manifest)
    rows = []
    all_ok = True
    for filename in required:
        path = cache_dir / filename
        expected = checksums.get(filename, "")
        exists = path.exists()
        actual = sha256_file(path) if exists else ""
        ok = bool(exists and expected and actual.lower() == expected.lower())
        all_ok = all_ok and ok
        rows.append({
            "file": filename,
            "exists": exists,
            "sha256_ok": ok,
            "expected_sha256": expected,
            "actual_sha256": actual,
        })
    return {
        "ok": all_ok,
        "metadata_source": metadata_source,
        "manifest": manifest,
        "files": rows,
    }
