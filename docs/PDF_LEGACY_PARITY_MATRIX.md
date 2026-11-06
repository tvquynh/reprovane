# PDF acquisition: parity with OpenAlex Literature Intelligence 4.1.0

Reference implementation: `litintel/fulltext.py` and the `fetch-oa` command of
OpenAlex Literature Intelligence 4.1.0 (predecessor commit `f70796d1ef6003e24ce3afa1328088cc515dc0a0`).
`reprovane/fulltext.py` is the same algorithm with only the User-Agent string changed and is kept
unchanged for the legacy regression tests. The replacement is the `reprovane pdf` command family
described in the [user guide](PDF_ACQUISITION_USER_GUIDE.md) and the
[architecture](PDF_ACQUISITION_ARCHITECTURE.md).

## Functional matrix

| 4.1.0 capability | Unified implementation | Status |
|---|---|---|
| `fetch-oa --input *_main.csv / *_enriched.* / *_raw.*` | `pdf import --input` (format auto-detected, exact bytes and column mapping kept) then `pdf discover` | Equivalent |
| Inputs other than 4.1.0 files | OpenAlex Work JSONL, Reprovane exports, identifier lists, saved acquisitions, discovery runs, sealed releases | Added |
| Paper key: OpenAlex ID, else DOI, else hash of title/year/URLs | Same key computed per record (`paper` column, manual file names); identity on exact Work ID/DOI with conflict reports | Equivalent, stricter identity |
| Duplicate rows skipped by key | Exact-identifier grouping; duplicates counted (`duplicate_records`); rows without identifiers are never grouped | Equivalent for identified rows |
| Rows without identifier counted | Rejected records with reason `NO_IDENTIFIER_OR_CONTENT` | Equivalent |
| Candidates `best_oa_pdf_url`, then `oa_url` | DIRECT_PDF before OA_URL; landing pages kept for humans; optional OpenAlex/Unpaywall/OpenAlex Content lookups | Equivalent plus optional sources |
| `is_open_access` false: no request | Routes explicitly marked closed are excluded (`NOT_OPEN_ACCESS`) | Equivalent |
| Version of the copy | Version claim kept per candidate and artifact, explicit allowed-version policy, never verified automatically | Added |
| Sequential requests, one User-Agent | Sequential; `Reprovane/<version> (+repository URL)` and `Accept: application/pdf,*/*;q=0.8` | Equivalent |
| `--delay` (minimum 1 s) and robots Crawl-delay (cap 60 s) | `per_host_delay_seconds` (minimum 1, default 2); Crawl-delay honoured up to `max_crawl_delay_seconds` (60), a longer one defers the host | Equivalent up to 60 s, stricter above |
| robots.txt RFC 9309 matching, unreachable means pause | Same parser; 5xx/unreachable pauses the host; 429 pauses and re-reads later | Equivalent |
| Bot check / CAPTCHA / 403 / TDM reservation: host blocked for good | Same detector; persistent `BLOCK` host events, redirect chain blocked except resolvers | Equivalent |
| `blocked_hosts.json` edited by hand to unblock | `pdf hosts --unblock HOST` appends an auditable `UNBLOCK` event | Equivalent, auditable |
| HTTP 429: host paused for the run | Short `Retry-After` (seconds or HTTP-date) waited for, then retried in place; long one becomes a persisted cooldown; host paused for the invocation | Improved |
| 5xx with `Retry-After`: host paused | Bounded retries with backoff; long `Retry-After` becomes a cooldown | Improved |
| 3 consecutive network failures pause a host | `host_failure_threshold` (default 3) | Equivalent |
| Redirects followed one hop at a time (5 max) | Same, each hop gated (blocked host, robots, SSRF check) | Equivalent plus SSRF guard |
| Not a PDF (header within first 1024 bytes) | 64 KiB sniff; must start with `%PDF-` and pass strict pypdf validation | Stricter |
| `--max-mb` (default 100) | `max_pdf_bytes` (default 100 MiB), declared and streamed sizes | Equivalent |
| Truncated / missing `%%EOF` | `TRUNCATED` (length mismatch) or `PDF_VALIDATION_FAILED` | Equivalent |
| `--limit` | `pdf download --limit` | Equivalent |
| `--retry-failed` | `pdf download --retry-failed` (bounded by `max_attempts_per_candidate`) | Equivalent, bounded |
| Resume by re-running (`fetch_state.json`) | Append-only attempts; `pdf resume`; interrupted attempts retried | Equivalent, crash-safe |
| Ctrl+C exit 130 | Exit 130 with resume hint | Equivalent |
| Files in `pdf/` named `<key>.pdf` | Content-addressed store; `pdf export-pdfs` writes `<key>.pdf` copies | Equivalent view |
| `pdf_manual/<key>.pdf` registered on next run | `pdf manual-import` (file, folder, recursive, mapping, reconciliation) and `pdf legacy-import` | Improved |
| `downloaded.csv`, `manual_download.csv/.html/.ris`, `fetch_summary.json`, `fetch_log.csv` | Same report names plus `reconciliation.csv`, `pdf_manifest.json`, `pdf_items.jsonl`, `SHA256SUMS.txt`; the request ledger replaces `fetch_log.csv` | Equivalent plus verification |
| Existing 4.1.0 output folders | `pdf legacy-import` (read-only; SHA must match `fetch_state.json`; an entry without a recorded SHA links only through validated identifiers; every per-paper outcome kept as history) | Added |
| Historical `main.csv` / `excluded.csv` classification | Kept as `historical.machine_classification`; never a screening decision | Preserved separately |

## Intentional differences

| Situation | 4.1.0 | Unified | Reason |
|---|---|---|---|
| Junk bytes before `%PDF-` | `DOWNLOADED` | `PDF_VALIDATION_FAILED` | Strict structural validation shared with Stage11B |
| Redirect to a private or metadata address | `DOWNLOADED` | `UNSAFE_URL` | SSRF prevention |
| URL with a non-default port | contacted | `UNSAFE_OR_INVALID_URL` at import | Shared conservative URL rule |
| `is_open_access` column present but empty | treated as closed | treated as absent (not explicitly closed) | Empty is unknown, not closed |
| Short `Retry-After` on 429 | host paused for the run | retried in place, then paused | Honours the server's stated delay |
| Same PDF bytes offered for two publications by hand | both registered | reconciliation item | Prevents attaching a PDF to the wrong publication |
| Two rows without identifiers and with the same title and year | one paper (same key) | two works, `LEGACY_KEY_COLLISION` reported | Equal titles do not prove identity |
| Manual file named after a title typed into the OpenAlex ID column | linked by file name | `KEY_NEEDS_CONFIRMATION` | Never link a PDF because its name looks like a title |
| robots.txt `Crawl-delay` above 60 s | waited 60 s | host deferred for the command (`CRAWL_DELAY_TOO_LONG`) | Never wait less than the host asks |

## Benchmark on the shared synthetic fixture

`tools/pdf_legacy_parity.py` runs both implementations on the same CSV (17 paper rows plus one
row without identifier) and the same in-memory web, with no network. Results below are from the
real predecessor checkout (`--legacy-root`); the in-repository reference gives identical results.
They describe synthetic fixtures only and are not an estimate of real-world download success.

| Metric | 4.1.0 | Unified |
|---|---|---|
| Accepted input records / unique works / rows without identifier | 16 papers, 1 skipped | 17 accepted, 16 works, 1 rejected |
| Candidate URLs | 16 | 14 (closed-flag URL excluded as evidence, unsafe-port URL refused) |
| Works with an attemptable URL | 14 | 13 |
| Successful automatic downloads | 4 | 2 (2 intentional refusals) |
| Manual PDFs registered | 1 | 1 |
| Resume after an interrupted pass equals a single pass | yes | yes |
| Byte-identical PDFs for common successes | 3 of 3 | 3 of 3 |
| Failure classification | 16 papers | 13 equivalent, 3 intentional, 0 regressions |
| 4.1.0 folder imported read-only | n/a | folder unchanged, verified, blocked host carried over |

| Paper | 4.1.0 | Unified | Classification |
|---|---|---|---|
| W1 | DOWNLOADED | DOWNLOADED | Equivalent |
| W2 | BOT_CHECK | BOT_CHECK | Equivalent |
| W3 | HOST_BLOCKED | HOST_BLOCKED | Equivalent |
| W4 | HOST_BLOCKED | HOST_BLOCKED | Equivalent |
| W5 | DOWNLOADED | DOWNLOADED | Equivalent |
| W6 | ROBOTS_DISALLOWED | ROBOTS_DISALLOWED | Equivalent |
| W7 | RATE_LIMITED | RATE_LIMITED | Equivalent |
| W8 | MANUAL_DOWNLOADED | MANUAL_IMPORTED | Equivalent |
| W9 | HTTP_404 | HTTP_404 | Equivalent |
| W10 | NOT_OPEN_ACCESS | NOT_OPEN_ACCESS | Equivalent |
| W11 | DOWNLOADED | PDF_VALIDATION_FAILED | Intentional (strict validation) |
| W12 | TRUNCATED | TRUNCATED | Equivalent |
| W13 | TOO_LARGE | TOO_LARGE | Equivalent |
| W14 | DOWNLOADED | UNSAFE_URL | Intentional (SSRF guard) |
| W15 | ROBOTS_UNREACHABLE | ROBOTS_UNREACHABLE | Equivalent |
| W16 | ROBOTS_UNREACHABLE | UNSAFE_OR_INVALID_URL | Intentional (port rule) |

Reproduce:

```bash
python tools/pdf_legacy_parity.py
python tools/pdf_legacy_parity.py --legacy-root ../openalex-literature-intelligence --json parity.json
```

`tests/test_pdf_legacy_parity.py` runs the same benchmark in the offline suite and fails on any
unclassified difference.
