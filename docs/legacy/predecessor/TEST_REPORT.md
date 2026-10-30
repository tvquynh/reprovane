> Historical v4.1.0 reference only. Commands below require predecessor software; current users follow the [Reprovane Quick Start](https://github.com/tvquynh/reprovane/blob/main/QUICKSTART.md).

# Test report - OpenAlex Literature Intelligence v4.1.0

Release date: 2026-10-28. 4.1.0 adds `--admit-tier`, which admits the venues of chosen tiers in your list to the main corpus even when the venue filters would exclude them, and a list of preprints for manual screening. 4.0.0 changed the default `--quartile-basis` from `publication` to `latest`, so a quartile filter keeps the newest papers when their venue qualifies. 3.3.0 reported papers published after the last SCImago year precisely when a publication-basis quartile filter excludes them: an extra exclusion reason, a console warning and summary diagnostics. 3.2.0 added `run --search-field` (match queries against titles and abstracts only). 3.1.1 fixed one `fetch-oa` defect found in real use (doi.org paused after a redirect to a bot check). 3.1.0 added quality signals with user-defined venue tiers, and the `fetch-oa` command (open-access full text). The automated tests below were rerun with the 4.1.0 code.

## Automated test suite (shipped in `tests/`)
Command: `python -m unittest discover -s tests -v`, run in a brand-new virtual environment on a clean copy of the repository tree.

| Item | Result |
|---|---|
| Tool version | 4.1.0 (`python literature_pipeline.py --version`) |
| Python | 3.11.9 |
| OS | Windows 11 Pro, build 26100 |
| Dependencies | requests 2.34.2, urllib3 2.8.0, idna 3.20, charset-normalizer 3.5.2, certifi 2026.10.23 |
| Total tests | 85 (52 from 3.0.5, 12 for `fetch-oa`, 7 for quality signals and venue tiers, 4 for the search field, 6 for papers newer than the SCImago data, 4 for venue admission and preprints) |
| Passed | 85 |
| Failed | 0 |
| Skipped | 0 |
| Duration | 32.3 s |

The tests need no network and no API key: they use a synthetic SCImago repository and local 127.0.0.1 servers with fake keys and simulated bot checks.

Test groups:
- **Matching:** ISSN conflict, title fallback, ISSN priority, `&`/`and`, ambiguity, invalid ISSN.
- **Historical sources:** discontinued titles, any earlier snapshot, Sourceid still in latest.
- **Configuration:** with and without `config.json`, precedence, keyless `run`, invalid configs.
- **Search field (new in 3.2.0):**
  - with the default, `run` sends the `search` parameter with the same parameters, in the same order, as 3.1.1;
  - with `title_and_abstract`, no `search` parameter is sent and the query is the last filter, `title_and_abstract.search:<query>`, after the year and abstract filters, on every page;
  - a `title_and_abstract` query containing `,` or `|` is refused before any request and before any file is written, also through the CLI, where no SCImago cache is created; both characters are still allowed with `search`;
  - the field enters the checkpoint signature only when it is not the default, so resuming with another field stops and checkpoints of earlier versions still resume;
  - precedence CLI > `config.json` > default, an empty value means the default, an unknown value in `config.json` is refused without echoing it, and an end-to-end CLI `run` records the field in the metadata and the summary.
- **Papers newer than the SCImago data (new in 3.3.0):**
  - with a publication-basis quartile filter, a paper published after the last SCImago year in a source of the latest snapshot gets `QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE` next to `QUARTILE_UNAVAILABLE`, in both quartile modes, and its publication-year quartile stays empty;
  - the same paper passes with `--quartile-basis latest`, and a paper inside the SCImago years passes as before;
  - over mixed rows only those papers are counted; a source outside the latest snapshot, or a missing quartile inside the SCImago years, keeps `QUARTILE_UNAVAILABLE` alone;
  - `enrich` and `run` print the warning only with a publication-basis quartile filter and at least one affected paper, `summary.json` always has `quartile_filter_diagnostics`, and the run metadata holds the SCImago years and the quartile policy;
  - the default basis is `latest` (changed in 4.0.0): without `--quartile-basis`, the library functions, `enrich` and `run` keep a 2026 paper whose venue has a selected latest quartile, print no warning and record `latest` in the summary and the run metadata.
- **Venue admission and preprints (new in 4.1.0):**
  - `--admit-tier` waives only the SCImago status, source-type and quartile reasons of papers in admitted tiers and records them in `main_waived_reasons`; APC, DOAJ, CWTS Core and retraction reasons stay, other tiers and `AMBIGUOUS` are not admitted, and without the option no admission column appears;
  - `--admit-tier` without a list, an unknown tier and a reserved name stop `enrich` and `run` before any request, and no SCImago cache is created;
  - `screening.venue_tiers` (relative to `config.json`) and `screening.admit_tiers` give the defaults, `--admit-tier` and `--no-admit-tiers` override them, and values of the wrong type are refused without echoing them;
  - an end-to-end `enrich` admits a conference without a quartile and a venue absent from SCImago, writes the preprint list most cited first with the enriched columns (preprints are recognised by OpenAlex type and by repository source), and re-enriching without the option drops the admission columns.
- **Security:** API-key redaction, no personal e-mail in source.
- **Repository checksums:** cold, warm, corrupt, offline, tampered, 404, unsafe names.
- **Corpus policy:** journal-only, all source types, historical-only, order-independent audit, stable schema.
- **Ranking:** exact-year metrics, future years, quartile placeholder.
- **Resume/checkpoint:** stop-by-limit, resume, legacy checkpoint.
- **Dedup:** DOI/OpenAlex-ID aliases.
- **Quality signals and venue tiers (new):**
  - `is_retracted`, `fwci` and `citation_normalized_percentile` are taken from the OpenAlex work, and are empty when absent;
  - merging duplicates keeps a retraction reported by either copy and fills missing metrics, also for rows written by 3.0.5;
  - `--exclude-retracted` is opt-in, gives the reason `RETRACTED`, and is counted in the summary;
  - venue matching follows the order source ID, ISSN, whole name, phrase; the longest phrase wins, phrases match whole words, `&` equals `and`, and two venues at the same step give `AMBIGUOUS`;
  - every malformed list is refused with its line number, and CSV lists work;
  - `enrich` with a list writes the tier columns, the source audit, the summary counts and the list's SHA-256 in the metadata; re-enriching without a list drops the old tiers;
  - a bad list stops `run` before any SCImago or OpenAlex request.
- **Open-access full text (`fetch-oa`, new):**
  - a blocked host is never requested again, in the same run, through a redirect, or in later runs, and survives an interruption (Ctrl+C);
  - challenge pages and headers are detected, while paper titles about CAPTCHAs and scripts injected into ordinary pages are not;
  - robots.txt follows RFC 9309 (longest match, `*`, `$`, agent groups, Crawl-delay), redirects are followed, and an unreadable robots.txt pauses the host;
  - redirect chains into a bot check block the redirecting hosts, and `tdm-reservation` on a redirect is respected;
  - a resolver that only redirected to a bot check stays usable for later DOI links, including links to other publishers, while a resolver that serves a bot check itself is paused for the run (new in 3.1.1);
  - HTTP 429 and 5xx with Retry-After pause the host for the run;
  - cut-off and truncated bodies and a duplicated Content-Length do not crash the run or count as downloads;
  - retries are decided per link, `--limit` and `--retry-failed` work, closed-access rows with a link are never requested, and hand-downloaded PDFs are registered;
  - the HTML and RIS manual lists are escaped, rows without an identifier are kept, and the output is safe on a non-UTF-8 console;
  - header names in lower case (`location`, `retry-after`) are handled, and every request appears in `fetch_log.csv`;
  - pacing, the User-Agent, and the CLI option checks are covered.

Before release, an independent review of the first `fetch-oa` draft reproduced nine defects with local servers. All are fixed in 3.1.0, and each has a test above. The new venue-tier tests were also checked against deliberate faults (shortest instead of longest phrase, matching inside words, stale tiers kept on re-enrichment, a retraction lost on merge): each fault makes a test fail. The search-field tests were checked the same way against five faults (the field ignored when the request is built, the field left out of the checkpoint signature, commas accepted, any `config.json` value accepted, the CLI check removed): each makes at least one test fail. The tests for papers newer than the SCImago data were checked against six faults (the new reason never added, added with the latest basis, added for every future-year paper, the latest quartile copied into the publication-year columns, the warning printed with a count of 0, the generic reason dropped): each makes at least one test fail. Reverting the default basis to `publication`, in the CLI or in the library, also makes a test fail. The venue-admission and preprint tests were checked against ten faults (every reason waived, admission ignored, admission columns always written, the reserved-name and missing-list checks removed, the preprint order reversed, either preprint criterion dropped, list values in `config.json` not checked, `--no-admit-tiers` ignored): each makes at least one test fail.

## Reference-corpus regression (100 OpenAlex records, real verified SCImago 2010-2025 mirror)
First run with the 3.1.0 code (not shipped: needs the reference corpus), and rerun with the 3.2.0 and 3.3.0 code on 27 October 2026 and with the 4.0.0 and 4.1.0 code on 28 October 2026, with the same results: the five CSV/JSONL files listed below are byte-identical to the earlier 3.1.0 outputs in both modes, the invariant checker again passes 20/20 in both modes, and `inspect-source` gives the same answer.

| Check | Result |
|---|---|
| journal-only main / excluded | 25 / 75 |
| `--all-source-types` main / excluded | 29 / 71 |
| papers ACTIVE_LATEST / HISTORICAL_ONLY / UNMATCHED | 29 / 1 / 70 |
| unique sources ACTIVE_LATEST / HISTORICAL_ONLY / UNMATCHED | 21 / 1 / 51 |
| independent invariant checker (`tools/check_reference_invariants.py`) | 20/20 PASS (default), 20/20 PASS (all source types) |
| `inspect-source --title "IEEE Access" --issn 9999-9999` | UNMATCHED, `TITLE_MATCH_ISSN_CONFLICT` |

These match 3.0.5 Stable exactly. Run without the new options, 3.1.0 and 3.0.5 write byte-identical `_enriched.csv`, `_enriched.jsonl`, `_main.csv`, `_excluded.csv` and `_source_audit.csv` files in both modes; `_summary.json` differs only in `tool_version` and three new counts. The reference records were retrieved with 3.0.2, so they carry no retraction flag or citation measures, and the tool reports this. These runs use no quartile filter, so with 4.0.0 only the basis recorded in `quartile_filter_diagnostics` changes, to the new default `latest`. With 4.1.0, `_summary.json` also gains the `preprints` counts (13 of the 100 records) and `_preprints.csv` is written; the five files listed above do not change.

With `--venue-tiers examples/venue_tiers_example.tsv`, one of the 100 papers is labelled (B, Computers & Security, matched by ISSN) and 99 are `UNLISTED`. The example list names none of the venues of the other 99 papers.

## Real-data check of papers newer than the SCImago data (2026-10-27)
`enrich --offline --all-source-types --quartile Q1 --quartile Q2` on the 100 records of a live 2010-2026 OpenAlex query, with each quartile basis, compared with 3.2.0 on the same input.

| | publication basis | latest basis |
|---|---:|---:|
| papers published in 2026 | 36 | 36 |
| of these, `ranking_basis = LATEST_AVAILABLE` | 12 | 12 |
| main corpus | 13 | 22 |
| `future_year_papers_without_publication_quartile` | 12 | 0 |
| warning printed | yes | no |

With the publication basis, `_main.csv` and `_source_audit.csv` are byte-identical to 3.2.0, and in `_excluded.csv` only the reasons of the 12 papers change, from `QUARTILE_UNAVAILABLE` to `QUARTILE_UNAVAILABLE; QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE`. With the latest basis, all files are identical to 3.2.0. With 4.0.0 the same command without `--quartile-basis` uses the latest basis and gives the latest-basis column: 22 papers in the main corpus and no warning.

## Real-data check of venue admission and preprints (2026-10-28)
`enrich --offline` with a 52-venue list (12 venues in tier A, 22 in tier B, 18 in tier C) and six relevant categories, on two inputs, with and without `--admit-tier A --admit-tier B`.

| | 100 papers of a live 2010-2026 query | 73 seed papers of a review |
|---|---:|---:|
| main corpus without admission | 25 | 20 |
| main corpus with tiers A and B admitted | 38 | 33 |
| admitted by waiving venue filters (A / B) | 4 / 9 | 9 / 4 |
| preprints listed | 36 | 27 |
| of these, top 10% by citations | 0 | 3 |

All admitted papers come from conferences or workshops, such as NDSS, ACM CCS, IEEE S&P, USENIX Security, ICLR, ACSAC and ACM AISec; the waived reasons were `SOURCE_TYPE_FILTER`, alone or with `SOURCE_STATUS_UNMATCHED`. Three KDD papers stayed unlisted because the list's phrase contains a word that the OpenAlex source name lacks, which is why the names in a list should be checked against `_source_audit.csv`.

## Live check of `--search-field` (2026-10-27)
One query, `malware AND ("concept drift" OR "temporal drift" OR "dataset shift")`, publication years 2010-2026, abstract required, all source types, run with each search field. Four OpenAlex requests in total; cost reported by OpenAlex 0.004 USD.

| Check | `search` | `title_and_abstract` |
|---|---:|---:|
| works matched (`meta.count`) | 3,124 | 228 |
| same count measured earlier that day | 3,124 (3.1.0 `run`) | 228 (separate measurement script) |
| records retrieved | 100 (`--max-per-query 100`) | 228 (all; 100, then resumed with a larger limit) |
| records with "malware" in title or abstract | 85 of 100 | 228 of 228 |
| records with "malware" and one of the three phrases in title or abstract | 85 of 100 | 228 of 228 |

In 5 of the 228 `title_and_abstract` records the phrase is written with a hyphen (concept-drift, concept-drifting): OpenAlex treats the hyphen as a word break. Resuming the `title_and_abstract` run from its checkpoint retrieved the other 128 records and exhausted the query, and `search_field` was recorded in the run metadata and the summary of both runs.

## Live pilot of `fetch-oa` (2026-10-27)
Input: the 20 records (`_enriched.csv`, all source types) of a live OpenAlex query `malware detection machine learning`, 2020-2026, abstracts required, limited to 20 records. Default settings (`--delay 2`), Windows 11. 22 requests in 42 s.

| Outcome | Papers | What happened |
|---|---:|---|
| downloaded | 1 | doi.org redirected to the publisher's PDF (454 KB, complete) |
| NOT_OPEN_ACCESS | 9 | closed access in OpenAlex; no request sent |
| HOST_BLOCKED | 6 | MDPI (4 papers) answered robots.txt with an "Access Denied" page (HTTP 403) and ScienceDirect (2) with `tdm-reservation: 1`; each host received exactly one request, and the other papers on it none |
| NOT_PDF | 3 | the OA link led to an article or record page (IEEE Xplore, Zenodo, a repository download page) instead of a PDF |
| HTTP_404 | 1 | the OA link reported by OpenAlex no longer exists |

The first pilot run found one defect that the local tests had missed: redirects whose `location` header is written in lower case (doi.org) were not followed. It is fixed, a test was added, and the table above is from the run after the fix.

## First real use of `fetch-oa` (2026-10-27)
On a thesis literature corpus (73 seed papers and a 100-paper test query), the first 3.1.0 run skipped every DOI link after the first one that led to a publisher's bot check (ACM, Elsevier): the redirect had paused doi.org for the rest of the run, and 21 links ended as `HOST_PAUSED` (17 in the test query, 4 among the seeds). Rerunning `fetch-oa` tried them again, because the blocked publishers were by then in `blocked_hosts.json`; 3 of them gave a PDF. 3.1.1 fixes the cause, and the new test reproduces the case: it fails with the 3.1.0 code and passes with 3.1.1.

The fix was then checked on the same 100 test papers: one 3.1.1 run into a new output folder (so no host was known to be blocked beforehand) sent 125 requests in about 10 minutes, mostly spent waiting for slow servers.

| | 3.1.0, first run | 3.1.0, after three runs | 3.1.1, one run |
|---|---:|---:|---:|
| `HOST_PAUSED` | 17 | 0 | 0 |
| PDFs downloaded | 35 | 38 | 38 |
| requests sent | 83 | 138 (three runs) | 125 |

doi.org was never paused: all 20 requests to it, including one for robots.txt, returned a redirect. The final status of all 100 papers is the same as after the three 3.1.0 runs, and the 38 PDFs have the same SHA-256. The only host paused was www.osti.gov, whose robots.txt could not be read, as with 3.1.0.
