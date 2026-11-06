# Unified PDF acquisition architecture

This subsystem replaces the OpenAlex Literature Intelligence 4.1.0 `fetch-oa` workflow and
adds manual PDF ingestion, while keeping the accepted Stage11B review acquisition intact.
User instructions are in the [PDF acquisition user guide](PDF_ACQUISITION_USER_GUIDE.md);
the feature-by-feature comparison with 4.1.0 is in the [parity matrix](PDF_LEGACY_PARITY_MATRIX.md).

## Two profiles, one set of shared components

| Concern | `DISCOVERY_DOWNLOAD` (`reprovane pdf ...`) | `REVIEW_FULLTEXT` (`reprovane pdf review ...` = `reprovane fulltext ...`) |
|---|---|---|
| Input | Files, identifier lists, saved acquisitions, discovery runs, sealed releases | Exactly one verified SEALED corpus handoff |
| Policy | `pdf-discovery-policy/1` | `fulltext-policy/1` (unchanged) |
| Persistence | Migration 0014 (`pdf_*` tables) | Migration 0012 (`fulltext_*` tables, unchanged) |
| Candidate rules | `fulltext_acquisition.candidates.location_routes` + `make_candidates` | same functions |
| Transport | `fulltext_acquisition.transport.SafeTransport` with politeness hooks | same class; `pdf review acquire` adds the same politeness hooks, `fulltext acquire` none |
| PDF validation | `fulltext_acquisition.safety.validate_pdf` (strict pypdf, at least one page) | same function |
| Storage | Content-addressed store `raw/sha256/xx/<sha256>` | same store |
| Manual PDFs | Linked to works (`pdf_work_artifact`) | Supplementary only (`pdf_review_supplement`) |

There is one downloader implementation. `SafeTransport.fetch` gained two optional hooks
(`gate` before every connection and `inspect` on every response head) plus a `terminal` flag on
`FetchError`. Without hooks its behaviour is unchanged, which the Stage11B and deterministic
end-to-end fixtures confirm byte for byte.

## Module map (`reprovane/pdf_acquisition/`)

| Module | Responsibility |
|---|---|
| `policy.py` | Discovery policy schema, validation, canonical SHA-256, adapters for the shared transport and candidate rules |
| `inputs.py` | Pure input adapters (4.1.0 flat rows, OpenAlex Work JSON, Reprovane exports, identifier lists, acquisitions, sealed handoffs) |
| `identifiers.py` | Exact DOI / OpenAlex Work ID normalization and the 4.1.0 file-name key |
| `identity.py` | Deterministic work grouping on exact identifiers, conflict issues |
| `candidates.py` | Frozen per-work plans on top of the shared eligibility rules |
| `politeness.py` | robots.txt (RFC 9309), per-host pacing, bot-check detection, Retry-After cooldowns, blocked hosts |
| `engine.py` | Sequential resumable downloader, transport factory |
| `store.py` | Schema 14 persistence, ledger and budgets, statuses, offline verification, close |
| `manual.py` | Manual ingestion, mapping files, reconciliation queue for both profiles |
| `legacy.py` | Read-only import of a 4.1.0 `fetch-oa` output folder |
| `report.py` | Semantic manifest, legacy-style reports, portable export verification, review status |
| `review.py` | Politeness wrapper for Stage11B acquisition (`reprovane pdf review acquire`) |
| `pdfcheck.py` | Shared structural PDF check without pypdf repair warnings on the terminal |
| `maintenance.py` | Orphan detection and quarantine |
| `__main__.py` | `reprovane pdf` command family |

## Run lifecycle

`OPEN` (inputs) -> `DISCOVERING` (identity frozen; plans frozen one work at a time) ->
`ACQUIRING` (downloads, manual ingestion, reconciliation) -> `CLOSED` (semantic manifest).
SQLite triggers allow only these transitions and reject every UPDATE or DELETE of evidence
rows. Inserts require a connection-scoped authorization function registered by the store, so
an ordinary external connection cannot write. The frozen policy and its SHA-256 never change.

## Identity

`identity.freeze` groups accepted records with a union-find on exact OpenAlex Work IDs, then
attaches DOI-only records to exactly one OpenAlex group sharing the DOI. A DOI-only record that
matches several OpenAlex works stays separate and is reported. Records without a DOI or Work ID
are never grouped: each is its own work (`row:<record_id>`), because an equal title-derived
4.1.0 key does not prove identity; a shared key is reported. Issues:
`DOI_SHARED_BY_SEVERAL_WORKS`, `DOI_RECORD_MATCHES_SEVERAL_OPENALEX_WORKS`,
`MULTIPLE_DOIS_FOR_OPENALEX_WORK`, `LEGACY_KEY_COLLISION`. The grouping is a pure function of
the stored records and is recomputed by `verify`.

Each work also carries `match_keys`, the file-name keys safe for automatic manual matching
(valid Work IDs, `doi_...` keys of valid DOIs, and a record's 4.1.0 key when the input adapter
marked it `legacy_key_trusted`), and `file_key`, the suggested file name: the 4.1.0 key when it is
safe and unique, otherwise the work ID. A key is trusted only when recomputing it from the
validated identifiers alone (or, without identifiers, from the content fields 4.1.0 hashed)
gives the same key; its shape (`row_...`, `doi_...`) proves nothing. A key copied from an invalid
identifier value stays in `legacy_keys` for display and fetch-state replay only.

## Candidates and eligibility

Routes come from input rows (`INPUT_ROW`: `best_oa_pdf_url` as DIRECT_PDF, `oa_url` as
OA_URL, `best_oa_landing_page` as LANDING_PAGE), saved OpenAlex records (`OPENALEX_RECORD`),
sealed releases (`FROZEN`) and optional lookups (`OPENALEX`, `UNPAYWALL`, `OPENALEX_CONTENT`).
OpenAlex is queried by Work ID, or by exact DOI (`/works/doi:...`, linkage `doi:...`) for a
work without a Work ID; the response must echo the same DOI and its Work ID is stored as
`resolved_openalex_id` evidence only, because identity is frozen before discovery. Each
successful lookup also stores a `display` block (title, year, DOI) that fills empty
bibliographic columns of the manual list through `provider_display` in the item status; it
never affects identity, eligibility or ranking.
Each URL keeps every route that reported it. Eligibility is the shared Stage11B rule set
(version policy, no landing pages, OA evidence, allowed schemes, version-conflict refusal).
Discovery ordering is version rank, URL-type rank, best-location flag, host rank, evidence
source, candidate ID. Routes explicitly marked closed are excluded under the default
`NOT_EXPLICITLY_CLOSED` OA policy (4.1.0 behaviour); `EXPLICIT_OPEN_REQUIRED` demands a true flag.

## Download engine and politeness

- One request at a time (`concurrency: SEQUENTIAL`). Bounded concurrency was not enabled: the
  ledger, per-host pacing and blocked-host register are designed for a single writer.
- Every connection (first URL, redirect hop, retry) passes `Politeness.gate`: blocked host,
  cooldown, invocation pause, failure threshold, robots.txt, then pacing.
- robots.txt: 2xx parsed with RFC 9309 matching (agent `Reprovane`); 4xx means no rules;
  5xx, network errors or oversize mean complete disallow and pause the host for the invocation;
  429 pauses the host and re-reads robots.txt next time.
- Pacing waits for the per-host delay, the robots.txt Crawl-delay and any short Retry-After,
  never less. A Crawl-delay above `max_crawl_delay_seconds` is not shortened: the gate pauses the
  host for the invocation (`CRAWL_DELAY_TOO_LONG`, transient).
- `inspect_pdf`: 429 and 5xx with `Retry-After` (seconds or HTTP-date) up to `max_retry_delay`
  set a not-before time for the host and are retried in place; longer values record a
  `COOLDOWN` host event. Bot checks (challenge titles and markers,
  `cf-mitigated`, AWS WAF, `TDM-Reservation: 1`, plain HTTP 403) block the serving host and every
  non-resolver host that redirected to it (`BLOCK` event, written immediately). A stored reason
  never quotes the remote page: a matched page title is replaced by its known challenge phrase.
  Any other reason containing a credential, the value of a sensitive query parameter of a runtime
  (signed) URL (the parameters `sanitize_url` redacts, four characters or longer) or private-looking
  text is stored as `REASON_WITHHELD_PRIVATE_LOOKING_TEXT`; the same values are refused in request
  records. Retained response headers cannot carry server-chosen text: the media type comes from a
  fixed vocabulary (otherwise `OTHER`), and a length or `Retry-After` keeps its grammar but is
  `REDACTED` when it repeats a sensitive query value of any request hop (redirects included) or a
  credential.
  A 200 response that does not start with `%PDF-` stops after the
  first 64 KiB as `NOT_PDF`. The 64-KiB prefix read obeys the same elapsed-time bound as the rest
  of the body, and every byte consumed before a refusal is charged to the byte budget.
- Every `Retry-After` is recorded as a host cooldown of the full requested length, so a later
  command never asks earlier. A remaining cooldown up to `max_retry_delay` is waited for; a longer
  one defers the host; one beyond `max_cooldown_seconds` blocks the host until an operator unblocks
  it. robots.txt responses with 429 or 5xx and `Retry-After` follow the same rules and are
  attributed to the host that answered; every robots.txt connection, including redirect hops,
  checks that host's block and cooldown first. Provider lookups record their `Retry-After` the same
  way: later lookups to that provider, in this or a later command, wait up to `max_retry_delay` and
  otherwise stay pending; beyond `max_cooldown_seconds` the provider host is blocked. Reasons are
  fixed text (`HTTP 429 Retry-After`); the deadline lives only in `until_utc`. A `Retry-After` that
  repeats a sensitive value of any request hop is never parsed into a record: the transport hands it
  to the inspector as `REDACTED`, and the host is blocked (`Retry-After withheld`), so no request is
  made earlier than asked and the value is not echoed.
- SSRF: the shared transport resolves every hop, requires all addresses to be public, pins the
  connection to the validated address with TLS hostname verification, refuses user-info URLs,
  non-default ports and HTTPS downgrades, and sends `Authorization` only to the original
  OpenAlex origin. The Unpaywall email is added only to the runtime URL.
- Budgets: `http_requests`, `bytes` and `provider_lookups` are checked inside the transaction that
  reserves each request; `items` and `artifacts` before each work, and a new distinct artifact is
  refused again inside the registration transaction (`BUDGET_EXHAUSTED`, nothing stored). A request
  result's charged bytes must equal the bytes in its hashed result and stay within its reservation
  (trigger and `verify`).
- Provider lookups that fail temporarily (DNS failure, network, timeout, 429, 5xx, budget) are not
  frozen as evidence; the work stays unplanned and the run stays `DISCOVERING` (no downloads in
  that run) until a later `discover` or `resume` succeeds, or `--accept-failed-lookups` records the
  failure as evidence. A name that resolves to a private address stays a permanent `UNSAFE_URL`;
  the shared transport separates the two only when hooks are passed, so Stage11B is unchanged.
- Signed URLs are stored sanitized. `download --runtime-urls-from FILE` re-reads the exact URLs
  from the user's own input file, including URLs nested in OpenAlex Work JSON, and matches them by
  the hash of the normalized URL; they stay in memory for that command.

## Persistence and crash safety (migration 0014)

Tables: `pdf_run`, `pdf_input_batch`, `pdf_input_record`, `pdf_work`, `pdf_evidence`,
`pdf_plan`, `pdf_candidate`, `pdf_request`, `pdf_request_result`, `pdf_attempt`,
`pdf_attempt_result`, `pdf_artifact`, `pdf_work_artifact`, `pdf_import_session`,
`pdf_manual_import`, `pdf_review_supplement`, `pdf_reconciliation`,
`pdf_reconciliation_decision`, `pdf_host_event`, `pdf_review_politeness_session`,
`pdf_review_robots_request`, `pdf_review_robots_result`. Insert triggers check that the parent context exists and is still
open (existence predicates, so a missing run never passes as SQL NULL).

Ordering per download: reserve attempt (commit) -> reserve request (commit) -> network ->
request result (commit) -> validate bytes -> place in CAS with fsync and atomic rename ->
one transaction registering the artifact, the attempt result and the work link. An interruption
before the last commit leaves an attempt without result (`INTERRUPTED`, retried within the bound)
and possibly an unreferenced CAS object (`reprovane pdf orphans`), never a registered success
without verified bytes. Re-downloading identical bytes reuses the object.

Migrations 0001-0013 are byte-identical. Opening a schema-13 workspace with any `pdf` writer,
`workspace init` or `workspace upgrade` applies 0014 in one transaction; there is no downgrade.
Reprovane 2.0.x cannot open a schema-14 workspace (it reports migration drift), so a release
carrying this subsystem needs a new minor version.

## Manual ingestion and reconciliation

Files are found under a file or folder (optionally recursive), symlinks are never followed and
only `*.pdf` files are considered. Each file is size-bounded, read once, hashed, validated with
the shared strict validator and stored by content. Linking requires a user mapping row (`file`
or `sha256` plus `work_id`, `corpus_item_id`, `openalex_id`, `doi` or `legacy_key`) or an exact
identifier key as the file name (`match_keys` or `file_key`). A mapping row giving both `file`
and `sha256` applies only when both agree; one disagreeing selector, or several applicable rows
naming different targets, gives `MAPPING_CONFLICT`. A stem equal only to an unqualified 4.1.0 key
gives `KEY_NEEDS_CONFIRMATION`. Unmatched, ambiguous, contradictory or already-linked-elsewhere
files create `pdf_reconciliation` items; a decision is one immutable row. Re-running an import
with unchanged files records nothing new. A 4.1.0 legacy import always records its session with
the sanitized per-paper history from `fetch_state.json`, shown as `legacy_v4_history`.

## Review bridge

`reprovane pdf review` dispatches to the unchanged Stage11B CLI, except `review acquire`, which
runs `FullTextStore.acquire` through `PoliteReviewTransport`: the discovery politeness gate and
response inspection in front of every PDF request, with a fixed settings profile
(`pdf-review-politeness/1`) recorded per invocation. robots.txt requests are reserved in
`pdf_review_robots_request` before any network I/O and completed in `pdf_review_robots_result`,
outside the Stage11B ledger, which accepts only PDF and METADATA requests. They count with the
Stage11B requests against the run's frozen request and byte budget (also checked before every
PDF request), and `status --fulltext-run-id` verifies this auxiliary ledger. Refused requests
become ordinary Stage11B failed attempts with their reason; plans,
version rules and sealing are unchanged. Review-run manual PDFs become
`pdf_review_supplement` rows with verification `SUPPLEMENTARY_UNVERIFIED_NOT_PRIMARY`; the
Stage11B run, its primaries, its manifest and the corpus release are never modified.
`reprovane pdf status --fulltext-run-id` shows Stage11B statuses with supplements.

## Verification and exports

`PdfAcquisitionStore.verify` re-hashes every row, re-derives every input batch from its archived
source (or from the acquisition replay or sealed handoff), recomputes identity and plans, checks
the request ledger, attempt bounds, HTTP 200 provenance of every automatic artifact, the manual
import sessions, imports, links and reconciliation decisions, CAS bytes and PDF structure of every
artifact the run holds (including manual files waiting in reconciliation, listed in the manifest
as `held_manual_artifacts`), budgets, and for a closed run the semantic manifest. `workspace doctor` adds a
`pdf_acquisition` section (closed runs and PDF artifacts) kept apart from `sealed_integrity`.

Exports are rendered from `pdf_manifest.json` and verified offline by rebuilding every byte.
They contain no timestamps, machine paths or credentials. A closed run's export is authoritative;
an open run's export is a snapshot.

## Known limits

- pypdf parses untrusted PDFs in-process; size bounds apply but there is no CPU sandbox.
- Signed URLs are stored sanitized; without `--runtime-urls-from` such candidates end as
  `RUNTIME_URL_REQUIRED`.
- robots.txt is cached per invocation, not persisted; Crawl-delay pacing restarts per invocation.
- Unpaywall daily guidance across runs and workspaces is the operator's responsibility.
- Politeness for review runs applies only through `reprovane pdf review acquire`; the
  `reprovane fulltext acquire` entry point keeps the original Stage11B behaviour. A host on
  cooldown or paused still consumes a Stage11B attempt ordinal, because Stage11B records the
  attempt before the request.
