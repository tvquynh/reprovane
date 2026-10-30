"""Download the open-access full texts of a corpus, and list everything that has to be fetched by hand.

Only the copies that OpenAlex reports as open access are tried (`best_oa_pdf_url`, then `oa_url`), and only for
papers marked open access. Requests are sequential, identified by the tool's User-Agent, spaced per host (honouring a
larger robots.txt Crawl-delay), and checked against robots.txt (RFC 9309 matching).

A host that answers with a bot check (a challenge or CAPTCHA page, HTTP 403, or a TDM rights reservation) is written
at once to `blocked_hosts.json`: no further request is sent to it, in this run or in later runs, and its papers go to
the manual-download list. HTTP 429, a 5xx with Retry-After, repeated network failures and an unreadable robots.txt only
pause the host for the current run. The tool never tries to get past a bot check.

Redirects are followed one hop at a time, so a link never reaches a host that is already blocked; when a bot check
appears after a redirect, the hosts that redirected to it are blocked too (DOI and handle resolvers excepted).
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests

from . import __version__
from .common import read_csv, read_json, read_jsonl, safe_str, sha256_file, write_csv

USER_AGENT_TOKEN = "Reprovane"
USER_AGENT = f"{USER_AGENT_TOKEN}/{__version__} (+https://github.com/tvquynh/scholarflow)"
DEFAULT_DELAY = 2.0          # seconds between two requests to the same host
MAX_CRAWL_DELAY = 60.0       # a larger robots.txt Crawl-delay is capped here
REQUEST_TIMEOUT = 60
DEFAULT_MAX_MB = 100
MAX_REDIRECTS = 5
MAX_NETWORK_FAILURES = 3     # consecutive failures before a host is paused for the rest of the run
SNIFF_BYTES = 64 * 1024
ROBOTS_MAX_BYTES = 512 * 1024
SMALL_PAGE = 20 * 1024
# Resolvers only redirect; blocking them would cut every DOI link, so a bot check there pauses them for the run.
RESOLVER_HOSTS = {"doi.org", "dx.doi.org", "hdl.handle.net", "n2t.net", "identifiers.org"}

DOWNLOADED, MANUAL_DONE = "DOWNLOADED", "MANUAL_DOWNLOADED"
BLOCK_OUTCOMES = {"BOT_CHECK", "HOST_BLOCKED"}
# Temporary problems: retried on the next run.
TRANSIENT = {"NETWORK_ERROR", "HOST_UNREACHABLE", "HTTP_5XX", "RATE_LIMITED", "ROBOTS_UNREACHABLE", "TRUNCATED",
             "HOST_PAUSED", "ERROR"}
# Order used to pick the reason shown for a paper with several links.
_PRIORITY = ["BOT_CHECK", "HOST_BLOCKED", "ROBOTS_DISALLOWED", "RATE_LIMITED", "HOST_PAUSED", "ROBOTS_UNREACHABLE",
             "HOST_UNREACHABLE", "NETWORK_ERROR", "HTTP_5XX", "TRUNCATED", "ERROR", "NOT_PDF", "TOO_LARGE",
             "TOO_MANY_REDIRECTS"]

# <title> texts of bot-check pages (compared from the start of the whole title, never as a substring).
_CHALLENGE_TITLES = (
    "just a moment", "attention required", "client challenge", "access denied", "pardon our interruption",
    "are you a robot", "robot or human", "human verification", "verify you are human", "security check",
    "ddos-guard", "captcha", "bot detection", "please wait", "one more step", "checking your browser",
    "request unsuccessful", "you have been blocked", "too many requests",
)
# Body markers that only challenge or block pages carry (not the scripts injected into ordinary pages,
# such as Cloudflare's /cdn-cgi/challenge-platform/scripts/jsd/main.js).
_CHALLENGE_MARKERS = (
    "/cdn-cgi/challenge-platform/h/", "cf_chl_", "cf-browser-verification", "incapsula incident id", "px-captcha",
    "captcha-delivery.com",
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _say(message: str) -> None:
    """print() that never fails on a console that cannot show some characters."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(message.encode(encoding, "replace").decode(encoding, "replace"))


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def host_of(url: str) -> str:
    """Host key used for blocking and pacing: lower-case host, plus the port unless it is the scheme default."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    return f"{host}:{port}" if port and port != default else host


def _clean(text: Any) -> str:
    return " ".join(safe_str(text).split())


def detect_bot_check(status: int, headers: Dict[str, str], head: bytes, plain_403: bool = True) -> str:
    """Return a short description when a response is a bot check or a rights reservation, else ''.

    plain_403: whether a bare HTTP 403 without any challenge sign counts (not for robots.txt, where 403 means "no rules").
    HTTP 429 is not handled here: it is a rate limit, see Fetcher.
    """
    lower = {k.lower(): safe_str(v).strip().lower() for k, v in headers.items()}
    if lower.get("cf-mitigated") == "challenge":
        return f"Cloudflare challenge (HTTP {status})"
    if lower.get("x-amzn-waf-action") in {"challenge", "captcha"}:
        return f"AWS WAF {lower['x-amzn-waf-action']} (HTTP {status})"
    if lower.get("tdm-reservation") == "1":
        return f"text and data mining rights reserved (tdm-reservation, HTTP {status})"
    if status >= 400 or len(head) < SMALL_PAGE:
        raw = head.decode("utf-8", errors="replace")
        title_match = _TITLE_RE.search(raw)
        if title_match:
            title = _clean(html.unescape(title_match.group(1)))
            lowered = title.lower()
            for phrase in _CHALLENGE_TITLES:
                if lowered.startswith(phrase) and len(lowered) <= len(phrase) + 25:
                    return f"bot-check page '{title[:60]}' (HTTP {status})"
        text = raw.lower()
        for marker in _CHALLENGE_MARKERS:
            if marker in text:
                return f"bot-check page ({marker}, HTTP {status})"
    if status == 403 and plain_403:
        return "HTTP 403 Forbidden"
    return ""


class Robots:
    """robots.txt rules for one host (RFC 9309: grouped user agents, longest match, Allow wins ties, * and $)."""

    def __init__(self, text: str = "", allow_all: bool = False):
        self.allow_all = allow_all
        self.groups: List[Tuple[List[str], List[Tuple[bool, str]], Optional[float]]] = []
        agents: List[str] = []
        rules: List[Tuple[bool, str]] = []
        delay: Optional[float] = None
        in_rules = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    self.groups.append((agents, rules, delay))
                    agents, rules, delay, in_rules = [], [], None, False
                agents.append(value.lower())
            elif key in {"allow", "disallow"} and agents:
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
            elif key == "crawl-delay" and agents:
                in_rules = True
                try:
                    delay = float(value)
                except ValueError:
                    pass
        if agents:
            self.groups.append((agents, rules, delay))

    def _selected(self) -> List[Tuple[List[str], List[Tuple[bool, str]], Optional[float]]]:
        token = USER_AGENT_TOKEN.lower()
        mine = [g for g in self.groups if any(a != "*" and a in token for a in g[0])]
        return mine or [g for g in self.groups if "*" in g[0]]

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
        return re.match(body + ("$" if anchored else ""), path) is not None

    def allowed(self, url: str) -> bool:
        if self.allow_all:
            return True
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if path == "/robots.txt":
            return True
        best: Optional[Tuple[int, bool]] = None
        for _, rules, _ in self._selected():
            for allow, pattern in rules:
                if self._matches(pattern, path):
                    length = len(pattern)
                    if best is None or length > best[0] or (length == best[0] and allow):
                        best = (length, allow)
        return True if best is None else best[1]

    def crawl_delay(self) -> Optional[float]:
        delays = [g[2] for g in self._selected() if g[2] is not None]
        return max(delays) if delays else None


def paper_key(row: Dict[str, Any]) -> str:
    """Stable file name stem: the OpenAlex ID, else the DOI, else a hash of title, year and links."""
    openalex_id = safe_str(row.get("openalex_id")).strip()
    if openalex_id:
        return re.sub(r"[^A-Za-z0-9._-]", "_", openalex_id)
    doi = safe_str(row.get("doi")).strip().lower()
    if doi:
        return "doi_" + re.sub(r"[^a-z0-9._-]", "_", doi)
    basis = "|".join(_clean(row.get(f)) for f in ("title", "publication_year", "best_oa_pdf_url", "oa_url"))
    return "row_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12] if basis.strip("|") else ""


def _truthy(value: Any) -> bool:
    return safe_str(value).strip().lower() in {"true", "1", "yes"}


def candidate_urls(row: Dict[str, Any]) -> List[str]:
    urls = []
    for field in ("best_oa_pdf_url", "oa_url"):
        url = safe_str(row.get(field)).strip()
        if url.lower().startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls


def _looks_like_pdf(head: bytes) -> bool:
    # The PDF header may follow a few junk bytes; readers accept it within the first 1024 bytes.
    return b"%PDF-" in head[:1024]


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return _looks_like_pdf(handle.read(1024))
    except OSError:
        return False


def _complete_pdf(path: Path) -> bool:
    """A complete PDF ends with %%EOF (readers look for it in the last 1024 bytes; trailing junk is tolerated)."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - 2048))
        return b"%%EOF" in handle.read()


def _content_length(headers: Dict[str, str]) -> Optional[int]:
    value = safe_str(headers.get("Content-Length")).split(",")[0].strip()
    return int(value) if value.isdigit() else None


class Fetcher:
    """One sequential, polite HTTP client with per-host pacing, robots.txt and the blocked-host register."""

    def __init__(self, blocked: Dict[str, Dict[str, Any]], blocked_path: Optional[Path], delay: float, timeout: float,
                 max_bytes: int, log: List[Dict[str, Any]]):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"})
        self.blocked = blocked
        self.blocked_path = blocked_path
        self.delay = delay
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.log = log
        self.last_request: Dict[str, float] = {}
        self.robots: Dict[str, Optional[Robots]] = {}
        self.failures: Dict[str, int] = {}
        self.paused: Dict[str, Tuple[str, str]] = {}   # host -> (outcome, detail), for this run only
        self.requests_sent = 0

    def close(self) -> None:
        self.session.close()

    # -- bookkeeping ---------------------------------------------------------------------------------------
    def _pace(self, host: str) -> None:
        delay = self.delay
        robots = self.robots.get(host)
        if robots is not None and robots.crawl_delay():
            delay = max(delay, min(robots.crawl_delay(), MAX_CRAWL_DELAY))
        wait = self.last_request.get(host, -1e9) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_request[host] = time.monotonic()

    def _get(self, url: str) -> requests.Response:
        self._pace(host_of(url))
        self.requests_sent += 1
        return self.session.get(url, timeout=self.timeout, stream=True, allow_redirects=False)

    def block(self, host: str, reason: str, url: str, status: Optional[int]) -> None:
        """Block a host for good (saved at once, so a crash cannot forget it); resolvers are only paused."""
        if host in RESOLVER_HOSTS:
            self.paused.setdefault(host, ("HOST_PAUSED", f"{host} (resolver) paused for this run: {reason}"))
            return
        if host not in self.blocked:
            self.blocked[host] = {"reason": reason, "url": url, "http_status": status, "first_seen": _now()}
            if self.blocked_path is not None:
                _write_json_atomic(self.blocked_path, self.blocked)

    def blocked_reason(self, host: str) -> str:
        return safe_str((self.blocked.get(host) or {}).get("reason")) or "blocked"

    def _record(self, paper: str, url: str, status: Optional[int], outcome: str, detail: str = "") -> None:
        self.log.append({"time": _now(), "paper": paper, "url": url, "host": host_of(url),
                         "http_status": "" if status is None else status, "outcome": outcome, "detail": detail})

    def _rate_limit(self, host: str, status: int, headers: Dict[str, str]) -> None:
        retry_after = safe_str(headers.get("Retry-After"))
        self.paused[host] = ("RATE_LIMITED", f"{host}: HTTP {status}" + (f", Retry-After {retry_after}" if retry_after else "")
                             + "; paused for this run")

    @staticmethod
    def _read(response: requests.Response, limit: int) -> bytes:
        data = b""
        for chunk in response.iter_content(65536):
            data += chunk
            if len(data) >= limit:
                break
        return data[:limit]

    # -- robots.txt ----------------------------------------------------------------------------------------
    def _load_robots(self, url: str, paper: str) -> None:
        host = host_of(url)
        parts = urlsplit(url)
        current = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rules: Optional[Robots] = None
        for _ in range(MAX_REDIRECTS + 1):
            hop = host_of(current)
            if hop in self.blocked or hop in self.paused:
                rules = Robots(allow_all=True)   # the PDF request will meet the block itself
                break
            try:
                response = self._get(current)
            except requests.RequestException as exc:
                self._record(paper, current, None, "ROBOTS_UNREACHABLE", type(exc).__name__)
                break
            try:
                status, headers = response.status_code, response.headers
                if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                    self._record(paper, current, status, "ROBOTS_REDIRECT", headers["Location"])
                    current = urljoin(current, headers["Location"])
                    continue
                body = self._read(response, ROBOTS_MAX_BYTES) if status < 500 else b""
            except requests.RequestException as exc:
                self._record(paper, current, None, "ROBOTS_UNREACHABLE", type(exc).__name__)
                break
            finally:
                response.close()
            if status == 429:
                self._rate_limit(host, status, headers)
                self._record(paper, current, status, "RATE_LIMITED", self.paused[host][1])
                rules = Robots(allow_all=True)
                break
            check = detect_bot_check(status, headers, body, plain_403=False)
            if check:
                self.block(host, check, current, status)
                self._record(paper, current, status, "BOT_CHECK", check)
                rules = Robots(allow_all=True)
                break
            if 200 <= status < 300:
                rules = Robots(body.decode("utf-8", errors="replace"))
                self._record(paper, current, status, "ROBOTS", f"{len(body)} bytes of rules")
            elif 400 <= status < 500:
                rules = Robots(allow_all=True)   # RFC 9309: robots.txt unavailable, no rules
                self._record(paper, current, status, "ROBOTS", "no robots.txt: no rules")
            else:
                self._record(paper, current, status, "ROBOTS_UNREACHABLE", f"HTTP {status}")
            break
        if rules is None:
            # RFC 9309: an unreachable robots.txt means complete disallow; try again on a later run.
            self.paused[host] = ("ROBOTS_UNREACHABLE", f"robots.txt of {host} could not be read; host paused for this run")
        self.robots[host] = rules

    # -- one URL ---------------------------------------------------------------------------------------------
    def fetch(self, paper: str, url: str, destination: Path) -> Tuple[str, str, str]:
        """Try one URL. Returns (outcome, detail, url_where_it_ended); DOWNLOADED means destination holds a PDF."""
        current, chain, hops = url, [], 0
        while True:
            host = host_of(current)
            if host in self.blocked:
                self._record(paper, current, None, "HOST_BLOCKED", self.blocked_reason(host))
                return "HOST_BLOCKED", f"{host}: {self.blocked_reason(host)}", current
            if host in self.paused:
                outcome, detail = self.paused[host]
                self._record(paper, current, None, outcome, detail)
                return outcome, detail, current
            if self.failures.get(host, 0) >= MAX_NETWORK_FAILURES:
                self._record(paper, current, None, "HOST_UNREACHABLE")
                return "HOST_UNREACHABLE", f"{host}: {MAX_NETWORK_FAILURES} consecutive failures in this run", current
            if host not in self.robots:
                self._load_robots(current, paper)
                continue   # re-check: robots.txt may have blocked or paused the host
            rules = self.robots[host]
            if rules is not None and not rules.allowed(current):
                self._record(paper, current, None, "ROBOTS_DISALLOWED")
                return "ROBOTS_DISALLOWED", f"robots.txt of {host} disallows this path", current
            try:
                response = self._get(current)
            except requests.RequestException as exc:
                self.failures[host] = self.failures.get(host, 0) + 1
                self._record(paper, current, None, "NETWORK_ERROR", type(exc).__name__)
                return "NETWORK_ERROR", type(exc).__name__, current
            # Keep requests' case-insensitive mapping: many servers send "location" or "retry-after" in lower case.
            status, headers = response.status_code, response.headers
            if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                response.close()
                check = detect_bot_check(status, headers, b"", plain_403=False)
                if check:
                    self._block_chain(chain + [host], check, current, status)
                    self._record(paper, current, status, "BOT_CHECK", check)
                    return "BOT_CHECK", f"{host}: {check}", current
                self.failures[host] = 0
                self._record(paper, current, status, "REDIRECT", headers["Location"])
                chain.append(host)
                current = urljoin(current, headers["Location"])
                hops += 1
                if hops > MAX_REDIRECTS:
                    self._record(paper, current, None, "TOO_MANY_REDIRECTS")
                    return "TOO_MANY_REDIRECTS", f"more than {MAX_REDIRECTS} redirects", current
                continue
            return self._receive(paper, current, response, destination, chain)

    def _block_chain(self, hosts: List[str], reason: str, url: str, status: Optional[int]) -> None:
        """Block the host that served a bot check and every host that redirected to it. A resolver that only
        redirected is left alone: its next DOI may lead to another publisher (one that served the check is paused)."""
        for index, host in enumerate(reversed(hosts)):
            if index and host in RESOLVER_HOSTS:
                continue
            self.block(host, reason if index == 0 else f"redirects to a bot check: {reason}", url, status)

    def _receive(self, paper: str, url: str, response: requests.Response, destination: Path,
                 chain: List[str]) -> Tuple[str, str, str]:
        host = host_of(url)
        status, headers = response.status_code, response.headers
        part = destination.with_name(destination.name + ".part")
        try:
            chunks = response.iter_content(65536)
            head = b""
            for chunk in chunks:
                head += chunk
                if len(head) >= SNIFF_BYTES:
                    break
            pdf = _looks_like_pdf(head)
            if status == 429:
                self._rate_limit(host, status, headers)
                self._record(paper, url, status, "RATE_LIMITED", self.paused[host][1])
                return "RATE_LIMITED", self.paused[host][1], url
            check = detect_bot_check(status, headers, b"" if pdf else head)
            if check:
                self._block_chain(chain + [host], check, url, status)
                self._record(paper, url, status, "BOT_CHECK", check)
                return "BOT_CHECK", f"{host}: {check}", url
            if status >= 500:
                self.failures[host] = self.failures.get(host, 0) + 1
                if headers.get("Retry-After"):
                    self.paused[host] = ("HTTP_5XX", f"{host}: HTTP {status}, Retry-After {headers['Retry-After']}; "
                                                     "paused for this run")
                self._record(paper, url, status, "HTTP_5XX")
                return "HTTP_5XX", f"HTTP {status}", url
            self.failures[host] = 0
            if status >= 400:
                self._record(paper, url, status, f"HTTP_{status}")
                return f"HTTP_{status}", f"HTTP {status}", url
            if not pdf:
                kind = safe_str(headers.get("Content-Type")).split(";")[0] or "unknown type"
                self._record(paper, url, status, "NOT_PDF", kind)
                return "NOT_PDF", f"server returned {kind}, not a PDF", url
            declared = _content_length(headers)
            if declared is not None and declared > self.max_bytes:
                self._record(paper, url, status, "TOO_LARGE", f"{declared} bytes")
                return "TOO_LARGE", f"{declared} bytes > limit (raise --max-mb and use --retry-failed)", url
            destination.parent.mkdir(parents=True, exist_ok=True)
            size = len(head)
            with part.open("wb") as handle:
                handle.write(head)
                for chunk in chunks:
                    size += len(chunk)
                    if size > self.max_bytes:
                        break
                    handle.write(chunk)
            if size > self.max_bytes:
                part.unlink(missing_ok=True)
                self._record(paper, url, status, "TOO_LARGE", f"> {self.max_bytes} bytes")
                return "TOO_LARGE", f"more than {self.max_bytes} bytes (raise --max-mb and use --retry-failed)", url
            if (declared is not None and size != declared) or not _complete_pdf(part):
                part.unlink(missing_ok=True)
                self._record(paper, url, status, "TRUNCATED", f"{size} bytes")
                return "TRUNCATED", f"incomplete PDF ({size} bytes)", url
            os.replace(part, destination)
            self._record(paper, url, status, DOWNLOADED, f"{size} bytes")
            return DOWNLOADED, f"{size} bytes", url
        except requests.RequestException as exc:
            part.unlink(missing_ok=True)
            self.failures[host] = self.failures.get(host, 0) + 1
            self._record(paper, url, status, "NETWORK_ERROR", type(exc).__name__)
            return "NETWORK_ERROR", type(exc).__name__, url
        finally:
            response.close()


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    return read_jsonl(path) if path.suffix.lower() == ".jsonl" else read_csv(path)


def _link(url: str, label: str) -> str:
    """An HTML link, only for http(s) URLs (never javascript: or data: URLs taken from the input)."""
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return f"<a href='{html.escape(url)}' rel='noopener noreferrer'>{html.escape(label)}</a>"


def _write_manual_html(path: Path, rows: List[Dict[str, Any]], manual_dir: Path) -> None:
    by_reason: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_reason.setdefault(row["reason"], []).append(row)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Manual download list</title>",
        "<style>body{font-family:sans-serif;margin:2em;max-width:70em}td,th{border-bottom:1px solid #ddd;padding:4px 8px;"
        "vertical-align:top;text-align:left}table{border-collapse:collapse}code{background:#f3f3f3}</style></head><body>",
        f"<h1>Manual download list ({len(rows)} papers)</h1>",
        f"<p>Save each PDF under the given file name in <code>{html.escape(str(manual_dir))}</code>, then run "
        "<code>fetch-oa</code> again: files found there are registered and leave this list.</p>",
    ]
    for reason in sorted(by_reason):
        group = sorted(by_reason[reason], key=lambda r: (r["host"], r["title"]))
        parts.append(f"<h2>{html.escape(reason)} ({len(group)})</h2><table><tr><th>File name</th><th>Paper</th>"
                     "<th>Links</th><th>Detail</th></tr>")
        for row in group:
            links = [link for link in (
                _link(f"https://doi.org/{row['doi']}", "DOI") if row["doi"] else "",
                _link(row["landing_page"], "landing page"),
                _link(row["pdf_url"], "PDF"),
            ) if link]
            parts.append(f"<tr><td><code>{html.escape(row['file_name'])}</code></td><td>{html.escape(row['title'])}"
                         f"<br><small>{html.escape(row['source'])} {html.escape(row['year'])}</small></td>"
                         f"<td>{' · '.join(links)}</td><td><small>{html.escape(row['detail'])}</small></td></tr>")
        parts.append("</table>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_ris(path: Path, rows: List[Dict[str, Any]]) -> None:
    lines = []
    for row in rows:
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {_clean(row['title'])}")
        for author in [_clean(a) for a in safe_str(row.get("authors")).split(";") if a.strip()]:
            lines.append(f"AU  - {author}")
        for tag, field in (("PY", "year"), ("T2", "source"), ("DO", "doi")):
            if _clean(row[field]):
                lines.append(f"{tag}  - {_clean(row[field])}")
        url = row["landing_page"] or (f"https://doi.org/{row['doi']}" if row["doi"] else row["pdf_url"])
        if _clean(url):
            lines.append(f"UR  - {_clean(url)}")
        lines.append(f"N1  - Save as {row['file_name']} ({row['reason']})")
        lines.append("ER  - ")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _overall(urls: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    """Status shown for a paper with several links: the most relevant outcome, with every link's detail."""
    outcomes = [info["outcome"] for info in urls.values()]
    known = [o for o in _PRIORITY if o in outcomes]
    status = known[0] if known else (sorted(outcomes)[0] if outcomes else "NOT_ATTEMPTED")
    detail = " | ".join(f"{info['outcome']}: {info.get('detail', '')}".rstrip(": ") for info in urls.values())
    return status, detail


def fetch_open_access(
    input_path: Path,
    output_dir: Path,
    delay: float = DEFAULT_DELAY,
    timeout: float = REQUEST_TIMEOUT,
    max_mb: float = DEFAULT_MAX_MB,
    limit: Optional[int] = None,
    retry_failed: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    rows = _load_rows(input_path)
    pdf_dir, manual_dir = output_dir / "pdf", output_dir / "pdf_manual"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    manual_dir.mkdir(parents=True, exist_ok=True)
    state_path, blocked_path = output_dir / "fetch_state.json", output_dir / "blocked_hosts.json"
    state: Dict[str, Dict[str, Any]] = read_json(state_path).get("papers", {}) if state_path.exists() else {}
    blocked: Dict[str, Dict[str, Any]] = read_json(blocked_path) if blocked_path.exists() else {}
    log: List[Dict[str, Any]] = []
    fetcher = Fetcher(blocked, blocked_path, delay, timeout, int(max_mb * 1024 * 1024), log)

    papers: Dict[str, Dict[str, Any]] = {}
    skipped_rows = 0
    for row in rows:
        key = paper_key(row)
        if not key:
            skipped_rows += 1
        elif key not in papers:
            papers[key] = row

    def url_needs_try(info: Optional[Dict[str, Any]]) -> bool:
        if info is None or info.get("outcome") in TRANSIENT:
            return True
        if info.get("outcome") in BLOCK_OUTCOMES:
            return info.get("host") not in blocked    # only once the host has been removed from blocked_hosts.json
        return retry_failed

    def save_state() -> None:
        _write_json_atomic(state_path, {"tool_version": __version__, "papers": state})

    attempted = 0
    try:
        for index, (key, row) in enumerate(papers.items(), start=1):
            entry = state.get(key, {})
            target, manual_file = pdf_dir / f"{key}.pdf", manual_dir / f"{key}.pdf"
            if _is_pdf(manual_file):
                stat = manual_file.stat()
                if entry.get("status") != MANUAL_DONE or entry.get("bytes") != stat.st_size or entry.get("mtime") != stat.st_mtime:
                    state[key] = {"status": MANUAL_DONE, "file": manual_file.relative_to(output_dir).as_posix(),
                                  "sha256": sha256_file(manual_file), "bytes": stat.st_size, "mtime": stat.st_mtime,
                                  "updated": _now()}
                continue
            if entry.get("status") == DOWNLOADED and _is_pdf(target):
                continue
            if entry.get("status") in {DOWNLOADED, MANUAL_DONE}:
                entry = {}                              # the file was deleted: start again
            urls = candidate_urls(row)
            is_oa = _truthy(row.get("is_open_access")) if "is_open_access" in row else bool(urls)
            if not is_oa or not urls:
                state[key] = {"status": "NOT_OPEN_ACCESS" if not is_oa else "NO_OA_URL",
                              "detail": safe_str(row.get("oa_status")), "updated": _now()}
                continue
            tried = dict(entry.get("urls", {}))
            todo = [u for u in urls if url_needs_try(tried.get(u))]
            if not todo:
                continue
            if limit is not None and attempted >= limit:
                continue
            sent_before = fetcher.requests_sent
            downloaded_from = ""
            for url in urls:
                if url not in todo:
                    continue
                try:
                    outcome, detail, final_url = fetcher.fetch(key, url, target)
                except Exception as exc:  # noqa: BLE001 - one bad response must not stop the run or lose the state
                    outcome, detail, final_url = "ERROR", f"{type(exc).__name__}: {exc}"[:200], url
                tried[url] = {"outcome": outcome, "detail": detail, "host": host_of(final_url), "final_url": final_url,
                              "updated": _now()}
                if outcome == DOWNLOADED:
                    downloaded_from = final_url
                    break
            if fetcher.requests_sent > sent_before:
                attempted += 1
            if downloaded_from:
                stat = target.stat()
                state[key] = {"status": DOWNLOADED, "detail": f"{stat.st_size} bytes", "urls": tried,
                              "file": target.relative_to(output_dir).as_posix(), "sha256": sha256_file(target),
                              "bytes": stat.st_size, "url": downloaded_from, "host": host_of(downloaded_from),
                              "updated": _now()}
            else:
                status, detail = _overall(tried)
                hosts = [info["host"] for info in tried.values() if info["outcome"] == status]
                state[key] = {"status": status, "detail": detail, "urls": tried, "host": hosts[0] if hosts else "",
                              "updated": _now()}
            save_state()
            if verbose:
                shown = state[key]["status"]
                _say(f"[fetch-oa {index}/{len(papers)}] {shown:<18} {key} "
                     f"{'' if shown == DOWNLOADED else state[key]['detail'][:150]}".rstrip())
    finally:
        fetcher.close()
        save_state()
        _write_json_atomic(blocked_path, blocked)
        log_path = output_dir / "fetch_log.csv"
        previous_log = read_csv(log_path) if log_path.exists() else []
        write_csv(log_path, previous_log + log, ["time", "paper", "url", "host", "http_status", "outcome", "detail"])
        summary = _write_outputs(output_dir, manual_dir, papers, state, blocked, fetcher, input_path, delay, timeout,
                                 max_mb, limit, retry_failed, skipped_rows)
    return summary


def _write_outputs(output_dir: Path, manual_dir: Path, papers: Dict[str, Dict[str, Any]], state: Dict[str, Dict[str, Any]],
                   blocked: Dict[str, Dict[str, Any]], fetcher: Fetcher, input_path: Path, delay: float, timeout: float,
                   max_mb: float, limit: Optional[int], retry_failed: bool, skipped_rows: int) -> Dict[str, Any]:
    """Rebuild every list from the full state (also after an interruption)."""
    downloaded, manual = [], []
    for key, row in papers.items():
        entry = state.get(key, {"status": "NOT_ATTEMPTED", "detail": "not tried yet (--limit or interrupted run)"})
        base = {"paper": key, "openalex_id": safe_str(row.get("openalex_id")), "doi": safe_str(row.get("doi")),
                "title": _clean(row.get("title")), "year": safe_str(row.get("publication_year")),
                "source": _clean(row.get("source")), "authors": safe_str(row.get("authors")),
                "oa_status": safe_str(row.get("oa_status"))}
        if entry["status"] in {DOWNLOADED, MANUAL_DONE}:
            downloaded.append({**base, "status": entry["status"], "file": entry.get("file", ""),
                               "sha256": entry.get("sha256", ""), "bytes": entry.get("bytes", ""),
                               "source_url": entry.get("url", "")})
        else:
            manual.append({**base, "reason": entry["status"], "detail": entry.get("detail", ""),
                           "host": entry.get("host", ""), "file_name": f"{key}.pdf",
                           "pdf_url": safe_str(row.get("best_oa_pdf_url")) or safe_str(row.get("oa_url")),
                           "landing_page": safe_str(row.get("best_oa_landing_page"))})
    write_csv(output_dir / "downloaded.csv", downloaded,
              ["paper", "status", "file", "sha256", "bytes", "openalex_id", "doi", "title", "year", "source", "oa_status", "source_url"])
    write_csv(output_dir / "manual_download.csv", manual,
              ["paper", "file_name", "reason", "detail", "host", "openalex_id", "doi", "title", "year", "source",
               "oa_status", "pdf_url", "landing_page"])
    _write_manual_html(output_dir / "manual_download.html", manual, manual_dir)
    _write_ris(output_dir / "manual_download.ris", manual)

    counts: Dict[str, int] = {}
    for key in papers:
        status = state.get(key, {}).get("status", "NOT_ATTEMPTED")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "tool_version": __version__, "input": str(input_path), "finished": _now(), "papers": len(papers),
        "rows_without_identifier": skipped_rows,
        "downloaded": sum(1 for d in downloaded if d["status"] == DOWNLOADED),
        "manually_downloaded": sum(1 for d in downloaded if d["status"] == MANUAL_DONE),
        "manual_list": len(manual), "status_counts": dict(sorted(counts.items())),
        "blocked_hosts": sorted(blocked), "paused_hosts_this_run": sorted(fetcher.paused),
        "requests_sent_this_run": fetcher.requests_sent,
        "settings": {"delay_seconds": delay, "timeout_seconds": timeout, "max_mb": max_mb, "limit": limit,
                     "retry_failed": retry_failed, "user_agent": USER_AGENT},
    }
    _write_json_atomic(output_dir / "fetch_summary.json", summary)
    return summary
