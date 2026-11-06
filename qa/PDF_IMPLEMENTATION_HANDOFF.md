# Unified PDF acquisition: implementation handoff for independent review

## 1. Branch and commits

- Repository: `tvquynh/reprovane` (local checkout), feature branch `feat/unified-pdf-acquisition`.
- Base: `main` at `6c4793c` (Merge PR #6, Reprovane 2.0.1 documentation update), clean before work.
- `13083e6`: implementation. `67c15d2`: first handoff. `8ffc0a3`: fixes from the first independent review
  and the first real-network run (sections 9 and 10). `d9a9fbf`: fixes from the second review. `155c940`:
  fixes from the third review. `db50a35`: fixes from the fourth review. `f7badb4`: fixes from the fifth
  review. `2ec1f25`: the confirmation round (section 9) and pending lookups for blocked provider hosts. The
  commit after it changes only this handoff. Review the branch tip.
- On 2026-11-06 the feature branch was pushed to `origin` at the owner's request, so that CI could run on it
  (section 6). Nothing was merged, tagged, released or published.

## 2. Files changed

New:

- `reprovane/core/migrations/0014_pdf_acquisition.sql`
- `reprovane/pdf_acquisition/` (`__init__`, `__main__`, `policy`, `identifiers`, `inputs`, `identity`,
  `candidates`, `politeness`, `engine`, `store`, `manual`, `legacy`, `report`, `maintenance`, `review`,
  `pdfcheck`)
- `tests/test_pdf_acquisition.py`, `tests/pdf_acquisition_fixtures.py`, `tests/test_pdf_legacy_parity.py`,
  `tests/fixtures/bootstrap/migration_hashes_schema14.json`
- `tools/pdf_legacy_parity.py`, `tools/pdf_offline_demo.py`
- `docs/PDF_ACQUISITION_USER_GUIDE.md`, `docs/PDF_ACQUISITION_ARCHITECTURE.md`, `docs/PDF_LEGACY_PARITY_MATRIX.md`,
  this file

Modified:

- `reprovane/fulltext_acquisition/transport.py`: optional `gate`/`inspect` hooks (the prefix read obeys the
  elapsed-time bound), `user_agent`, `extra_headers`, `FetchError.terminal`. Without hooks the Stage11B
  behaviour is unchanged.
- `reprovane/__main__.py`: `pdf` route.
- `reprovane/operations/workspace.py`: `workspace init`/`upgrade` apply schema 14; `doctor` adds a
  separate `pdf_acquisition` section (not inside `sealed_integrity`, so the accepted e2e fixtures are untouched).
- Tests updated for schema 14 (see section 6): `tests/test_rebrand.py`, `tests/test_reprovane_bootstrap.py`,
  `tests/test_stage12_release_hardening.py`.
- Release gates for 14 migrations: `tools/validate_ci_package.py`, `tools/validate_release.py`.
- Packaging of the new guides: `pyproject.toml`, `MANIFEST.in`.
- Documentation: `ARCHITECTURE.md`, `CHANGELOG.md` (Unreleased), the 1.x migration guide,
  `OPERATIONS.md`, `QUICKSTART.md`, `SECURITY.md`, `USER_GUIDE.md`, `VERSION`,
  `docs/architecture/fulltext_acquisition.md`.

Not changed: migrations 0001-0013 (byte-identical, verified by test), Stage11B store/policy/candidates/CLI,
`reprovane/fulltext.py` (legacy algorithm), corpus/screening/discovery code, the product version (2.0.1).

## 3. Database migration 0014

`0014_pdf_acquisition.sql`, SHA-256 `1a6ae9f4aa03a22eedb4880be0fa4fa3fa4bff31db4434cd26b32d1da6f3220b`
(pinned in `tests/fixtures/bootstrap/migration_hashes_schema14.json`). Twenty-two append-only tables
(`pdf_run`, inputs, works, evidence, plans, candidates, request ledger, attempts, artifacts, links, manual
import sessions/files, review supplements, reconciliation items/decisions, host events, review politeness
sessions and their robots.txt request reservations and results), indexes and triggers: inserts need the store's connection-scoped
`pdf_acquisition_authorized()` function and an existing, still-open parent context (existence predicates, so
a missing run never passes as SQL NULL); updates and deletes of evidence are rejected; `pdf_run` allows only
OPEN -> DISCOVERING -> ACQUIRING -> CLOSED. Forward-only, applied in one transaction; recovery is the
existing backup-first `workspace upgrade` (copy-on-write). There is no downgrade. 2.0.x refuses a schema-14
workspace with a migration-drift error, so the next release must be a minor version; the version bump was
left to release preparation. Migration 0014 itself has never been released, so its content was revised in
place during review; a workspace created with an earlier revision of it is a test workspace only.

## 4. Commands

`reprovane pdf policy | runs | import | discover | download | resume | manual-import | legacy-import |
reconcile | status | inspect | export | export-pdfs | verify | verify-export | close | hosts | orphans |
review ...`. `review` forwards to the unchanged Stage11B `fulltext` CLI, except `review acquire`, which adds
robots.txt, pacing, Retry-After cooldowns and blocked hosts in front of every PDF request. `discover
--accept-failed-lookups` plans works whose provider lookups failed temporarily; `download/resume
--runtime-urls-from FILE` re-reads signed URLs from the user's own input file, in memory only. Writers take
the workspace OS lock; read commands use the existing read-only database snapshot. Exit codes 0/2/3/4 and
130 on Ctrl+C.

## 5. Implemented requirements

| Requirement | Implementation |
|---|---|
| FR-01 inputs | `inputs.py`: 4.1.0 flat CSV/JSONL, OpenAlex Work JSON(L), Reprovane corpus exports and its own PDF exports (identity and candidate URLs kept), generic tables, identifier lists, COMPLETE acquisitions, discovery runs, SEALED releases; exact source bytes archived in CAS (unless secret-like), SHA-256 and field mapping kept; exact-identifier identity with conflict issues; rows without identifiers are never merged |
| FR-02 multi-source | `INPUT_ROW`, `OPENALEX_RECORD`, `FROZEN`, optional `OPENALEX` (by Work ID, or exact DOI for DOI-only works), `UNPAYWALL`, `OPENALEX_CONTENT`; URL identity dedup keeps every route; explicit version policy incl. UNKNOWN |
| FR-03 policies | `DISCOVERY_DOWNLOAD` (`pdf-discovery-policy/1`, canonical SHA-256, frozen per run, `--expected-policy-sha256`); `REVIEW_FULLTEXT` = Stage11B policy, unchanged |
| FR-04 engine | Shared `SafeTransport` + `politeness.py` for both profiles (review through `review acquire`): streaming size bounds and elapsed-time bound, timeouts, bounded retries, 429/5xx/Retry-After in seconds or HTTP-date, per-host pacing and Crawl-delay (never shortened), robots.txt, persistent blocks and cooldowns, bot-check/CAPTCHA/HTML detection, per-hop redirect checks, SSRF guard, budgets incl. artifacts, append-only ledger, per-candidate outcomes, resume (also of failed lookups), idempotent retries |
| FR-05 manual (P0) | `manual.py`: file, folder, recursive folder, 4.1.0 `pdf_manual`; structure check, SHA-256, content dedup, identity-qualified file keys or user mapping (contradictions go to reconciliation), MANUAL provenance, CAS, status, reconciliation queue; review runs get supplementary links only |
| FR-06 persistence | Migration 0014, single-transaction commits, CAS placement before registration, interrupted attempts resumable, orphan scan/quarantine, no success before validation; `verify` covers manual sessions, reconciliation and held manual PDFs |
| FR-07 CLI/reports | `reprovane pdf` family; `downloaded.csv`, `manual_download.csv/.html/.ris`, `fetch_summary.json`, `reconciliation.csv`, manifest, JSONL, checksums; counts by reason, evidence source and host |
| FR-08 compatibility | `legacy-import` of `fetch-oa` folders (read-only, every per-paper outcome kept as history), historical classification kept apart, existing CLIs/APIs unchanged |

## 6. Tests and how to rerun

```bash
python tools/run_offline_tests.py
python -m unittest discover -s tests -p "test_pdf_*.py"
python tools/pdf_legacy_parity.py
python tools/pdf_legacy_parity.py --legacy-root ../openalex-literature-intelligence
python tools/audit_documentation.py
```

Results on Windows 11: baseline before changes 1489 tests OK; final run 1636 tests OK
(`tools/run_offline_tests.py`, no skips) on Python 3.11.9, and the same suite also passes on Python 3.14.4 with
the built wheel installed. New: 145 tests in `test_pdf_acquisition.py` and `test_pdf_legacy_parity.py`, plus 2
generated help tests for the `pdf` route. Linux and Python 3.13 run in CI only: the workflow
"Reprovane checks" on `2ec1f25` passed all five jobs (`verify` on ubuntu-latest and windows-latest with
Python 3.11 and 3.13, and `package`).

Assertions changed on purpose because the schema moved from 13 to 14 (none weakened):

- `test_no_schema14` (two files) became `test_schema14_is_the_only_addition`: exactly
  `0014_pdf_acquisition.sql` exists and no `0015*`.
- Migration-hash tests now check that the 13 accepted hashes are unchanged **and** that the full set equals
  the pinned 14-hash fixture.
- `initialize`/`doctor`/`upgrade` expectations `13` -> `14` (init and upgrade apply all shipped migrations).
- Release gates expect 14 pinned migrations.

Packaging: the CI package job was reproduced locally with network access: `python -m build` in an isolated
environment (pinned `setuptools<81` from PyPI) from a clean copy, then `tools/validate_ci_package.py`, which
installs the wheel and the sdist into fresh virtual environments and runs `--version`, `--help` and
`doctor --offline` from each: PACKAGE VALIDATION PASS.

## 7. Legacy parity

`tools/pdf_legacy_parity.py` on one synthetic in-memory web, run with both the in-repository reference and
the real 4.1.0 checkout (read-only): 16 papers, 13 equivalent, 3 intentional policy differences (strict PDF
validation, SSRF guard, non-default port refused), 0 regressions; resume after an interrupted pass equals a
single pass in both tools; common PDFs byte-identical; the 4.1.0 output folder imports read-only and
verifies. Further intentional differences without a fixture row (title-only duplicates, title-like manual
file names, Crawl-delay above 60 s) are listed in the [parity matrix](../docs/PDF_LEGACY_PARITY_MATRIX.md).
These are synthetic results, not real-world success rates.

## 8. Known limits

- Sequential downloads only; no bounded concurrency.
- pypdf parses untrusted PDFs in-process (size-bounded, no CPU sandbox).
- robots.txt and pacing state are per invocation; blocks and cooldowns persist.
- Politeness for review runs applies through `reprovane pdf review acquire` only; `reprovane fulltext acquire`
  keeps the original Stage11B behaviour. A host on cooldown still uses a Stage11B attempt ordinal. Each
  `review acquire` invocation records a politeness session, also when Stage11B then does nothing (sealed
  run) or refuses (policy mismatch).
- A run with a pending provider lookup downloads nothing until the lookup succeeds or is accepted as failed.
- Identity is frozen before provider lookups: a DOI-only record whose OpenAlex lookup returns a Work ID that
  another record already carries stays a separate work. Both may download the same bytes, stored once.
- Sensitive query values shorter than four characters are not used by the substring guard; retained headers
  and block reasons carry no remote text, so they have no other persisted path.
- Re-imported exports carry URLs as `UNKNOWN` versions; the CSV views carry no open-access flag.
- Inputs whose raw bytes look secret-bearing are not archived; their records can be hash-checked but not
  re-derived by `verify`.
- A publication on a host paused only for the current invocation shows `NOT_ATTEMPTED`; the download summary
  lists the paused hosts.
- The real-network run in section 10 is a small smoke test, not a success-rate study.

## 9. Review focus and independent reviews

### Suggested focus for further review

The first handoff (`67c15d2`) listed the areas below; they are kept here, updated to the branch tip, because
a defect in any of them would cost most.

1. `SafeTransport` hooks: Stage11B behaviour without hooks must stay unchanged; with `inspect`, check prefix
   byte accounting, per-hop sensitive values and `DNS_UNAVAILABLE` (reported only when hooked).
2. Secrets on persisted paths: the withheld `Retry-After` (`REDACTED`, host blocked), the fixed media
   vocabulary of retained headers, fixed host-event reasons, and the four-character minimum of the substring
   guard (section 8).
3. Crash windows: CAS placement against the transaction in `Downloader.attempt` and `manual.ingest`; resume
   bounds, pending lookups and `--accept-failed-lookups`.
4. Identity: `identity.freeze` before lookups (section 8), `legacy_key_trusted`, `manual._match`, and the
   `MAPPING_CONFLICT` and `KEY_NEEDS_CONFIRMATION` reconciliation kinds: no wrong link.
5. Migration 0014 triggers: the authorization function, run-state guards, the byte checks (JSON integer,
   equal to the hashed result, within the reservation) and REPLACE/DELETE refusal.
6. `pdf review acquire`: the combined frozen budget, the robots.txt reservation and result ledger, and
   `verify_review_politeness`.
7. Hostile PDFs: pypdf runs in-process with size bounds only (section 8).
8. Existing workspaces: schema 14 cannot be opened by 2.0.x, so the next version needs a new minor number.

### Round 1

An independent reviewer (separate agent and model, offline worktree of `67c15d2`, original task text as the
acceptance standard) reported 12 findings: 7 P1 and 5 P2, none P0. Its sandbox could not start Python, so
the reviewer's Python reproducers were executed afterwards against `67c15d2`: all 11 reproduced, and its
SQLite probe had confirmed F-12. After the fixes none reproduces. Each finding has a regression test in
`TestIndependentReviewFindings` unless stated otherwise.

| ID | Finding | Fix |
|---|---|---|
| F-01 | A bot-check page title reflecting a runtime secret was stored in a host event | Host-event reasons are checked against runtime secrets and private-looking text; such a reason becomes `REASON_WITHHELD_PRIVATE_LOOKING_TEXT` |
| F-02 | The artifact budget was checked only afterwards | Checked before each work and again before storing a new distinct PDF (`BUDGET_EXHAUSTED`, nothing stored) |
| F-03 | A title typed into the OpenAlex column could link a manual file by name | Only identity-qualified keys match automatically; otherwise `KEY_NEEDS_CONFIRMATION`; suggested file name falls back to the work ID |
| F-04 | Rows without identifiers merged on title-derived keys | Never merged; shared keys reported as `LEGACY_KEY_COLLISION`; a file with a shared key is `AMBIGUOUS` |
| F-05 | Mapping rows with a wrong SHA or conflicting targets were resolved silently | Both selectors must agree; conflicts give `MAPPING_CONFLICT` |
| F-06 | Corrupt PDFs held in reconciliation passed `verify` and `close` | `verify` checks manual sessions, imports, links, decisions and every held PDF; manifest lists `held_manual_artifacts` |
| F-07 | The 64-KiB prefix read ignored the elapsed-time bound | Same bound checked after every prefix chunk |
| F-08 | Identical bytes from two candidates of one work failed verification | Verification accepts the shared link (many attempts, one link) |
| F-09 | HTTP-date `Retry-After` was not waited for; Crawl-delay above 60 s was shortened | Short Retry-After sets a not-before time; long Crawl-delay defers the host (`CRAWL_DELAY_TOO_LONG`) |
| F-10 | Re-importing the subsystem's own JSONL lost the OpenAlex ID and URLs | Dedicated export adapter for `pdf_items.jsonl`, `downloaded.csv`, `manual_download.csv` |
| F-11 | Legacy 4.1.0 failures were discarded | Sanitized per-paper history kept in the import session and shown as `legacy_v4_history` |
| F-12 | Manual rows could be added to a closed or missing run (SQL NULL in `NOT IN`) | Existence predicates in every context trigger; regression test with controls |

The reviewer also listed three limitations: review acquisition without robots.txt (`review acquire`
politeness, tests in `TestReviewBridge`); failed provider lookups frozen as evidence (temporary failures stay
pending, `--accept-failed-lookups` to proceed); signed URLs unusable from the CLI (`--runtime-urls-from`).

### Round 2

The same reviewer re-reviewed `8ffc0a3` and confirmed F-02, F-04, F-05, F-08 and F-12 as fixed. It rated the
others and the three limitations PARTIAL and reported ten findings: 4 P1, 6 P2, none P0. Its Python
reproducers were executed against `8ffc0a3`: all ten reproduced, two controls passed. After the fixes none
reproduces; regression tests are in `TestIndependentReviewRound2` (R2-04 in `TestReviewBridge`).

| ID | Finding | Fix |
|---|---|---|
| R2-01 | A signed-URL token reflected in a bot-check title was stored and exported | Stored reasons never quote remote pages (titles become their known challenge phrase); query values of runtime URLs join the persistence guards |
| R2-02 | Legacy history could keep a known runtime secret | Every copied history field is checked against runtime secrets and private-looking text |
| R2-03 | Key shape (`row_...`, `doi_...`) was taken as identifier provenance | A key is trusted only when recomputed from validated identifiers (`legacy_key_trusted`) |
| R2-04 | Review robots.txt requests were outside the frozen budget and not reserved before I/O | Durable reservation and result tables; combined budget checked before robots and PDF requests; ledger verified in `status --fulltext-run-id` |
| R2-05 | Two decisions for identical bytes failed verification | Manual imports and decisions are verified against work/content associations |
| R2-06 | robots.txt `Retry-After` ignored; long `Retry-After` shortened to the cooldown cap | robots 429/5xx use the same rules; the full delay is kept, and a delay beyond `max_cooldown_seconds` blocks the host |
| R2-07 | Re-import lost signed-URL identity and OA flags | `pdf_items.jsonl` carries every planned URL with identity, type, runtime flag and OA flag; `REDACTED` URLs are never requested |
| R2-08 | Runtime URLs nested in OpenAlex Work JSON were not found | Nested values are searched |
| R2-09 | Bytes read before a prefix timeout were not charged | Every consumed byte is charged |
| R2-10 | A temporary DNS failure was frozen as `UNSAFE_URL` | With hooks, an unresolved name is a temporary `NETWORK_ERROR`; a private address stays a permanent `UNSAFE_URL` |

### Round 3

The reviewer re-reviewed `d9a9fbf`, rated R2-05, R2-08, R2-09 and R2-10 FIXED and the rest PARTIAL, and
reported ten findings: 3 P1, 7 P2, none P0. Executed against `d9a9fbf`, nine reproduced; R3-09 did not
reproduce in its scenario but the stricter rule was applied anyway. After the fixes none reproduces (the
R3-05 probe passes no synthetic clock to the lookup pacer; the regression test does). Regression tests are
in `TestIndependentReviewRound3`.

| ID | Finding | Fix |
|---|---|---|
| R3-01 | Redirect or short tokens reflected in response headers reached the request ledger | Retained headers keep only their shape |
| R3-02 | Legacy status and blocked-host URL bypassed the private-value guard | Both guarded; a private-looking status becomes `UNRECOGNIZED`, such a URL is not stored |
| R3-03 | A fetch-state entry without a checksum still used an untrusted historical key | Fetch-state authority requires a recorded SHA-256 that matches; otherwise identifiers or reconciliation |
| R3-04 | Every long query value was treated as private and refused ordinary URLs | Only values of sensitive parameters (as redacted by `sanitize_url`) |
| R3-05 | Provider lookups shortened `Retry-After` | Lookups wait up to `max_retry_delay`, otherwise stay pending |
| R3-06 | A short `Retry-After` was forgotten by the next command | Every `Retry-After` is recorded as a cooldown; a short remainder is waited for |
| R3-07 | Earlier `pdf_items.jsonl` rows lost their URLs | Fallback to the earlier `candidates` field |
| R3-08 | URL-only export rows were rejected on re-import | Classified after their URLs are restored |
| R3-09 | The OA flag of a landing page could mark a direct PDF open | OA flag taken only from non-landing routes |
| R3-10 | Charged bytes were not bound to the hashed result's bytes | Trigger and verifiers require equal, integer bytes within the reservation |

### Round 4

The reviewer re-reviewed `155c940`, rated R3-02, R3-03, R3-04, R3-07, R3-08 and R3-09 FIXED, and reported five
findings: 1 P1, 3 P2, 1 P3, none P0. All five reproduced against `155c940`; after the fixes none does (the
R4-02 probe stops with `StopIteration` because the second lookup is no longer sent early). Regression tests
are in `TestIndependentReviewRound4`.

| ID | Finding | Fix |
|---|---|---|
| R4-01 | A token could survive in a valid media subtype or a numeric header | Fixed media vocabulary; lengths and `Retry-After` redacted when they repeat a sensitive value of any hop |
| R4-02 | Provider deadlines did not govern later works or commands | Provider `Retry-After` recorded as a host cooldown and enforced before every lookup |
| R4-03 | robots.txt redirect hops skipped the responder's cooldown and attributed directives to the origin | Every robots hop checks its own host; directives go to the responder |
| R4-04 | A direct-PDF route was dropped when the same URL was also a landing page | Re-import keeps each (URL, role) pair |
| R4-05 | JSON booleans and reals passed the integer byte checks | Triggers require `json_type` integer; verifiers require `type(x) is int` |

### Round 5 and overall assessment

The reviewer re-reviewed `db50a35`, rated R4-01, R4-03, R4-04, R4-05, R3-01, R3-06 and R3-10 FIXED, and reported
two findings: 1 P1, 1 P2. Both reproduced against `db50a35`; after the fixes neither does. Regression tests are
in `TestIndependentReviewRound5`.

| ID | Finding | Fix |
|---|---|---|
| R5-01 | A numeric signed value reflected in `Retry-After` reached host-event reasons and exports | Fixed reason text; a `Retry-After` repeating a sensitive value of any hop is withheld and blocks the host |
| R5-02 | An absurd provider `Retry-After` overflowed date arithmetic and aborted discovery | Bound checked first; beyond `max_cooldown_seconds` the provider host is blocked and the lookup stays pending |

The reviewer's overall assessment of `6c4793c..db50a35` against the original specification: FR-01, FR-02,
FR-03, FR-05 (P0), FR-06 and FR-08 MET; FR-04 and FR-07 PARTIAL only because of R5-01 and R5-02, which this
commit fixes. Across five rounds the reviewer reported 39 findings (none P0). Thirty-eight reproduced with their
own probes before the fix (R3-09 did not, and was hardened anyway); none reproduces after the fixes.

### Round 6: confirmation

On `f7badb4` the reviewer rated R5-01 and R5-02 FIXED, FR-01 to FR-08 MET, reported no open finding (P0 to P3
all zero) and gave the final verdict ACCEPT WITH NOTED LIMITS. The noted limits are those of section 8, plus
one residual case, changed afterwards: a later lookup refused because the provider host is blocked was frozen
as failed evidence; it now stays pending until the host is unblocked or the failure is accepted (the R5-02
regression test covers the unblock path). The reviewer could not execute Python in its sandbox; every Python
result above was produced by the implementer running the reviewer's probes and the suites.

## 10. Real-network smoke test and offline demo

A run against the public network in a throw-away workspace used 13 identifiers: nine for the first run
(seven OpenAlex Work IDs and two DOIs) and four DOIs for the second (OpenAlex lookups enabled, no API key,
default politeness). Results: valid PDFs from PLOS ONE, PLOS
Computational Biology, Nature, arXiv, JOSS (twice) and Genome Biology; MDPI answered with an access-denied
page and was blocked and listed for manual download as designed; a closed Elsevier article was
`NOT_OPEN_ACCESS`; a nonexistent DOI had no URL; a PeerJ record without a direct PDF link was
`NO_DIRECT_PDF`. `verify`, `close`, `export`, `verify-export` and `doctor --full` passed. The run exposed
three gaps that were fixed with tests: DOI-only works were not looked up in OpenAlex, manual lists lacked
titles for identifier-only input, and pypdf repair warnings reached the terminal. The same 13 identifiers
(11 unique works) were run again with the round-2 code in one fresh workspace: 7 PDFs, the MDPI block stored
as `bot-check page 'access denied' (HTTP 403)` without page text, manual list with titles, DOIs and years;
close, export and verify-export passed.

Offline demo (no network, credentials or real PDFs):

```bash
python tools/pdf_offline_demo.py --directory "demo output folder"
```

It creates a synthetic CSV, a manual-PDF folder and a workspace in a new folder, then runs import, discover,
download (same engine, in-memory web), manual import, reconciliation listing, status, verify, close, export,
verify-export, export-pdfs, hosts and a full doctor, printing one JSON summary. Command-line equivalents for a
real run are in the [user guide](../docs/PDF_ACQUISITION_USER_GUIDE.md).
