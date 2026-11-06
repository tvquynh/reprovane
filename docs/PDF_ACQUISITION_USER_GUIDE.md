# PDF acquisition user guide

Reprovane collects open-access (OA) PDFs in two ways that share one engine:

- **Discovery downloads** (`DISCOVERY_DOWNLOAD`): gather PDFs for any list of publications
  (an OpenAlex search, a citation-discovery run, a CSV/JSONL file, a DOI list) before or
  while you screen. The PDFs are acquisition evidence only. They never change screening or
  corpus membership.
- **Review full text** (`REVIEW_FULLTEXT`): the accepted, policy-frozen workflow for a
  SEALED corpus release (`reprovane pdf review ...`, identical to `reprovane fulltext ...`).

Everything below works offline except `discover --online`, `download` and `resume`, which
contact the hosts named in your inputs. Reprovane never logs in, never solves a CAPTCHA,
never bypasses a paywall and always honours `robots.txt`.

## 1. Before you start

Install Reprovane and create a workspace as described in the
[Quick Start](../QUICKSTART.md). The examples use `reprovane`; on a locked-down Windows
host use `python -m reprovane` instead. Quote paths that contain spaces. Paths with spaces
and non-ASCII characters are supported on Windows, macOS and Linux.

Optional credentials are read only at run time from the environment and are never stored:
`OPENALEX_API_KEY` (OpenAlex lookups and the OpenAlex cached-PDF service) and
`UNPAYWALL_EMAIL` (Unpaywall DOI lookups). See [Security](../SECURITY.md).

## 2. The five-step discovery workflow

```bash
reprovane pdf import --workspace "My Review" --input "results/query_main.csv" --label "pilot"
reprovane pdf discover --workspace "My Review" --run-id pdr_example
reprovane pdf download --workspace "My Review" --run-id pdr_example
reprovane pdf manual-import --workspace "My Review" --run-id pdr_example --input "manual downloads"
reprovane pdf export --workspace "My Review" --run-id pdr_example --directory "pdf report 1"
```

1. **Import** prints a new run ID (`pdr_...`). Save it. Add more inputs to the same run with
   `--run-id` before step 2.
2. **Discover** freezes the list of unique publications and, for each one, an ordered plan
   of candidate URLs. After this step the run accepts no new inputs.
3. **Download** fetches eligible OA PDFs one at a time, politely. It is safe to repeat.
4. **Manual import** adds PDFs you downloaded yourself, for example from the manual list.
5. **Export** writes reports to a new folder. Use a new folder name for each export of an
   unfinished run.

When the run is complete, freeze it with `close`; exports of a closed run are authoritative.

```bash
reprovane pdf close --workspace "My Review" --run-id pdr_example
reprovane pdf export --workspace "My Review" --run-id pdr_example --directory "pdf report final"
reprovane pdf verify-export --directory "pdf report final"
```

### Windows PowerShell

```powershell
$ws = "$HOME\Documents\My Review"
reprovane pdf import --workspace "$ws" --input "$HOME\Documents\results\query_main.csv"
reprovane pdf discover --workspace "$ws" --run-id pdr_example
reprovane pdf download --workspace "$ws" --run-id pdr_example
reprovane pdf manual-import --workspace "$ws" --run-id pdr_example --input "$HOME\Downloads\manual pdfs" --recursive
reprovane pdf status --workspace "$ws" --run-id pdr_example
```

### macOS and Linux

```bash
WS="$HOME/My Review"
reprovane pdf import --workspace "$WS" --input "$HOME/results/query_main.csv"
reprovane pdf discover --workspace "$WS" --run-id pdr_example
reprovane pdf download --workspace "$WS" --run-id pdr_example --limit 50
reprovane pdf status --workspace "$WS" --run-id pdr_example
```

## 3. Inputs

| Source | Command |
|---|---|
| OpenAlex Literature Intelligence 4.1.0 CSV/JSONL (`*_main.csv`, `*_raw.jsonl`, ...) | `import --input FILE` |
| Reprovane corpus or PDF exports, generic CSV/TSV/JSONL/JSON | `import --input FILE [--format ...]` |
| DOI or OpenAlex Work ID list | `import --identifier 10.1234/abc --identifier W123` or `import --input ids.txt` |
| A COMPLETE saved OpenAlex acquisition | `import --acquisition-id oaa_...` |
| All COMPLETE acquisitions of a citation-discovery run | `import --discovery-run-id dsc_...` |
| A SEALED corpus release | `import --corpus-release-id ...` |

Common column names are detected automatically (`openalex_id`/`id`, `doi`, `title`,
`publication_year`, `best_oa_pdf_url`, `oa_url`, `best_oa_landing_page`, `is_open_access`,
`oa_status`). The exact source bytes, their SHA-256 and the column mapping are kept.
Re-importing the same file into the same run does nothing. Reprovane's own exports can be
imported again; the earlier run's `work_id` is ignored. `pdf_items.jsonl` keeps OpenAlex IDs,
DOIs and every planned URL with its type, open-access flag and, for signed URLs, the identity
needed by `--runtime-urls-from`. `downloaded.csv` and `manual_download.csv` keep identifiers,
titles and one URL without an open-access flag. Version claims are not carried over: re-imported
URLs are `UNKNOWN` versions, as for any table input. A sanitized URL containing `REDACTED` is
never requested.

Publications are merged only on exact identifiers: the same OpenAlex Work ID, or the same
DOI. Titles are never used to merge, and rows without any identifier are never merged with
each other, even when their titles are equal. Disagreements (for example one DOI listed
under two OpenAlex IDs, or two rows sharing a 4.1.0 file key) are reported under
`identity.issues` in `status` and `inspect`.

Columns such as `main_corpus` or `main_exclusion_reasons` in a 4.1.0 `*_main.csv` or
`*_excluded.csv` are kept as historical machine classifications. They are never turned into
reviewer screening decisions.

## 4. Policies and versions

```bash
reprovane pdf policy --profile discovery
reprovane pdf policy --profile review
```

A run freezes its policy (with a SHA-256) when it is created. To change any rule, save the
printed `policy` object to a file, edit it and create a new run with `--policy FILE`. Pass
`--expected-policy-sha256` to `download` or `resume` to refuse a run whose policy differs.

The discovery profile accepts publishedVersion, acceptedVersion, submittedVersion and
UNKNOWN. Every PDF keeps its version claim and a verification label such as
`VERSION_UNKNOWN_UNVERIFIED`; nothing is promoted to a verified version. The review profile
keeps the Stage11B rules: published and accepted versions only.

Provider lookups (OpenAlex Work, Unpaywall DOI, OpenAlex cached PDF) are off by default.
Enable them by adding `OPENALEX`, `UNPAYWALL` or `OPENALEX_CONTENT` to
`evidence_sources_enabled` in a policy file, then run `discover --online`. OpenAlex is
looked up by Work ID, or by exact DOI for a publication known only by DOI; a response for
a different DOI is refused (`PROVIDER_ID_MISMATCH`). OpenAlex works without an API key;
Unpaywall needs `UNPAYWALL_EMAIL`. A Work ID found through a DOI lookup is recorded as
evidence and never merges publications after import.

## 5. Downloading, interruptions and resume

`download` processes publications in input order. For each one it tries the eligible
candidates in frozen rank order and stops at the first valid PDF. It reads `robots.txt`
for every host, waits at least 2 seconds between requests to the same host (longer if the
host asks with `Crawl-delay`), retries temporary errors a bounded number of times and honours
`Retry-After` in seconds or as a date, also on `robots.txt` and provider lookups. Every
`Retry-After` is recorded, so a later `resume` waits for what is left of it; one longer than
`max_cooldown_seconds` blocks the host until you unblock it. A `Crawl-delay` longer than `max_crawl_delay_seconds` (60 by default) is never
shortened: that host is skipped for the current command (`CRAWL_DELAY_TOO_LONG`) and its
publications wait for a later `resume` or the manual list.

A host that answers with a bot check, a CAPTCHA page, HTTP 403 or a text-and-data-mining
reservation is blocked for all future runs. Its publications go to the manual list.

```bash
reprovane pdf hosts --workspace "My Review"
reprovane pdf hosts --workspace "My Review" --unblock example-publisher.org --reason "permission granted"
```

Press Ctrl+C at any time. Completed work is saved. Continue with:

```bash
reprovane pdf resume --workspace "My Review" --run-id pdr_example
```

`resume` finishes discovery if needed and continues downloads. Temporary failures are
retried; permanent ones (for example HTTP 404) only with `--retry-failed`. No candidate is
tried more often than the policy allows, and a downloaded PDF is never fetched again.
Downloads stop when the frozen budget for requests, bytes, publications or distinct PDFs is
used up.

If a provider lookup fails temporarily during `discover --online` (network error, timeout,
HTTP 429 or 5xx), that publication is not planned yet and the run stays in discovery; run
`discover --online` or `resume --online` again later. A provider that asks to wait longer than
`max_cooldown_seconds` is blocked like any other host (`pdf hosts` lists it; unblock it with
`pdf hosts --unblock api.openalex.org` when appropriate). While a lookup is pending nothing in that
run is downloaded. To plan without the missing lookups, record the failure as evidence:

```bash
reprovane pdf discover --workspace "My Review" --run-id pdr_example --online --accept-failed-lookups
```

Signed download links (for example with a `token` or `signature` parameter) are stored only in
sanitized form. To use them, give your original input file again; the exact links stay in memory
for that command:

```bash
reprovane pdf download --workspace "My Review" --run-id pdr_example --runtime-urls-from "results/query_main.csv"
```

## 6. Manual PDFs

Use the manual list (`manual_download.html`, `.csv` or `.ris` in an export). Save each PDF
under the suggested file name, such as `W2741809807.pdf` or `doi_10.1234_abc.pdf`, then:

```bash
reprovane pdf manual-import --workspace "My Review" --run-id pdr_example --input "manual pdfs" --recursive
```

A file is linked only when its name is exactly the suggested file name: an OpenAlex Work
ID, a `doi_...` key, the opaque `row_...` key 4.1.0 gave a row without identifiers, a
work ID or (for review runs) a corpus item ID. A 4.1.0 key copied from an invalid identifier
column (for example a title typed into `openalex_id`) is never trusted on its own: such a file
waits for confirmation (`KEY_NEEDS_CONFIRMATION`). You can also give a mapping file:

```text
file,openalex_id,doi
paper from library.pdf,W2741809807,10.1234/abc
```

```bash
reprovane pdf manual-import --workspace "My Review" --run-id pdr_example --input "manual pdfs" --mapping "mapping.csv"
```

A mapping row with both `file` and `sha256` applies only when both agree with the file. When
one agrees and the other does not, or when several rows for the same file name different
publications, the file waits for a decision (`MAPPING_CONFLICT`).

Every file is checked as a real PDF and stored once by its SHA-256. Running the same import
again changes nothing. Files that match nothing, match several publications, contradict
each other or have bytes already linked to another publication wait for your decision:

```bash
reprovane pdf reconcile --workspace "My Review" --run-id pdr_example
reprovane pdf reconcile --workspace "My Review" --run-id pdr_example --item-id prc_example --target-id pw_example --decided-by reviewer_1
reprovane pdf reconcile --workspace "My Review" --run-id pdr_example --item-id prc_example --reject
```

Decisions are permanent records. Use a role label or initials, not an email address.

## 7. Bringing in a 4.1.0 `fetch-oa` folder

Import the same `*_main.csv` you gave to 4.1.0, run `discover`, then point Reprovane at the
old output folder (the one containing `fetch_state.json`, `pdf/` and `pdf_manual/`):

```bash
reprovane pdf legacy-import --workspace "My Review" --run-id pdr_example --source "query_main_fulltext"
```

The old folder is only read. PDFs whose bytes still match `fetch_state.json` are recorded
as `LEGACY_V4_DOWNLOADED`; files in `pdf_manual/` become manual imports; hosts in
`blocked_hosts.json` stay blocked (add `--no-blocked-hosts` to skip that). Every 4.1.0
outcome, including failures such as `HTTP_404`, is kept as history: `status --items` shows
it under `legacy_v4_history` and the manual list mentions it. History is never counted as
a new attempt.

## 8. Review runs (sealed corpus)

```bash
reprovane pdf review create --workspace "My Review" --release-id cr_example --policy "review_policy.json"
reprovane pdf review discover --workspace "My Review" --run-id ftr_example
reprovane pdf review acquire --workspace "My Review" --run-id ftr_example --expected-policy-sha256 POLICY_SHA256_FROM_CREATE
reprovane pdf review finalize --workspace "My Review" --run-id ftr_example
reprovane pdf manual-import --workspace "My Review" --fulltext-run-id ftr_example --input "manual pdfs"
reprovane pdf status --workspace "My Review" --fulltext-run-id ftr_example
```

`review acquire` runs the Stage11B acquisition with the same politeness rules as discovery
downloads: robots.txt, per-host pacing and Crawl-delay, Retry-After cooldowns, bot-check
detection and the workspace's blocked hosts. A refused request is recorded as an ordinary
failed attempt with its reason (for example `ROBOTS_DISALLOWED` or `HOST_BLOCKED`); the
version rules, plans and sealing are unchanged. robots.txt requests count against the run's
frozen request and byte budget; `status --fulltext-run-id` shows and verifies them under
`politeness`. `reprovane fulltext acquire` keeps the original Stage11B behaviour without these
checks.

For review runs a manual PDF is recorded as `SUPPLEMENTARY_UNVERIFIED_NOT_PRIMARY`. The
sealed run, its primary PDFs, its manifest and the corpus membership never change. `status`
shows such items as `MANUAL_SUPPLIED_UNVERIFIED` next to the unchanged Stage11B status.

## 9. Reports, verification and maintenance

| File in an export | Content |
|---|---|
| `downloaded.csv` | Publications with a PDF: source (AUTO, MANUAL, LEGACY_V4), SHA-256, size, storage locator, version claim |
| `manual_download.csv` / `.html` / `.ris` | Publications without a PDF, reason, suggested file name and links; title, DOI and year come from the input, or from provider lookups when the input has none |
| `reconciliation.csv` | Manual files waiting for, or with, a decision |
| `fetch_summary.json` | Counts: inputs, accepted/rejected, unique works, candidate URLs, downloads, manual imports, failures by reason, by source and by host |
| `pdf_manifest.json`, `pdf_items.jsonl` | Complete semantic record from which every other file is rebuilt |
| `SHA256SUMS.txt` | Checksums of all other files |

```bash
reprovane pdf verify --workspace "My Review" --run-id pdr_example
reprovane pdf export-pdfs --workspace "My Review" --run-id pdr_example --directory "pdf copies"
reprovane pdf orphans --workspace "My Review"
reprovane pdf orphans --workspace "My Review" --quarantine "cas quarantine"
```

`verify` re-derives inputs, identities and plans from stored evidence and re-checks every
PDF the run holds, including manual files still waiting in reconciliation, together with
the manual import and reconciliation records. `orphans` lists stored objects that no record
uses (for example after a crash); the quarantine option moves them out of the store and
never deletes anything.

Status values you will meet: `DOWNLOADED`, `MANUAL_IMPORTED`, `LEGACY_V4_DOWNLOADED`,
`NOT_OPEN_ACCESS`, `NO_OA_URL`, `UNSAFE_OR_INVALID_URL`, `BOT_CHECK`, `HOST_BLOCKED`,
`ROBOTS_DISALLOWED`, `ROBOTS_UNREACHABLE`, `CRAWL_DELAY_TOO_LONG`, `RATE_LIMITED`,
`HOST_COOLDOWN`, `BUDGET_EXHAUSTED`, `HTTP_404`,
`HTTP_5XX`, `NETWORK_ERROR`, `NOT_PDF`, `PDF_VALIDATION_FAILED`, `TOO_LARGE`, `TRUNCATED`,
`UNSAFE_URL`, `RUNTIME_URL_REQUIRED`, `CREDENTIAL_REQUIRED`, `NOT_ATTEMPTED`.

## 10. Good practice

- A PDF being available does not make a study eligible. Screening stays a human decision.
- Successful download is not permission to redistribute; see [Third-party data](../THIRD_PARTY_DATA.md).
- Back up the whole workspace with all writers stopped, as described in [Operations](../OPERATIONS.md).
- Exit codes: 0 success, 2 input or configuration problem, 3 integrity failure, 4 operational
  failure, 130 interrupted (safe to resume).
