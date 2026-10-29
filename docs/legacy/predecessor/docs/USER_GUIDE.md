# OpenAlex Literature Intelligence v4.1.0: Comprehensive User Guide

Collect, enrich, screen, and audit scientific literature with OpenAlex and historical SCImago data (2010-2025): SJR, quartiles, source status, estimated APC, and a reproducible audit trail.

Release 4.1.0, 28 October 2026. This Markdown file is generated from [OpenAlex_Literature_Intelligence_User_Guide.docx](OpenAlex_Literature_Intelligence_User_Guide.docx); both have the same content.

## Contents

- [1. What the tool is for: purpose and value](#1-what-the-tool-is-for-purpose-and-value)
- [2. Suitable and unsuitable research use cases](#2-suitable-and-unsuitable-research-use-cases)
- [3. Architecture and end-to-end data flow](#3-architecture-and-end-to-end-data-flow)
- [4. Core concepts](#4-core-concepts)
- [5. System requirements and preparation](#5-system-requirements-and-preparation)
- [6. Detailed installation on Windows 11](#6-detailed-installation-on-windows-11)
- [7. Quick installation on Linux/macOS](#7-quick-installation-on-linuxmacos)
- [8. config.json and configuration precedence](#8-configjson-and-configuration-precedence)
- [9. Initialize and validate the SCImago repository](#9-initialize-and-validate-the-scimago-repository)
- [10. run: search, download, and build a corpus](#10-run-search-download-and-build-a-corpus)
- [11. Complete filtering and screening scenarios](#11-complete-filtering-and-screening-scenarios)
- [12. enrich: enrich an existing corpus](#12-enrich-enrich-an-existing-corpus)
- [13. inspect-source: inspect journal/source history](#13-inspect-source-inspect-journalsource-history)
- [14. Offline mode, cache, refresh, and resume](#14-offline-mode-cache-refresh-and-resume)
- [15. Understanding every output file](#15-understanding-every-output-file)
- [16. Interpreting matching, status, quartiles, and exclusions](#16-interpreting-matching-status-quartiles-and-exclusions)
- [17. Practical workflows by research goal](#17-practical-workflows-by-research-goal)
- [18. Recommended operating practice](#18-recommended-operating-practice)
- [19. Troubleshooting and diagnostics](#19-troubleshooting-and-diagnostics)
- [20. Limitations, methodological cautions, and security](#20-limitations-methodological-cautions-and-security)
- [21. Quick command reference](#21-quick-command-reference)
- [Appendix A. Full run option reference](#appendix-a-full-run-option-reference)
- [Appendix B. Subcommands](#appendix-b-subcommands)
- [Appendix C. Data sources and references](#appendix-c-data-sources-and-references)
- [Appendix D. Checklist for a new research project](#appendix-d-checklist-for-a-new-research-project)
- [Appendix E. Filter guide for new researchers and examples for prioritizing trusted sources](#appendix-e-filter-guide-for-new-researchers-and-examples-for-prioritizing-trusted-sources)
- [Appendix F. License, citation, and support](#appendix-f-license-citation-and-support)
- [Appendix G. Downloading open-access full text (fetch-oa)](#appendix-g-downloading-open-access-full-text-fetch-oa)
- [Appendix H. Quality signals and venue tiers](#appendix-h-quality-signals-and-venue-tiers)
- [Appendix I. Choosing where OpenAlex matches the query](#appendix-i-choosing-where-openalex-matches-the-query)

## 1. What the tool is for: purpose and value

OpenAlex Literature Intelligence is not merely a script that downloads a list of papers. Its purpose is to turn a research query into a structured scientific-literature dataset, enrich that dataset with SCImago venue information, apply an explicit screening policy, and preserve enough audit evidence to explain why each record was retained or excluded.

> **Core idea**  
> The tool helps answer three questions: (1) Which works are related to my topic? (2) Where were those works published, and what were the SJR/quartile characteristics of those sources at the relevant time? (3) Under my declared screening policy, which works belong in the main corpus, and why?

### 1.1. Value chain

| Step | Component | Result |
|---|---|---|
| 1 | Research query | Topic/keywords, year range, abstract requirement |
| 2 | OpenAlex discovery | Find works, paginate, resume, deduplicate |
| 3 | SCImago enrichment | Match sources; publication-year SJR/quartile; latest metadata; APC snapshot |
| 4 | Policy screening | Journal-only, status, Q1/Q2, category, APC, DOAJ, CWTS Core |
| 5 | Audit & provenance | Exclusion reasons, source audit, summary, run metadata, checkpoint |
| 6 | Analysis-ready corpus | Raw / enriched / main / excluded outputs for review, thesis, bibliometrics, or NLP |

### 1.2. Main applications

| Application | How the tool helps |
|---|---|
| Systematic/scoping review | Creates a broad discovery corpus, preserves raw data, applies explicit venue policies, and records exclusions. It does not replace title/abstract/full-text screening. |
| Thesis/dissertation/research project | Builds a topic- and time-bounded literature corpus and keeps both publication-time and latest venue context. |
| Target-journal intelligence | Compares latest SJR/quartile, category, and estimated APC to build a shortlist that can later be verified on publisher and indexing websites. |
| Bibliometric/meta-research | Provides structured paper, source, country, citation, OA, SJR/quartile, status, and category fields. |
| NLP/AI corpus | Collects title/abstract/metadata, deduplicates across queries, and preserves an audit trail for topic modeling, embeddings, classification, or trend analysis. |
| Search-strategy auditing | Stores queries, year bounds, policy, tool version, manifest, source status, and checkpoint state. |
| Journal history | Uses inspect-source plus SCImago 2010–2025 snapshots to inspect SJR/quartile trajectories. |
| Publishing-cost screening | Uses estimated APC snapshot values for preliminary budgeting, followed by verification on the publisher website. |

### 1.3. What the tool does not do

- It does not search Scopus or Web of Science directly; discovery currently uses the OpenAlex Works API.
- It does not download subscription full text and never tries to get past a bot check. Since v3.1.0, fetch-oa downloads only the open-access copies that OpenAlex lists and puts everything else on a manual-download list (Appendix G).
- It does not prove current official Scopus indexing. Presence in the latest SCImago snapshot must not be treated as definitive proof of current Scopus status.
- It does not label journals as predatory. UNMATCHED or absent-from-SCImago is not evidence of predatory behavior.
- It does not provide authoritative publisher APCs. APC values are estimated snapshots and must be verified before submission or budgeting.
- It does not replace a PRISMA or systematic-review protocol. It supports data collection, screening, and auditability.

## 2. Suitable and unsuitable research use cases

| Suitability | Scenario |
|---|---|
| Highly suitable | Topic-based literature discovery + venue enrichment + Q/status/APC filters + audit. |
| Highly suitable | Build title/abstract corpora for NLP, topic mapping, and trends. |
| Highly suitable | Compare quartile at publication time with latest quartile/status. |
| Conditionally suitable | Choosing target journals: use as a preliminary intelligence layer, then verify scope, publisher data, Scopus/WoS, and author guidelines. |
| Conditionally suitable | Conference-oriented reviews: use cautiously because annual SCImago conference coverage varies substantially. |
| Not suitable alone | Concluding predatory/non-predatory status, proving current Scopus status, determining official APCs, or replacing commercial databases. |

## 3. Architecture and end-to-end data flow

The pipeline has three conceptual layers: input/configuration, core processing, and auditable outputs. Raw discovery data is kept separate from the main corpus so that records excluded by policy are never silently lost.

| Layer | Function |
|---|---|
| Input | Query / query-file; year range; require_abstract; config.json; screening policy. |
| OpenAlex | Search works; cursor pagination; checkpoint/resume; deduplicate DOI → OpenAlex ID → normalized title + year. |
| SCImago repository | Read manifest; verify SHA256SUMS; reuse verified cache; redownload missing or corrupted files. |
| Matching & enrichment | Prefer ISSN/ISSN-L, then safe title matching; block title fallback under explicit ISSN conflict; attach historical/latest metadata. |
| Screening | Source status, source type, quartile, category, APC, DOAJ, CWTS Core. |
| Audit/output | raw, enriched, main, excluded, source_audit, summary, run_metadata, checkpoint. |

### 3.1. End-to-end flow

![Figure 1. End-to-end collection, enrichment, screening, and audit flow.](images/data_flow.png)

Figure 1. End-to-end collection, enrichment, screening, and audit flow.

> **Important distinction**  
> OpenAlex is the discovery source: it answers “which works are related?”. SCImago is not used to find papers; it is a venue-level reference layer used to add SJR, quartiles, historical/latest status, categories, and estimated APC data.

### 3.2. Step 1: Define the query and data scope

The user expresses the research question as one or more query strings. Queries may be provided directly with --query or loaded from a text file with --query-file.

- Direct query example: "malware detection machine learning".
- Multiple-query examples: "Windows PE malware", "concept drift malware detection", "adversarial malware detection".
- --from-year and --to-year define the publication window.
- --require-abstract requests only OpenAlex works that have an abstract.
- --search-field chooses where OpenAlex matches the query: search, the default, also matches the full text where OpenAlex has it; title_and_abstract matches titles and abstracts only (Appendix I).
- --max-per-query is useful for a pilot run before collecting a large corpus.

Purpose: convert a research question into a reproducible retrieval strategy. Query strings, the search field, time bounds, abstract requirements, and record limits are recorded in metadata/checkpoint files so the collection can be audited later.

### 3.3. Step 2: Query the OpenAlex Works API

The network source used for paper discovery is the OpenAlex Works API. The endpoint is:

```text
https://api.openalex.org/works
```

For each query, the tool builds a logical request of the following form. The API key is redacted from logs and documentation:

```text
GET https://api.openalex.org/works
  ?search=<QUERY>
  &filter=from_publication_date:<FROM>-01-01,to_publication_date:<TO>-12-31[,has_abstract:true]
  &per_page=<up to 100>
  &cursor=<current cursor>
  &api_key=<REDACTED>
  &mailto=<optional email>
```

| Parameter | Value sent by the tool | Purpose |
|---|---|---|
| search | User query string | Find OpenAlex works relevant to the query. With --search-field title_and_abstract, the query is sent as the filter title_and_abstract.search:&lt;QUERY&gt; instead (Appendix I). |
| filter | from_publication_date, to_publication_date, and optionally has_abstract:true | Restrict the publication window and optionally require abstracts. |
| per_page | Up to 100 records/request | Reduce API calls while paging safely. |
| cursor | Starts with \* then uses meta.next_cursor | Stable pagination and resumable collection. |
| api_key | Read from config.json / supported configuration source | Authenticate and use the API quota. |
| mailto | Configured email, if supplied | Contact/polite-use metadata for OpenAlex. |

Each response contains meta and results. The tool reads meta.count for the estimated number of matches, meta.next_cursor for the next page, and converts each work in results into a normalized record.

### 3.4. Step 3: Normalize OpenAlex work records

Each OpenAlex work is flattened into a research-friendly record that can be stored in CSV/JSONL and matched with SCImago. Important fields include:

- Identifiers: OpenAlex ID and DOI.
- Content: title/display_name, abstract reconstructed from abstract_inverted_index, language, type/type_crossref.
- Time: publication_year and publication_date.
- Authors: names, OpenAlex author IDs, ORCID.
- Institutions/countries: institution names/IDs and country codes.
- Publication source: source name, source OpenAlex ID, source type, ISSN-L, ISSN list, publisher.
- Source indicators: source_is_oa, source_is_in_doaj, source_is_core, source_listed_in.
- Impact/topic fields: cited_by_count, fwci, citation_normalized_percentile, citation_top_1_percent, citation_top_10_percent, references_count, topics, topic IDs, primary topic, keywords; and is_retracted (Appendix H).
- Open Access: is_open_access, oa_status, oa_url, best_oa_pdf_url, best_oa_landing_page.
- Query traceability: matched_query and relevance_score.

OpenAlex does not provide the abstract as a plain text field in this workflow. The tool reconstructs the abstract from abstract_inverted_index by sorting tokens according to their positions.

### 3.5. Step 4: Deduplicate and save checkpoint/resume state

The same paper can be returned by multiple queries. The tool merges records using the following identifier priority:

```text
DOI
  ↓
OpenAlex ID
  ↓
normalized title + publication year
```

When duplicates are merged, matched_query values are combined, a longer abstract is preferred, the larger cited_by_count is retained, a retraction reported by either copy is kept, missing citation measures are filled, and missing DOI/OpenAlex IDs may be filled from the duplicate record. After each page, raw JSONL and checkpoint state are saved. If --max-per-query stops a run before exhaustion, the next cursor is preserved for a later resume.

### 3.6. Step 5: Synchronize and verify the SCImago repository

SCImago is used for enrichment, not discovery. The tool reads the mirror manifest and checksum file, verifies local cache integrity, and downloads only missing or invalid files.

| Component | What the tool does | Why it matters |
|---|---|---|
| manifest.json | Reads first/last year, latest snapshot, filename pattern, policy, and expected file count. | Defines the exact dataset expected by the client. |
| SHA256SUMS.txt | Loads the expected SHA-256 checksum for every data file. | Detects missing, corrupted, or changed files. |
| scimagojr-2010.csv … scimagojr-2025.csv | Downloads if missing/hash-invalid; otherwise reuses verified cache. | Provides historical SJR/quartile/category metadata. |
| scimago-journal-value-2025.csv | Loads estimated APC/value snapshot. | Adds budget-oriented APC estimates and value metadata. |

### 3.7. Step 6: Match OpenAlex sources to SCImago

This step determines whether a paper can receive historical/latest venue metadata. Matching proceeds from strong identifiers to weaker title-based methods:

```text
ISSN_EXACT → ISSNL_EXACT → TITLE_EXACT → TITLE_NORMALIZED → TITLE_STATUS_NORMALIZED
```

- ISSN_EXACT/ISSNL_EXACT have the highest priority because identifiers are stronger than titles.
- TITLE_NORMALIZED tolerates presentation differences such as punctuation and “&” versus “and”.
- TITLE_STATUS_NORMALIZED handles historical suffixes such as “(discontinued)”.
- If titles match but the OpenAlex and SCImago ISSNs explicitly conflict, the tool (since v3.0.5) does not let title matching override the conflict; the record receives source_status=UNMATCHED, match method TITLE_MATCH_ISSN_CONFLICT, and match_diagnostic=ISSN_CONFLICT, and the rejected candidates are listed in issn_conflict_candidates.
- If several candidates remain plausible, the result is AMBIGUOUS rather than silently selecting one.

### 3.8. Step 7: Assign publication-year/latest SJR and classify source status

| Situation | Tool behavior | Interpretation |
|---|---|---|
| Paper 2010–2025 and exact-year snapshot exists | Attach publication-year SJR, Best Quartile, H-index field, areas/categories; ranking_basis=EXACT_PUBLICATION_YEAR. | Historical context is aligned to publication year. |
| Source exists in latest 2025 snapshot | source_status=ACTIVE_LATEST and latest SJR/quartile fields are populated. | The source appears in the latest mirror snapshot. |
| Source appears only in older snapshots | source_status=HISTORICAL_ONLY. | Historical SCImago presence but absent from latest mirror. |
| No reliable match | UNMATCHED, with method/diagnostic retained. | Do not infer low quality or predatory status. |
| Paper year 2026 while latest SCImago=2025 | Do not invent Q2026; publication-year ranking fields remain unavailable, while latest metadata may be used with ranking_basis=LATEST_AVAILABLE. | Separates latest metadata from publication-year metrics. |

### 3.9. Step 8: Apply policy screening to create main.csv

After enrichment, records are not deleted. Instead, every record receives main_corpus=True/False and main_exclusion_reasons. The default main corpus requires source_status=ACTIVE_LATEST and SCImago source type=journal.

The user can change that policy with --include-historical-only, --any-source-status, --all-source-types or --source-type; Q1/Q2 filters; Best Quartile or relevant-category mode; --max-apc; --require-doaj; --require-cwts-core; and --exclude-retracted. --venue-tiers adds venue tier labels from your own list without filtering (Appendix H).

### 3.10. Step 9: Export data and audit evidence

| File | Contains | Primary use |
|---|---|---|
| \*_raw.csv / \*_raw.jsonl | OpenAlex-derived unique papers after deduplication. | Preserve discovery corpus before enrichment. |
| \*_enriched.csv / \*_enriched.jsonl | Raw + SCImago + status + SJR/Q/APC + match diagnostics. | Full analysis or re-screening without re-querying OpenAlex. |
| \*_main.csv | Records that pass the active screening policy. | Main corpus for review/analysis. |
| \*_excluded.csv | Records excluded from main + main_exclusion_reasons. | Audit exclusions and adjust policy. |
| \*_source_audit.csv | One row per OpenAlex source with status/method/candidate/paper count. | Inspect source-matching quality. |
| \*_preprints.csv | Preprints and repository records, most cited first, with the enriched columns. | Screen preprints by hand (Appendix H.7). |
| \*_summary.json | Counts by status, source class, match method, quartile, ranking basis; quartile filter diagnostics. | Quick corpus-quality summary. |
| \*_run_metadata.json | Query, years, config, version, repository/checksum state, environment. | Provenance and reproducibility. |
| \*_checkpoint.json | Cursor and progress per query. | Resume incomplete collection. |

### 3.11. End-to-end example

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --from-year 2020 `
  --to-year 2026 `
  --require-abstract `
  --max-per-query 100 `
  --output test_malware
```

- Send search="malware detection machine learning" to OpenAlex Works API with the 2020–2026 date filter and has_abstract:true.
- Retrieve up to 100 works for the pilot run, rebuild abstracts, and extract DOI/authors/source/ISSN/topics/OA/citation metadata.
- Deduplicate by DOI/OpenAlex ID and write raw + checkpoint.
- Verify SCImago 2010–2025 and APC 2025 cache by SHA-256.
- Match each source to latest and publication-year SCImago snapshots.
- Attach source_status, publication-year SJR/quartile, latest quartile, category/APC fields as available.
- Apply default journal-only + ACTIVE_LATEST policy and write exclusions with reasons.
- Export raw, enriched, main, excluded, source_audit, summary, run_metadata, and checkpoint.

> **Why this matters**  
> main.csv is not simply “the first 100 OpenAlex hits”. It is a policy-defined subset that has passed source identification, venue enrichment, screening, and audit logic.

## 4. Core concepts

### 4.1. OpenAlex

OpenAlex is the discovery source. Each work can contribute metadata such as OpenAlex ID, DOI, title, abstract, year, authors, institutions, countries, source, ISSN/ISSN-L, citation count, topics/keywords, and Open Access information.

### 4.2. SCImago mirror 2010–2025

The tool uses annual SCImago mirror files for 2010–2025 and verifies every required file by SHA-256 before use. The current latest snapshot is 2025. Journal Value/APC is also a 2025 snapshot.

The default manifest URL points to a community mirror (scopus.cuatui.cloud) that the author maintains on a best-effort basis, without an availability guarantee; its files are delivered through a CDN cache. To use another mirror with the same manifest.json and SHA256SUMS.txt layout, set scimago.manifest_url in config.json or pass --manifest-url.

### 4.3. SJR, Best Quartile, and category quartile

SJR and Best Quartile are source-level metadata in each SCImago snapshot. The tool keeps Best Quartile separate from category-specific quartiles. When relevant-category filtering is used, category names must match the SCImago category label after normalization; substring matching is deliberately avoided.

| Source status | Meaning |
|---|---|
| ACTIVE_LATEST | Source matches the latest snapshot. Default main policy accepts it only if it is also a journal and passes all enabled filters. |
| HISTORICAL_ONLY | Source exists in older snapshots but not latest. Enriched keeps it; main excludes it by default unless explicitly included. |
| UNMATCHED | No reliable mapping to SCImago. This does not mean poor quality or predatory. |
| AMBIGUOUS | Multiple plausible candidates remain; the tool does not choose silently. |
| MIXED (source audit) | Aggregate status used when papers from one OpenAlex source receive different record-level statuses. |

| ranking_basis | Interpretation |
|---|---|
| EXACT_PUBLICATION_YEAR | The publication year has an exact SCImago snapshot; use that year’s SJR/quartile. |
| LATEST_AVAILABLE | Publication year is newer than the latest SCImago snapshot and the source is active; only latest metadata is available. |
| UNAVAILABLE | Exact-year data is unavailable and no appropriate latest ranking basis is applicable; no interpolation is performed. |

### 4.4. Matching policy and ISSN protection

| Method | Rule |
|---|---|
| ISSN_EXACT / ISSNL_EXACT | Strongest match. A valid identifier match is preferred even if the title differs slightly. |
| TITLE_EXACT | Used only when there is no contradictory ISSN evidence. |
| TITLE_NORMALIZED | Normalizes title presentation, including “&” versus “and”. |
| TITLE_STATUS_NORMALIZED | Lowest-priority title fallback; removes terminal status labels such as “(discontinued)”. |
| TITLE_MATCH_ISSN_CONFLICT | Title matches but identifiers conflict; the tool refuses the match and retains candidates for manual review. |

> **Reliability-first design**  
> The tool intentionally accepts some false negatives to avoid false-positive venue matching. For example, if OpenAlex only exposes an e-ISSN while SCImago only contains a print ISSN, the tool may diagnose an ISSN conflict instead of guessing that the sources are identical.

## 5. System requirements and preparation

| Component | Requirement |
|---|---|
| Operating system | Windows 11, Linux, or macOS. |
| Python | Python 3.11+ recommended. |
| Internet | Required for OpenAlex and first SCImago synchronization. enrich/inspect can work offline after verified cache is available. |
| OpenAlex API key | Required for run. Not required for sync-scimago, validate-repo, enrich, or inspect-source. |
| Storage | First SCImago sync may download roughly 180 MB; additional space is required for corpus outputs. |
| File permissions | config.json is private; restrict read access on shared machines where appropriate. |

## 6. Detailed installation on Windows 11

### 6.1. Install Python

Install Python from the official Windows download page. Enable the option to add Python to PATH if offered by the installer.

```text
python --version
python -m pip --version
```

### 6.2. Get the tool

Clone the repository with Git, or download the source ZIP of the latest release from the GitHub Releases page and extract it to a fixed directory, for example C:\\OpenAlexV3. Do not run the tool from inside the ZIP archive.

```text
git clone https://github.com/tvquynh/openalex-literature-intelligence.git C:\OpenAlexV3
cd C:\OpenAlexV3
```

### 6.3. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, bypass it only for the current PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

If corporate policy prevents activation, call the venv interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe literature_pipeline.py --help
```

### 6.4. Install dependencies

```text
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 6.5. Create config.json

```powershell
Copy-Item config.example.json config.json
notepad config.json
```

Place the real OpenAlex API key in the private config.json file. Do not edit config.example.json if you plan to share the project.

```json
{
  "openalex": {
    "api_key": "YOUR_OPENALEX_API_KEY",
    "email": ""
  },
  "scimago": {
    "manifest_url": "https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json",
    "cache_dir": "cache/scimago"
  },
  "run": {
    "from_year": 2010,
    "to_year": 2026,
    "require_abstract": false,
    "max_per_query": null,
    "search_field": "search"
  }
}
```

### 6.6. Verify the CLI

```text
python literature_pipeline.py --version
python literature_pipeline.py --help
```

Expected subcommands: sync-scimago, validate-repo, run, enrich, inspect-source.

## 7. Quick installation on Linux/macOS

```bash
git clone https://github.com/tvquynh/openalex-literature-intelligence.git
cd openalex-literature-intelligence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Edit config.json and add your API key. Use shell line continuation with \\ or keep commands on one line; do not use PowerShell backticks on Linux/macOS.

## 8. config.json and configuration precedence

Configuration precedence is: CLI argument &gt; config.json &gt; built-in default. For backward compatibility, the OPENALEX_API_KEY environment variable is still read as a deprecated fallback after config.json.

| Key | Type | Meaning |
|---|---|---|
| openalex.api_key | string | Private API key; required for run. |
| openalex.email | string | OpenAlex mailto/contact; may be left blank. |
| scimago.manifest_url | string | URL of the SCImago mirror manifest. |
| scimago.cache_dir | string | Cache directory, relative to config.json if relative. |
| run.from_year | integer | Default start year; code default is 2010. |
| run.to_year | integer | Default end year; code default is 2026. |
| run.require_abstract | boolean | Whether an abstract is required by default. |
| run.max_per_query | integer/null | Per-query retrieval limit; null means no explicit limit. |
| run.search_field | string | search or title_and_abstract; code default is search (Appendix I). |
| screening.venue_tiers | string | Venue list used as --venue-tiers by run and enrich; relative to config.json if relative; default none. |
| screening.admit_tiers | list of strings | Tiers admitted to the main corpus, as --admit-tier; needs screening.venue_tiers; default none (Appendix H.6). |

> **Security**  
> config.json is ignored by Git and must not be included in public ZIPs. Avoid --api-key on the command line because shell history/process listings can expose it. If a key has been shared outside your control, rotate it.

## 9. Initialize and validate the SCImago repository

### 9.1. First synchronization

```text
python literature_pipeline.py sync-scimago
```

Sequence: download manifest.json → download SHA256SUMS.txt → inspect cache → download missing/hash-invalid files → verify SHA-256.

| Message | Meaning |
|---|---|
| Local cache not found -&gt; downloading from remote | The file is missing locally; this does not mean the remote repository is missing it. |
| OK - downloaded and SHA-256 verified | Download succeeded and checksum matches. |
| OK - local cache verified | Verified local file was reused. |
| Local cache checksum mismatch -&gt; re-downloading | Local integrity failed; tool downloads a fresh copy. |
| integrity_verified: True | All required files passed integrity checks. |

### 9.2. Validate cache independently

```text
python literature_pipeline.py validate-repo
```

Expected result: overall_ok: True and [OK] for every required file.

### 9.3. Force a full redownload

```text
python literature_pipeline.py sync-scimago --force-redownload
```

## 10. run: search, download, and build a corpus

run executes the complete pipeline: OpenAlex discovery → deduplication → SCImago sync/verify → enrichment → policy screening → audit/output.

### 10.1. Single query

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --from-year 2020 `
  --to-year 2026 `
  --require-abstract `
  --max-per-query 100 `
  --output test_malware
```

### 10.2. Multiple queries in one command

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --query "adversarial malware detection" `
  --query "concept drift malware detection" `
  --from-year 2015 `
  --to-year 2026 `
  --output malware_multi_query
```

Duplicate query strings are removed at query-list level; retrieved papers are then deduplicated at DOI → OpenAlex ID → title+year level.

### 10.3. Query file

One query per line. Blank lines and lines beginning with # are ignored.

```text
# my_queries.txt (illustration; the shipped file is examples/queries_example.txt)
malware detection machine learning
adversarial malware detection
concept drift malware detection
static PE malware detection
```

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --output PE_Malware_Research
```

### 10.4. Limit records per query

```text
python literature_pipeline.py run --query "malware detection" --max-per-query 1000 --output malware_1000
```

Stopping because of max-per-query is a successful limit-based stop, not an error. The summary/checkpoint records stopped_by_limit, has_more_results, resume_available, and the cursor.

### 10.5. Do not require abstracts

If --require-abstract is omitted, papers without abstracts remain eligible according to config/defaults. This is useful for bibliometric or source-level analysis where abstract text is not required.

## 11. Complete filtering and screening scenarios

> **Important**  
> Filters decide which papers enter \*_main.csv. \*_raw and \*_enriched keep the broader corpus so screening decisions remain reversible and auditable.

| Goal | Flag(s) | Effect |
|---|---|---|
| Default | No extra source/status flags | Journal + ACTIVE_LATEST. |
| All source types | --all-source-types | Disables journal-only, while default status policy still applies. |
| Journal + conference | --source-type journal --source-type "conference and proceedings" | Keeps only those two SCImago source types. |
| Include historical | --include-historical-only | Allows ACTIVE_LATEST + HISTORICAL_ONLY. |
| Any status | --any-source-status | Allows UNMATCHED/AMBIGUOUS too; use only for special broad-corpus needs. |
| Q1/Q2 | --quartile Q1 --quartile Q2 | Filters quartile using the selected basis/mode. |
| Relevant category | --quartile-mode relevant-category --relevant-category ... | Filters category-specific quartile rather than Best Quartile. |
| APC ceiling | --max-apc 500 | Requires an estimated APC and value &lt;= 500 USD. |
| DOAJ | --require-doaj | Requires OpenAlex source_is_in_doaj=True. |
| CWTS Core | --require-cwts-core | Requires OpenAlex source_is_core=True. |
| Exclude retracted | --exclude-retracted | Excludes works that OpenAlex marks as retracted (reason RETRACTED). |
| Venue tiers | --venue-tiers FILE | Adds venue_tier labels from your own list; excludes nothing (Appendix H). |

### 11.1. Default journal-only

```text
python literature_pipeline.py run --query "malware detection" --output malware_journal
```

### 11.2. Keep all source types

```text
python literature_pipeline.py run --query "malware detection" --all-source-types --output malware_all_sources
```

### 11.3. Journal + conference/proceedings

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --source-type journal `
  --source-type "conference and proceedings" `
  --output malware_journal_conference
```

### 11.4. Q1/Q2 at publication year

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis publication `
  --output PE_Malware_Q1_Q2
```

The publication basis screens each paper by the quartile of the year it was published, which suits historical analyses; up to v3.3.0 it was the default, and the example therefore names it explicitly. The SCImago data end at 2025, so a 2026 paper has no publication-year quartile and fails a publication-basis Q1/Q2 filter; the tool never copies the 2025 quartile into the publication-year columns. When the source is in the latest snapshot (ranking_basis LATEST_AVAILABLE), the exclusion reasons are QUARTILE_UNAVAILABLE; QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE, run and enrich print a warning with the number of such papers, and summary.json counts them under quartile_filter_diagnostics (0 when no paper is affected). The default basis, latest (section 11.5), screens these papers by the latest available snapshot, which for a 2026 paper is the 2025 quartile of its venue, not a 2026 quartile.

### 11.5. Latest quartile

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --output malware_latest_q1q2
```

latest is the default basis: --quartile without --quartile-basis screens every paper by its venue's quartile in the latest SCImago snapshot, so the newest papers are not excluded by accident. For a 2026 paper that is the 2025 quartile of its venue, not a 2026 quartile, and an older paper is also judged by its venue's 2025 quartile rather than by the quartile of its publication year. Sources found only in earlier snapshots (HISTORICAL_ONLY, admitted with --include-historical-only) have no latest quartile and fail a quartile filter on this basis; use --quartile-basis publication for them.

### 11.6. Relevant-category quartile

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-mode relevant-category `
  --quartile-basis publication `
  --relevant-category "Computer Networks and Communications" `
  --relevant-category "Computer Science Applications" `
  --relevant-category "Software" `
  --relevant-category "Information Systems" `
  --output PE_Malware_CS_Q1_Q2
```

### 11.7. Maximum APC

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --max-apc 500 `
  --output low_apc_q1q2
```

When APC is unavailable, the record is excluded with APC_UNAVAILABLE if --max-apc is active. The value is an estimated 2025 snapshot, not an official historical fee.

### 11.8. DOAJ only

```text
python literature_pipeline.py run --query "malware detection" --require-doaj --output malware_doaj
```

### 11.9. CWTS Core only

```text
python literature_pipeline.py run --query "malware detection" --require-cwts-core --output malware_cwts
```

### 11.10. Include HISTORICAL_ONLY

```text
python literature_pipeline.py run --query-file examples\queries_example.txt --include-historical-only --output broad_corpus
```

### 11.11. Ignore source-status filtering

```text
python literature_pipeline.py run --query "malware detection" --any-source-status --output corpus_any_status
```

> **Caution**  
> --any-source-status can place UNMATCHED and AMBIGUOUS records into main.csv. Use it only when you explicitly need a broad corpus and will perform manual screening.

### 11.12. Combine multiple conditions

```powershell
python literature_pipeline.py run `
  --query-file examples\queries_example.txt `
  --from-year 2015 `
  --to-year 2026 `
  --require-abstract `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --max-apc 1000 `
  --require-doaj `
  --output strict_shortlist
```

Enabled conditions are combined with AND logic: a paper enters main only if it satisfies every active requirement.

## 12. enrich: enrich an existing corpus

enrich is for an existing OpenAlex-derived CSV/JSONL or an older output when you want to rerun SCImago enrichment/screening without querying OpenAlex. It does not require an OpenAlex API key.

```powershell
python literature_pipeline.py enrich `
  --input malware_1000.csv `
  --output malware_1000_v3
```

All policy flags available to run can also be used with enrich. If the input already contains enrichment columns, the tool removes stale enrichment/screening fields before recomputing them, including old venue_tier columns. Records retrieved before v3.1.0 lack is_retracted, fwci and citation_normalized_percentile; enrich cannot add them, so run run again to obtain them.

## 13. inspect-source: inspect journal/source history

### 13.1. By title

```text
python literature_pipeline.py inspect-source --title "Journal of Information Security and Applications"
```

### 13.2. By ISSN

```text
python literature_pipeline.py inspect-source --issn 22142126
```

### 13.3. Export the result

```text
python literature_pipeline.py inspect-source --title "IEEE Access" --output ieee_access_history.json
```

inspect-source is useful for inspecting yearly SJR/Q trajectories, latest presence, historical-only status, and the APC snapshot. The same ISSN-conflict protection applies.

## 14. Offline mode, cache, refresh, and resume

### 14.1. Offline SCImago

```text
python literature_pipeline.py validate-repo --offline
python literature_pipeline.py enrich --offline --input malware_1000.csv --output malware_offline
```

run --offline still needs Internet for OpenAlex. Here, --offline only prevents access to the SCImago repository and requires a verified local cache.

### 14.2. Refresh SCImago in a full run

```text
python literature_pipeline.py run --query "malware detection" --refresh-scimago --output malware_refresh
```

### 14.3. Resume

run stores &lt;output&gt;_checkpoint.json and &lt;output&gt;_raw.jsonl. If a run is interrupted or stopped before query exhaustion, rerun the same command with the same output prefix to continue.

```text
python literature_pipeline.py run --query "malware detection" --max-per-query 5000 --output malware_large
```

The checkpoint stores the year range and the abstract requirement of every query. If you rerun the same prefix with a different year range or abstract setting, the run stops with “Checkpoint parameters changed”; use a new output prefix or --no-resume. Adding a query or changing only screening flags needs neither, because every run re-enriches and re-screens the whole raw corpus.

## 15. Understanding every output file

| File | Purpose |
|---|---|
| &lt;prefix&gt;_raw.csv / .jsonl | All unique papers retrieved from OpenAlex after deduplication; no SCImago enrichment; always preserve this. |
| &lt;prefix&gt;_checkpoint.json | Cursor, pages, retrieved count, matches_total, has_more_results/resume state. |
| &lt;prefix&gt;_enriched.csv / .jsonl | Raw + SCImago/APC + source status + match methods + diagnostics + screening fields. |
| &lt;prefix&gt;_main.csv | Papers passing the active policy and every enabled filter. |
| &lt;prefix&gt;_excluded.csv | Papers not in main, with main_exclusion_reasons. |
| &lt;prefix&gt;_source_audit.csv | One row per OpenAlex source with status/method/candidates/diagnostic/paper_count. |
| &lt;prefix&gt;_preprints.csv | Works typed as preprints or from a repository source (arXiv, Zenodo, university archives), most cited first; same columns as the enriched file. |
| &lt;prefix&gt;_summary.json | Paper and unique-source counts by status, class, method, quartile, and ranking basis; quartile_filter_diagnostics counts the papers that fail a publication-basis quartile filter because they were published after the last SCImago year. |
| &lt;prefix&gt;_run_metadata.json | Tool version, query, policy, manifest, environment, and configuration source; never the API key value. |

### 15.1. Important enriched fields

| Field | Meaning |
|---|---|
| source_status | ACTIVE_LATEST / HISTORICAL_ONLY / UNMATCHED / AMBIGUOUS. |
| scimago_sourceid | SCImago Sourceid if matched. |
| latest_match_method | How latest snapshot matching succeeded/failed. |
| publication_match_method | How publication-year matching succeeded/failed. |
| match_diagnostic | Diagnostic such as ISSN_CONFLICT. |
| issn_conflict_candidates | Candidate Sourceids rejected because of identifier conflict. |
| sjr_year_at_publication | SCImago year used for publication metrics. |
| sjr_at_publication | Publication-year SJR if available. |
| best_quartile_at_publication | Best Quartile at publication year. |
| relevant_category_quartile_at_publication | Quartile in the user-selected relevant category. |
| ranking_basis | EXACT_PUBLICATION_YEAR / LATEST_AVAILABLE / UNAVAILABLE. |
| sjr_latest / best_quartile_latest | Latest-snapshot metadata. |
| estimated_apc_usd | Estimated 2025 APC snapshot. |
| main_corpus | True/False after screening. |
| main_exclusion_reasons | One or more reasons the record was excluded from main. |
| is_retracted | True when OpenAlex marks the work as retracted. |
| fwci | Field-weighted citation impact computed by OpenAlex; 1.0 is the expected level for the year and field. |
| citation_normalized_percentile | Citation percentile among comparable works (0 to 1), with top-1% and top-10% flags. |
| venue_tier | Tier from the --venue-tiers list, UNLISTED or AMBIGUOUS; only with --venue-tiers. |
| venue_tier_venue / venue_tier_match | Listed venue that matched and the step that decided. |

## 16. Interpreting matching, status, quartiles, and exclusions

### 16.1. UNMATCHED does not mean “bad journal”

UNMATCHED only means the tool could not establish a sufficiently reliable mapping between the OpenAlex source and SCImago snapshots. Causes include repositories, preprints, conferences, ebook platforms, missing ISSNs, new sources, title differences, or explicit ISSN conflicts. source_audit.csv should be the first place to inspect.

### 16.2. Common exclusion reasons

| Reason | Interpretation |
|---|---|
| SOURCE_STATUS_UNMATCHED / AMBIGUOUS / HISTORICAL_ONLY | Status is not accepted by the current policy. |
| SOURCE_TYPE_FILTER | SCImago source type is not in the allowed list. |
| QUARTILE_UNAVAILABLE | No quartile exists for the requested basis/mode. |
| QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE | Added to QUARTILE_UNAVAILABLE with --quartile-basis publication when the paper was published after the last SCImago year and its source is in the latest snapshot (ranking_basis LATEST_AVAILABLE). |
| QUARTILE_Qx_NOT_SELECTED | Quartile exists but is not one of the selected quartiles. |
| APC_UNAVAILABLE | No APC estimate is available while --max-apc is active. |
| APC_ABOVE_LIMIT | Estimated APC exceeds the ceiling. |
| APC_INVALID | APC could not be parsed as a valid numeric value. |
| NOT_IN_DOAJ | Does not satisfy --require-doaj. |
| NOT_IN_CWTS_CORE | Does not satisfy --require-cwts-core. |
| RETRACTED | OpenAlex marks the work as retracted and --exclude-retracted is active. |

### 16.3. PowerShell checks

```powershell
Import-Csv .\test_malware_source_audit.csv |
  Group-Object source_status |
  Select-Object Name,Count
```

```powershell
Import-Csv .\test_malware_excluded.csv |
  Group-Object main_exclusion_reasons |
  Sort-Object Count -Descending |
  Select-Object -First 20 Name,Count
```

```powershell
Import-Csv .\test_malware_source_audit.csv |
  Where-Object { $_.match_diagnostic -eq "ISSN_CONFLICT" } |
  Format-Table source,issn,source_status,latest_match_method,issn_conflict_candidates -AutoSize
```

## 17. Practical workflows by research goal

### 17.1. Systematic/scoping review

1. Define and version-control the query protocol before running.
1. Run broad discovery and preserve raw corpus.
1. Use enriched to audit source matching, status, and publication-year quartiles.
1. Apply a main policy only if it matches the protocol’s inclusion/exclusion logic.
1. Never delete excluded; retain reasons for reproducibility.
1. Store run_metadata.json and summary.json with project/manuscript materials.
1. If using PRISMA, raw/main/excluded counts can support reporting, but title/abstract/full-text screening is still required.

### 17.2. Thesis/dissertation literature corpus

```powershell
python literature_pipeline.py run `
  --query-file thesis_queries.txt `
  --from-year 2010 `
  --to-year 2026 `
  --require-abstract `
  --output thesis_literature
```

Recommended: treat _enriched.csv as the master corpus, _main.csv as the priority-reading set, and _excluded.csv as the audit trail. When a new SCImago snapshot arrives, re-enrich the raw corpus to update latest status without rerunning discovery if the search question is unchanged.

### 17.3. Journal shortlist for submission

```powershell
python literature_pipeline.py run `
  --query "machine learning malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --max-apc 1000 `
  --output journal_shortlist
```

Group results by source in enriched/main to see which journals frequently publish related work. Always verify scope, official APC, current Scopus/WoS status, and author guidelines before deciding.

### 17.4. Bibliometric/meta-research

Use raw/enriched to analyze year, citations, source, country, institution, topic, OA status, and SJR/quartile. Do not use only main if you intend to describe the full landscape, because main already contains policy-selection bias.

### 17.5. NLP/AI corpus

Enable --require-abstract when abstract text is required. JSONL is convenient for streaming/Python pipelines; CSV is convenient for manual inspection. Deduplication reduces duplicate papers across overlapping queries.

### 17.6. Inspect one venue

```text
python literature_pipeline.py inspect-source --title "Computers & Security"
```

Useful for seeing historical Q trajectories, latest/historical-only status, and APC snapshot availability.

### 17.7. “Venue quality” screening: use it correctly

The tool can implement a venue-screening policy based on SCImago latest, quartile, DOAJ/CWTS Core, and APC. These are user-defined research signals, not absolute quality labels. Never infer “not in SCImago = predatory” or “in SCImago = definitely currently indexed by Scopus”.

## 18. Recommended operating practice

### 18.1. Individual researcher

- Keep one private config.json.
- Use a different output prefix for each project.
- Store query files inside the project folder.
- Do not modify raw outputs in place; create derived files for cleaning.
- Version-control scripts/query/protocol, but never commit config.json or cache.

### 18.2. Research group/lab

- Share the repository link or a tagged release; each member creates a private config.json from config.example.json and uses a separate API key.
- Use the same manifest URL and declared policy for reproducibility.
- Store run_metadata and summary with analysis outputs.
- Include tool version and run date in project-folder naming.
- When SCImago is updated, re-enrich a benchmark corpus and assess impact before changing the production workflow.

### 18.3. When a new SCImago year becomes available

The mirror operator adds the new annual file, updates manifest first_year/last_year/latest_snapshot_year and the expected file count, and regenerates SHA256SUMS.txt. Users then run sync-scimago again and can re-enrich old corpora to update latest metadata. Never label a paper with a future-year quartile before that snapshot exists.

## 19. Troubleshooting and diagnostics

| Symptom | Interpretation / action |
|---|---|
| python not recognized | Check python --version; reinstall Python/PATH; try py --version on Windows. |
| PowerShell blocks Activate.ps1 | Use Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass for the current session, then activate again. |
| Missing requests module | Ensure the correct venv is active and run python -m pip install -r requirements.txt. |
| ERROR (config) | Check JSON syntax, allowed sections/keys, and data types. |
| run says API key is missing | Copy config.example.json to config.json and fill openalex.api_key. |
| sync says cache not found | Normal on first sync; wait for downloaded and SHA-256 verified. |
| checksum mismatch | Online: tool redownloads. Offline: reconnect and resync first. |
| offline fails | Verified cache/metadata is missing; run sync-scimago online first. |
| Very high UNMATCHED | Inspect source_audit.csv and source_class; repositories/conferences/ebooks often do not fit journal policy. |
| ISSN_CONFLICT | Review candidates manually; the tool intentionally rejects title-only matching when identifiers contradict. |
| 2026 paper has no publication-year Q | Correct design while latest SCImago is 2025; latest metadata may still be available. |
| Q1/Q2 publication filter removes 2026 papers | Expected: such papers get QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE, and run and enrich print a warning with their number. Use --quartile-basis latest (the default) if your question concerns the latest quartile, which is not a 2026 quartile. |
| APC filter excludes too much | APC_UNAVAILABLE means the snapshot has no estimate, not that the journal is necessarily expensive. |
| Checkpoint parameters changed | The year range or abstract setting of a query changed under the same output prefix; use a new prefix or --no-resume. |
| Conference absent in one annual file | Do not infer discontinuation; SCImago conference coverage varies strongly by year. |
| OpenAlex results change over time | Expected as OpenAlex data/ranking changes; rely on run_metadata and run date for audit. |

## 20. Limitations, methodological cautions, and security

- OpenAlex search can change over time and is not equivalent to Scopus/WoS syntax or coverage.
- The SCImago mirror currently covers 2010–2025; latest snapshot is 2025 in this Stable release.
- The default SCImago mirror is a best-effort community service. If it is unavailable, sync-scimago fails, while commands run with --offline keep working from a verified local cache.
- 2026 papers use latest-available metadata where applicable; there is no “Q2026” until a 2026 snapshot exists.
- Conference coverage changes substantially by year; absence does not prove delisting from Scopus.
- SCImago presence is not a definitive statement of current Scopus indexing; absence is not a predatory label.
- APC is an estimated 2025 snapshot and must be verified with the publisher.
- Strict ISSN-conflict protection may produce false negatives in order to avoid false-positive venue matching.
- The mirror H-index field may repeat a current value across years; do not interpret h_index_at_publication as a historical H-index trajectory without validation.
- For systematic reviews, preserve raw discovery corpus and the screening policy; do not keep only main.csv.
- When sharing derived datasets, users must review applicable OpenAlex and SCImago terms/licensing.

### 20.1. API key security

- Do not hard-code the key into Python.
- Do not commit config.json or place it in public ZIPs.
- Avoid --api-key on the CLI because shell history and process listings can expose it.
- run_metadata records only that a key is configured and its source, never the value.
- Network/HTTP errors are redacted so api_key is not leaked in URLs.
- Rotate the key if it has ever been exposed outside your control.

## 21. Quick command reference

| Task | Example command |
|---|---|
| Check version | python literature_pipeline.py --version |
| Help | python literature_pipeline.py --help |
| Sync SCImago | python literature_pipeline.py sync-scimago |
| Validate repository | python literature_pipeline.py validate-repo |
| Run query | python literature_pipeline.py run --query "..." --output out |
| Run query-file | python literature_pipeline.py run --query-file queries.txt --output out |
| Enrich CSV | python literature_pipeline.py enrich --input raw.csv --output enriched |
| Inspect by title | python literature_pipeline.py inspect-source --title "IEEE Access" |
| Inspect by ISSN | python literature_pipeline.py inspect-source --issn 21693536 |
| Offline validate | python literature_pipeline.py validate-repo --offline |
| Force sync | python literature_pipeline.py sync-scimago --force-redownload |
| No resume | python literature_pipeline.py run ... --no-resume |
| All source types | ... --all-source-types |
| Include historical | ... --include-historical-only |
| Q1/Q2 publication | ... --quartile Q1 --quartile Q2 --quartile-basis publication |
| Q1/Q2 latest | ... --quartile Q1 --quartile Q2 --quartile-basis latest |
| Download open-access PDFs | python literature_pipeline.py fetch-oa --input out_main.csv |
| Exclude retracted works | ... --exclude-retracted |
| Label venue tiers | ... --venue-tiers my_venues.tsv |

## Appendix A. Full run option reference

| Option | Meaning |
|---|---|
| --config PATH | Use another private config file. |
| --manifest-url URL | Override SCImago manifest URL. |
| --cache-dir PATH | Override cache directory. |
| --offline | Use verified SCImago cache only. |
| --include-historical-only | Allow HISTORICAL_ONLY into main. |
| --any-source-status | Disable status filtering; can include UNMATCHED/AMBIGUOUS. |
| --source-type TYPE | Allowed SCImago source type; repeatable. |
| --all-source-types | Disable journal-only. |
| --quartile Q1\|Q2\|Q3\|Q4 | Allowed quartile; repeatable. |
| --quartile-basis latest\|publication | Snapshot basis for --quartile filtering; default latest (section 11.5). |
| --quartile-mode best\|relevant-category | Use Best Quartile or relevant-category quartile. |
| --relevant-category NAME | Category of interest; repeatable. |
| --max-apc USD | Maximum estimated APC. |
| --require-doaj | Require source_is_in_doaj=True. |
| --require-cwts-core | Require source_is_core=True. |
| --exclude-retracted | Exclude works marked as retracted by OpenAlex. |
| --venue-tiers FILE | Label papers with venue tiers from your list; filters nothing by itself. |
| --admit-tier TIER | Admit venues of this tier even when the venue filters would exclude them; repeatable (Appendix H.6). |
| --no-admit-tiers | Admit no tier, overriding screening.admit_tiers in config.json. |
| --query TEXT | OpenAlex query; repeatable. |
| --query-file FILE | One query per line; # introduces comments. |
| --from-year N | Start year. |
| --to-year N | End year. |
| --require-abstract | Require an abstract. |
| --max-per-query N | Maximum retrieved records per query. |
| --search-field search\|title_and_abstract | Where OpenAlex matches the query; default search (Appendix I). |
| --api-key KEY | CLI fallback; not recommended for security. |
| --email EMAIL | OpenAlex mailto. |
| --output PREFIX | Output prefix; default literature. |
| --no-resume | Discard old state and start a new collection. |
| --refresh-scimago | Refresh SCImago before a full run. |

## Appendix B. Subcommands

| Subcommand | Main options |
|---|---|
| sync-scimago | --config, --manifest-url, --cache-dir, --offline, --force-redownload |
| validate-repo | --config, --manifest-url, --cache-dir, --offline |
| run | Repository options + policy options + query/year/key/output/resume/refresh |
| enrich | Repository options + policy options + --input + --output + --refresh-scimago |
| inspect-source | Repository options + --title/--issn + optional --output |
| fetch-oa | --input, --output-dir, --delay, --timeout, --max-mb, --limit, --retry-failed (no API key, no SCImago access) |

## Appendix C. Data sources and references

- Project repository and issue tracker: https://github.com/tvquynh/openalex-literature-intelligence
- OpenAlex Works API: https://api.openalex.org/works
- OpenAlex API documentation: https://help.openalex.org/api/
- OpenAlex paging: https://help.openalex.org/api/paging/
- OpenAlex authentication: https://help.openalex.org/api/authentication/
- SCImago mirror manifest: https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json
- SCImago mirror checksums: https://scopus.cuatui.cloud/du-lieu/scimago/SHA256SUMS.txt
- Original SCImago source: https://www.scimagojr.com/

## Appendix D. Checklist for a new research project

1. Create the project folder and query file.

2. Define the research question and screening policy before running.

3. Check config.json/API key.

4. Run sync-scimago and validate-repo.

5. Run a 20–100 paper pilot to test the query.

6. Inspect source_audit and excluded to detect an overly strict policy or poor query.

7. Run the full corpus.

8. Preserve raw/enriched/main/excluded/summary/run_metadata/checkpoint.

9. Do not delete raw or excluded.

10. Record tool version, run date, and policy in the research method.

## Appendix E. Filter guide for new researchers and examples for prioritizing trusted sources

This appendix is for researchers who encounter terms such as Q1/Q2, DOAJ, CWTS Core, ACTIVE_LATEST, Best Quartile, relevant category, or APC but are not yet sure when to use them. The goal is not to create a “trusted paper” button. The goal is to define a screening policy that has a clear rationale, can be explained, and can be audited.

### E.1. Understand each filter: what it means, what it helps with, and what it can exclude

| Filter / concept | What it means | Benefit | Main caution |
|---|---|---|---|
| ACTIVE_LATEST | The matched source appears in the latest SCImago mirror snapshot. | Useful for prioritizing venues that still appear in the latest dataset. | Not proof of official current Scopus indexing. |
| Journal-only | Keeps SCImago source type=journal. | Creates a more homogeneous corpus and removes many repositories/proceedings. | Can miss important conferences, especially in Computer Science. |
| Q1 / Q2 | Relative journal position within a SCImago category based on SJR. Q1 is the top quartile; Q2 the next quartile. | Useful for prioritizing venues with stronger SJR standing. | Quartile is a venue/category/year property, not a score for the individual paper. |
| publication quartile | Uses quartile from the paper’s publication year. | Historically appropriate when evaluating the context at publication time. | Newer papers may have no exact-year snapshot yet. |
| latest quartile | Uses the latest available snapshot. | Useful for current journal-shortlist decisions. | Should not be described as the paper’s historical quartile. |
| relevant category | Uses quartile only in user-selected categories. | Avoids relying on a journal’s best quartile in an unrelated category. | Requires exact category labels and can narrow the corpus substantially. |
| DOAJ | OpenAlex indicates the source is listed in the Directory of Open Access Journals. | Adds an OA transparency/best-practice membership signal. | DOAJ covers fully OA journals; many excellent subscription/hybrid journals are not in DOAJ. |
| CWTS Core | OpenAlex indicates the source is in the CWTS/Leiden core source list. | Adds an independent core-source allow-list signal. | Not the same as Clarivate Web of Science Core Collection and not a paper-quality guarantee. |
| Retracted works | OpenAlex marks the work as retracted (is_retracted). | --exclude-retracted removes such works from main. | Retraction notices reach OpenAlex with a delay; check key papers on the publisher page. |
| Venue tier list | Your own list of venues and tiers, given with --venue-tiers. | Ranks conferences, which SCImago gives no quartile, by the rule of your protocol. | The tiers are only as good as the list; it labels papers and excludes nothing. |
| APC ceiling | Requires estimated APC &lt;= the chosen threshold. | Useful for budget-oriented shortlists. | Snapshot estimate only; missing APC can cause exclusion and official publisher price may differ. |
| HISTORICAL_ONLY | Source exists in older SCImago snapshots but not latest. | Useful for historical literature and longitudinal analysis. | Should not be treated automatically as low quality or formally discontinued from Scopus. |
| --any-source-status | Allows records regardless of status. | Useful for very broad exploratory corpora. | Can put unmatched or ambiguous sources into main; manual review is then essential. |

### E.2. Decision tree: which filter should a new researcher choose?

- If your goal is systematic/scoping review coverage: start broad. Preserve raw/enriched. Do not use prestige filters as a substitute for methodological screening.
- If your goal is a manageable reading list for a thesis: start with default journal + ACTIVE_LATEST, then add latest Q1/Q2 if the shortlist is still too large.
- If you are studying historical literature quality/context: use --quartile-basis publication and consider --include-historical-only.
- If you are choosing journals to submit to now: use --quartile-basis latest, optionally combine with relevant category and an APC ceiling.
- If you specifically need fully Open Access journals: add --require-doaj.
- If you want a CWTS/Leiden core-source shortlist: add --require-cwts-core.
- If you need to rank conferences as well as journals, or to apply the venue tiers of your own protocol: write a venue list and add --venue-tiers (Appendix H).
- In every review: add --exclude-retracted, and check the retraction status of key papers by hand.
- If the venue spans many categories: prefer relevant-category mode over Best Quartile when disciplinary fit matters.

> **Do not enable every filter by default**  
> Policy filters are combined with AND logic. Every additional condition reduces main.csv and increases the risk of false negatives. Tight filtering is useful for a priority shortlist, not automatically for discovery or systematic review inclusion.

### E.3. Example 1: A new researcher wants a manageable set of relatively strong venues

Goal: build a reading shortlist for a thesis/dissertation. Prefer journals that still appear in the latest SCImago snapshot and are currently Q1/Q2. This is a balanced, easy-to-explain starting point.

```powershell
python literature_pipeline.py run `
  --query "machine learning malware detection" `
  --from-year 2018 `
  --to-year 2026 `
  --require-abstract `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --output thesis_priority_reading
```

- Default policy already means journal + ACTIVE_LATEST, so --source-type journal is unnecessary.
- Latest Q1/Q2 prioritizes venues with stronger SJR positions in the latest snapshot.
- Keep _raw.csv and _enriched.csv so you can broaden the corpus if the priority list is too small.

### E.4. Example 2: Q1/Q2 at the time each paper was published

Goal: analyze literature historically and avoid evaluating old papers with today’s journal ranking. Use publication-year quartiles.

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --from-year 2010 `
  --to-year 2025 `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis publication `
  --include-historical-only `
  --output historical_q1q2
```

- publication basis uses the quartile from the paper’s publication year.
- --include-historical-only allows papers from venues that were in SCImago historically but are absent from latest.
- If the time range includes 2026 while latest SCImago is 2025, 2026 papers cannot have exact 2026 publication-year quartiles.

### E.5. Example 3: Q1/Q2 in the relevant discipline, not merely Best Quartile

A journal can belong to multiple SCImago categories. Best Quartile uses the strongest category result, which may not be the category that matters for your study. relevant-category mode lets you restrict the quartile test to categories you actually care about.

```powershell
python literature_pipeline.py run `
  --query "malware detection machine learning" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-mode relevant-category `
  --quartile-basis latest `
  --relevant-category "Computer Science Applications" `
  --relevant-category "Information Systems" `
  --relevant-category "Software" `
  --output malware_relevant_q1q2
```

### E.6. Example 4: Open Access via DOAJ + Q1/Q2

DOAJ is the Directory of Open Access Journals. DOAJ lists fully Open Access journals and requires transparent information about the journal website, ISSN, licensing, editorial board, and editorial/quality-control process. In this tool, --require-doaj checks the membership flag supplied by OpenAlex; it does not call the DOAJ API directly.

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --require-doaj `
  --output malware_oa_q1q2
```

Benefit: combines two different venue-level signals: SJR standing (Q1/Q2) plus DOAJ OA-list membership. Trade-off: many excellent subscription or hybrid journals are excluded because they are not fully OA and therefore are not in DOAJ.

### E.7. Example 5: CWTS Core + Q1/Q2

CWTS Core is a core source list maintained by the Centre for Science and Technology Studies (CWTS), Leiden University, for Leiden Ranking Open Edition. OpenAlex exposes an is_core flag for sources in that list. It is not the Clarivate Web of Science Core Collection.

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --require-cwts-core `
  --output malware_cwts_q1q2
```

Benefit: adds an independent core-source allow-list signal. Caution: membership does not mean OpenAlex or CWTS certifies the quality of every individual article.

### E.8. Example 6: Q1/Q2 under an APC budget

For submission planning under a limited budget, filter latest Q1/Q2 venues and set an APC ceiling. This is a shortlist tool, not a final submission decision.

```powershell
python literature_pipeline.py run `
  --query "machine learning malware detection" `
  --quartile Q1 `
  --quartile Q2 `
  --quartile-basis latest `
  --max-apc 1000 `
  --output q1q2_under_1000
```

- APC values are estimated 2025 snapshots and must be verified on the publisher website.
- APC_UNAVAILABLE means the tool lacks an estimate; it does not prove the journal has no APC or is expensive.

### E.9. Example 7: Very strict shortlist (Q1 + relevant category + DOAJ + CWTS Core + APC)

Multiple signals can be combined to create a very small priority shortlist. Because the policy is AND-based, every paper must satisfy every enabled condition.

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --quartile Q1 `
  --quartile-mode relevant-category `
  --quartile-basis latest `
  --relevant-category "Computer Science Applications" `
  --require-doaj `
  --require-cwts-core `
  --max-apc 1000 `
  --output very_strict_shortlist
```

Strength: easy-to-manage shortlist with several positive venue-level signals. Weakness: high false-negative risk; excellent journals may be excluded because they are hybrid/subscription, not in CWTS Core, lack an APC estimate, or are not in the exact category entered.

### E.10. Example 8: Why not filter for “prestige” too early in a systematic or scoping review?

For systematic/scoping reviews, the first priority is usually coverage of the search strategy and inclusion/exclusion criteria derived from the research question. Using quartile/DOAJ/CWTS Core to eliminate evidence during discovery may introduce venue-selection bias. The tool deliberately separates raw/enriched from main so you can search broadly while still creating a priority subset.

- Use \*_raw.csv or \*_enriched.csv as the discovery/audit corpus.
- Use \*_main.csv as a priority layer only if the policy matches your protocol.
- Do not treat exclusion by a venue filter as a scientific reason to exclude a study unless the protocol explicitly defines it.
- Then perform title/abstract/full-text screening and methodological quality assessment according to the review protocol.

### E.11. Example 9: Computer Science project that must keep conferences

Many Computer Science conferences are scientifically important. Journal-only creates a homogeneous corpus but may omit critical conference literature. If your research question requires conferences, enable them explicitly rather than disabling all source-type filtering.

```powershell
python literature_pipeline.py run `
  --query "malware detection" `
  --source-type journal `
  --source-type "conference and proceedings" `
  --output malware_journal_and_conference
```

> **Conference caution**  
> SCImago conference/proceedings coverage changes substantially across years. Absence from one annual export must not be interpreted as formal discontinuation from Scopus.

### E.12. Recommended presets for new users

| Goal | Suggested starting policy | Why |
|---|---|---|
| Broad exploratory discovery | Keep raw/enriched; avoid prestige filters initially. | Maximizes coverage and preserves auditability. |
| Balanced thesis reading list | Default journal + ACTIVE_LATEST + latest Q1/Q2. | Reasonable size and easy to explain. |
| Historical evaluation | publication Q1/Q2 + include historical. | Aligns ranking context with publication time. |
| Current target-journal shortlist | latest Q1/Q2 + relevant category; optional APC ceiling. | Focuses on current venue standing and fit. |
| Fully OA priority list | latest Q1/Q2 + DOAJ. | Combines SJR standing with fully OA-list membership. |
| Very strict priority list | Q1 + relevant category + DOAJ + CWTS Core + APC. | Useful only when a tiny shortlist is desired; high false-negative risk. |

### E.13. How to tell whether your filters are too strict

After a 20–100 paper pilot, do not inspect only main count. Read raw, enriched, excluded, and source_audit together.

- Many QUARTILE_UNAVAILABLE: the chosen basis/mode may be too strict, or papers may be newer than the latest ranking snapshot.
- Many NOT_IN_DOAJ: remember DOAJ covers fully OA journals; do not conclude the others are poor quality.
- Many NOT_IN_CWTS_CORE: reconsider whether your research question truly requires the core-list restriction.
- Many APC_UNAVAILABLE: do not interpret this as “expensive”; it only means the snapshot has no estimate.
- Many UNMATCHED: inspect source_class in source_audit; repositories/conferences/ebook platforms often do not fit journal-oriented policies.

### E.14. What a new researcher should remember

- Q1/Q2 is a relative journal/category position based on SJR; it is not a score for an individual paper.
- DOAJ is a list of fully OA journals with transparency/best-practice criteria; it is not a list of every good journal.
- CWTS Core is a Leiden/CWTS core source list; it is not the Web of Science Core Collection.
- ACTIVE_LATEST means present in the latest SCImago mirror snapshot; it is not definitive proof of current official Scopus indexing.
- Relevant category is useful when a journal belongs to many categories and disciplinary fit matters.
- APC filtering is a budget tool based on an estimate, not an official price list.
- Filters combine with AND logic: stricter policies increase omission risk.
- For systematic reviews, preserve raw/enriched and never replace methodological screening with prestige-oriented venue filters.

### E.15. Official references for understanding filters

- OpenAlex, Sources overview / allow lists: https://help.openalex.org/data/sources/
- OpenAlex, Source attributes (is_in_doaj, is_core): https://help.openalex.org/data/sources/attributes/
- OpenAlex, Source lists: https://help.openalex.org/data/source-lists/
- DOAJ, Guide to applying / inclusion criteria: https://doaj.org/apply/guide/
- DOAJ, Principles of transparency and best practice: https://doaj.org/apply/transparency/
- SCImago Journal & Country Rank: https://www.scimagojr.com/

> **Conclusion**  
> OpenAlex Literature Intelligence is most useful when you need to transform literature discovery into a pipeline with preserved raw data, enriched venue metadata, explicit screening policy, and an auditable trail. Its main value is not only finding papers, but making the resulting corpus explainable, reproducible, and updateable as source data changes.

## Appendix F. License, citation, and support

- License: the source code is released under the MIT License (LICENSE in the repository). The license covers the code only; OpenAlex and SCImago data remain under the terms of their providers (THIRD_PARTY_DATA.md).
- Citation: if the tool supports published work, cite it with the metadata in CITATION.cff; GitHub shows it under “Cite this repository”.
- Questions and bug reports: open an issue at https://github.com/tvquynh/openalex-literature-intelligence/issues. Include the tool version, the command, and the relevant part of the output. Never paste an API key or the contents of config.json.
- Security problems: follow SECURITY.md.

## Appendix G. Downloading open-access full text (fetch-oa)

fetch-oa reads a corpus produced by run or enrich and downloads the open-access PDF of each paper. It needs no API key and sends no request to OpenAlex: it uses the best_oa_pdf_url and oa_url columns already in the file. Papers without an open copy, and every link that fails, go to a manual-download list.

```text
python literature_pipeline.py fetch-oa --input thesis_literature_main.csv --limit 20
python literature_pipeline.py fetch-oa --input thesis_literature_main.csv
```

The first command is a pilot that tries 20 papers; the second processes the whole file. The output folder defaults to &lt;input name&gt;_fulltext next to the input file (here thesis_literature_main_fulltext); --output-dir chooses another one.

### G.1. How requests are made

- Requests are sent one at a time, at least --delay seconds apart for the same host (default 2 s, minimum 1 s), with the User-Agent OpenAlexLiteratureIntelligence/4.1.0 and a link to the project repository.
- robots.txt is read once per host and run, following redirects, with RFC 9309 matching (longest rule wins, \* and $ wildcards). A path it disallows is never requested (ROBOTS_DISALLOWED), a Crawl-delay longer than --delay is honoured (up to 60 s), and a host whose robots.txt cannot be read is skipped for that run (ROBOTS_UNREACHABLE).
- Redirects are followed one hop at a time, so a link through doi.org never reaches a host that is already blocked.
- A response is kept only if it is a complete PDF (it starts with %PDF- and ends with %%EOF) of at most --max-mb megabytes (default 100); a cut-off file is discarded (TRUNCATED) and tried again on the next run.

### G.2. Hosts that block bots

When a host answers with a bot check, the tool records it in blocked_hosts.json and sends it no further request, in this run or in later runs. A bot check is a challenge or CAPTCHA page (for example from Cloudflare, Akamai, Imperva, PerimeterX, DataDome, DDoS-Guard or AWS WAF), HTTP 403, or the header tdm-reservation: 1, by which a publisher reserves text and data mining rights. The block is saved at once, so an interrupted run cannot forget it, and when the check appears after a redirect, the hosts that redirected to it are blocked as well. The tool never tries to get past a bot check: the papers of that host go to the manual-download list. To try a host again, delete its entry from blocked_hosts.json. Some answers only pause a host for the current run and are tried again next time: HTTP 429 (RATE_LIMITED), a busy server that sends Retry-After with a 5xx status, repeated network failures, and an unreadable robots.txt. DOI and handle resolvers such as doi.org are never blocked, because blocking them would cut every DOI link. A resolver that only redirected to a bot check is not paused either, since its next DOI may lead to another publisher; a resolver that answers with a bot check itself is paused for the run (HOST_PAUSED).

### G.3. Output folder

- pdf/&lt;OpenAlex ID&gt;.pdf: the downloaded files.
- downloaded.csv: one row per downloaded paper with the file, its SHA-256, its size, and the source URL.
- manual_download.csv, manual_download.html, manual_download.ris: the papers still to be fetched by hand, with the reason, the DOI, landing-page and PDF links, and the file name to use. The .ris file imports into Zotero.
- pdf_manual/: save PDFs downloaded by hand here under the suggested file name. The next run registers them (MANUAL_DOWNLOADED) and removes them from the manual list.
- blocked_hosts.json, fetch_state.json, fetch_log.csv, fetch_summary.json: blocked hosts with the evidence, the state of every paper, every request sent, and the counts of each run.

### G.4. Reasons in the manual list

- NOT_OPEN_ACCESS: OpenAlex lists no open copy, which is typical for subscription journals.
- NO_OA_URL: the paper is marked open access but has no usable link.
- BOT_CHECK: the request for this paper met a bot check; its host is now blocked.
- HOST_BLOCKED: the host was blocked earlier, so no request was sent.
- ROBOTS_DISALLOWED: robots.txt forbids automatic access to the link.
- NOT_OPEN_ACCESS also covers rows that carry a link but are marked closed access: they are never requested.
- NOT_PDF: the link returned a web page instead of a PDF, often the article page, where the PDF can be downloaded by hand.
- HTTP_404, HTTP_410 and other HTTP_4xx codes: the link failed.
- RATE_LIMITED, HOST_PAUSED, ROBOTS_UNREACHABLE, NETWORK_ERROR, HOST_UNREACHABLE, HTTP_5XX, TRUNCATED, ERROR: temporary problems, retried on the next run.
- TOO_LARGE, TOO_MANY_REDIRECTS, and NOT_ATTEMPTED for papers left for a later run by --limit.

### G.5. Reruns and licences

Run the same command again at any time, also after pressing Ctrl+C: progress is saved after every paper. Downloaded papers are skipped, temporary failures are retried, and permanent failures are kept unless --retry-failed is given; this decision is taken separately for each link of a paper. Blocked hosts stay blocked either way. TOO_LARGE is permanent, so after raising --max-mb run with --retry-failed. Open access does not always mean free reuse: check the licence of each file before sharing or redistributing it.

## Appendix H. Quality signals and venue tiers

Since v3.1.0, run keeps five OpenAlex fields that help to judge and order papers, and two options use them or add your own venue tiers. None of them replaces reading the papers.

### H.1. Fields taken from OpenAlex

- is_retracted: OpenAlex marks the work as retracted. --exclude-retracted moves such works to _excluded.csv with the reason RETRACTED.
- fwci: field-weighted citation impact, the citations a work received relative to those expected for works of the same publication year and field; 1.0 is the expected level.
- citation_normalized_percentile: the citation percentile of the work among comparable works, from 0 to 1. citation_top_1_percent and citation_top_10_percent say whether it is in the top 1% or 10%.

OpenAlex computes the citation measures when the query runs (see its documentation for the exact definitions), so they change as citations accrue, carry little information for papers from the last year or two, and are empty when OpenAlex has not computed them. They are taken from the responses already received, so no extra request is made. Use them to order reading within a group of papers, not to exclude papers; the tool never filters on them. Records retrieved before v3.1.0 lack these columns, and enrich cannot add them; run run again to obtain them.

### H.2. Venue tiers from your own list

--venue-tiers FILE labels every paper with the tier of its venue from a list that you write and approve, for example the tiers defined in a review protocol. The tool does not rank venues itself, and the option removes nothing from the main corpus. The file examples/venue_tiers_example.tsv shows the format; its tiers are examples, not a recommendation.

The list is a tab-separated file, or comma-separated when its name ends in .csv, with a header row and one row per venue. Lines starting with # and blank lines are ignored. The columns are:

- tier: any label you choose (A, B, core, and so on); UNLISTED and AMBIGUOUS are reserved.
- venue: the name written to venue_tier_venue.
- openalex_source_id, issn, title_exact, title_contains: ways to recognise the venue. Give at least one, and separate several values with a semicolon.

Each source ID, ISSN or phrase may belong to one venue only. A malformed list (an unknown column, an empty tier, an invalid ISSN or source ID, a key given for two venues, a stray cell) stops run or enrich before any download, with the line number.

### H.3. How a venue is recognised

The steps are tried in this order, and the first step that finds a listed venue decides:

- the OpenAlex source ID of the paper's source;
- any ISSN or ISSN-L of the source;
- the whole OpenAlex source name (title_exact);
- a phrase contained in the source name (title_contains). Phrases match whole words, and the longest listed phrase wins: for a paper in the ACM Asia Conference on Computer and Communications Security, the phrase asia conference on computer and communications security beats conference on computer and communications security.

Names ignore case, punctuation and accents, and & counts as and. A paper whose source matches two listed venues at the same step gets AMBIGUOUS with both names: correct the list and run enrich again rather than choosing by hand. A paper whose source matches no row gets UNLISTED.

### H.4. Outputs

- venue_tier, venue_tier_venue and venue_tier_match (openalex_source_id, issn, title_exact or title_contains) in the enriched, main, excluded and source-audit files.
- paper_venue_tier_counts and unique_source_venue_tier_counts in the summary.
- The list's path, SHA-256, number of venues and tiers in the run metadata, so the labels of a run can be traced to the exact list.

```text
python literature_pipeline.py enrich --input thesis_literature_raw.jsonl --output thesis_tiers --all-source-types --exclude-retracted --venue-tiers my_venues.tsv
```

### H.5. Writing a useful list

- SCImago assigns no quartile to conference proceedings, so a list is the practical way to rank conferences together with journals.
- Conference papers printed in a book series such as Lecture Notes in Computer Science carry the series as their OpenAlex source, so such a conference cannot be recognised by name; tier those papers by hand.
- Prefer ISSNs for journals: two journals can share a title (for example Electronics), but not an ISSN.
- After a first run, look at the UNLISTED sources with the most papers in _source_audit.csv and extend the list. Record the list and its SHA-256 in your protocol.

### H.6. Admitting listed venues to the main corpus

The default main corpus keeps journals of the latest SCImago snapshot. Many strong conferences are absent from SCImago, and those present have no quartile, so the default policy and any quartile filter exclude them. Since v4.1.0, --admit-tier admits every paper whose venue has the given tier in your list, even when the SCImago status, source-type or quartile filters would exclude it; repeat the option for several tiers. The APC, DOAJ, CWTS Core and retraction filters still apply.

```text
python literature_pipeline.py enrich --input thesis_literature_raw.jsonl --output thesis_ab --venue-tiers my_venues.tsv --admit-tier A --admit-tier B
```

- main_admission_tier holds the admitted tier and main_waived_reasons the venue filters that were set aside; both columns exist only when --admit-tier is used.
- summary.json counts the papers admitted this way under admission_by_venue_tier.
- A tier missing from the list, a reserved name (UNLISTED, AMBIGUOUS), or --admit-tier without a list stops run or enrich before any download.
- screening.venue_tiers and screening.admit_tiers in config.json make the list and the admitted tiers defaults; --no-admit-tiers turns admission off for a journal-only view.

On 100 papers of a live 2010-2026 query, a list with the major security and machine-learning conferences in tiers A and B raised the main corpus from 25 to 38 papers, all 13 from conferences. Check that the list's names match the source names that OpenAlex uses: a phrase with one extra word matches nothing.

### H.7. Preprints for manual screening

Preprints are not admitted wholesale: most are not peer reviewed, and the same work often appears later in a journal or a conference. Every run and enrich writes &lt;output&gt;_preprints.csv with the works that OpenAlex types as preprints or whose source is a repository (arXiv, Zenodo, a university archive), most cited first: the top-10% citation flag, then the citation percentile, then the citation count. The file has the enriched columns, so fetch-oa can read it, and summary.json reports the counts under preprints. Important preprints, such as datasets released only on arXiv, are best reached through the papers that cite them.

OpenAlex sometimes gives a repository copy as the primary location of a paper that appeared at a conference or in a journal, for example a university archive for a USENIX Security paper. The tool reads the source of the primary location only, so such a paper is listed in _preprints.csv and is not admitted by --admit-tier, even when its venue is in your list. When screening the preprint list, look for the published version of each work.

## Appendix I. Choosing where OpenAlex matches the query

Since v3.2.0, run can send each query to OpenAlex in two ways. The choice changes which works are returned, so record it in your protocol; the tool writes it to the run metadata, the summary and the checkpoint.

- search, the default and the only behavior before v3.2.0: the query goes to the OpenAlex search parameter, which matches the title, the abstract and, for works whose full text OpenAlex has indexed, the full text.
- title_and_abstract: the query goes to the title_and_abstract.search filter, which matches the title and the abstract only.

### I.1. Effect on the result set

A query that also matches full text returns many works that mention its terms only in passing, for example in a related-work section. In a live check on 27 October 2026, the query below, limited to 2010-2026 and to works with an abstract, matched 3,124 works with search and 228 with title_and_abstract. All 228 works contain the terms in their title or abstract, while 15 of the first 100 works returned by search do not mention malware there.

```text
python literature_pipeline.py run --query 'malware AND ("concept drift" OR "temporal drift" OR "dataset shift")' --search-field title_and_abstract --require-abstract --from-year 2010 --to-year 2026 --output drift
```

The narrower field also misses relevant works whose titles and abstracts use other words. Before a large collection, check the choice on papers you already know to be relevant (for example the seed papers of a review): count how many of them each query returns with each search field.

### I.2. Rules for title_and_abstract

- The query is sent as a filter value, in which a comma separates filters and | means OR between values. A query that contains , or | is therefore refused before any SCImago or OpenAlex request; join the terms with OR instead. With search, both characters are allowed as before.
- The query syntax is the same as with search: AND, OR and NOT in capitals, quoted phrases and parentheses. OpenAlex treats a hyphen as a word break, so "concept drift" also matches concept-drift.
- The default comes from run.search_field in config.json; --search-field on the command line overrides it.
- The checkpoint remembers the search field. Resuming with a different field stops with "Checkpoint parameters changed"; use --no-resume or a new --output prefix. Checkpoints written by earlier versions resume unchanged with the default field.
