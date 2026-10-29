# Release notes - OpenAlex Literature Intelligence

- **Version:** 4.1.0
- **Release date:** 2026-10-28
- **Previous stable release:** 3.0.5 (2026-10-25)

OpenAlex Literature Intelligence finds literature in OpenAlex and enriches each venue with historical and latest SCImago data (SJR, quartiles, categories, estimated APC). It then screens a main corpus and keeps a full audit trail. See `README.md` for usage and `CHANGELOG.md` for every change.

## New in 4.1.0: admitting listed venues and a preprint list
- `--admit-tier A --admit-tier B` admits papers of the venues your `--venue-tiers` list puts in those tiers, even when the SCImago status, source-type or quartile filters would exclude them, so strong conferences absent from SCImago, or present without a quartile, can enter the main corpus. The APC, DOAJ, CWTS Core and retraction filters still apply, and `main_waived_reasons` records what was set aside.
- `config.json` can hold the defaults: `screening.venue_tiers` and `screening.admit_tiers`; `--no-admit-tiers` turns admission off for a journal-only view.
- `<output>_preprints.csv` lists the preprints and repository records of each run, most cited first, for manual screening; preprints are not admitted wholesale.

## Changed in 4.0.0: default quartile basis
- `--quartile` without `--quartile-basis` now screens by the venue's quartile in the latest SCImago snapshot (`latest`) instead of the quartile of the publication year (`publication`), so papers newer than the SCImago data are no longer excluded by accident.
- With the latest basis, a 2026 paper is judged by its venue's 2025 quartile, which is not a 2026 quartile, older papers are also judged by their venue's 2025 quartile, and sources found only in earlier snapshots (`HISTORICAL_ONLY`) fail a quartile filter.
- To keep the 3.x behavior, pass `--quartile-basis publication`. The library functions `apply_main_policy` and `quartile_filter_diagnostics` have the same new default.

## New in 3.3.0: papers newer than the SCImago data
- With `--quartile` and `--quartile-basis publication` (then the default basis), a paper published after the last SCImago year has no publication-year quartile and fails the filter, as before. When its source is in the latest snapshot, its exclusion reasons now read `QUARTILE_UNAVAILABLE; QUARTILE_PUBLICATION_YEAR_NOT_AVAILABLE`.
- `run` and `enrich` print a warning with the number of such papers, and `summary.json` always contains `quartile_filter_diagnostics`.
- The main corpus does not change, and the latest quartile is never used as a publication-year quartile; `--quartile-basis latest` screens such papers by the latest available snapshot.

## New in 3.2.0: search field
- `run --search-field title_and_abstract` (or `run.search_field` in `config.json`) matches queries against titles and abstracts only, through the OpenAlex `title_and_abstract.search` filter. The default, `search`, keeps the earlier behavior, which also matches full text where OpenAlex has it.
- In a live check, one query matched 3,124 works with `search` and 228 with `title_and_abstract`.
- A `title_and_abstract` query may not contain `,` or `|`; such a query is refused before any request. The field is recorded in the run metadata, the summary and the checkpoint.

## Fixed in 3.1.1
- `fetch-oa` no longer pauses doi.org for the whole run when one DOI link leads to a publisher's bot check. The publisher is blocked as before; later DOI links, including those of other publishers, are still tried.

## New in 3.1.0: quality signals and venue tiers
- `run` keeps the OpenAlex retraction flag (`is_retracted`) and the field-normalized citation measures (`fwci`, `citation_normalized_percentile` with top-1% and top-10% flags) of every work, at no extra request.
- `--exclude-retracted` removes retracted works from the main corpus, with reason `RETRACTED`.
- `--venue-tiers <file>` labels each paper with the tier of its venue from a list you maintain (by OpenAlex source ID, ISSN, whole source name or a phrase in it). It adds `venue_tier`, `venue_tier_venue` and `venue_tier_match`, filters nothing, and records the list's SHA-256 in the run metadata. See `examples/venue_tiers_example.tsv`.
- Without these options, outputs are byte-identical to 3.0.5 apart from three new counts in `summary.json`.

## New in 3.1.0: open-access full text
- `fetch-oa --input <corpus file>` downloads the PDFs that OpenAlex lists as open access, sequentially, with a per-host delay, the tool's User-Agent and robots.txt respected.
- A host that answers with a bot check (challenge or CAPTCHA page, HTTP 403, `tdm-reservation: 1`) is never requested again; it is recorded in `blocked_hosts.json` at once. HTTP 429 and busy servers are only paused for the current run; DOI resolvers are never blocked, and are paused only when they answer with a bot check themselves.
- Everything not downloaded goes to `manual_download.csv`, `.html` and `.ris` with the reason and the file name to use; PDFs saved by hand in `pdf_manual/` are registered on the next run.
- `fetch-oa` changes nothing in discovery, enrichment or screening.

## Major fixes and improvements (3.0.3 -> 3.0.5)
- **ISSN conflict protection:**
  - a title match is never accepted when the source's ISSN contradicts the SCImago candidate's ISSN; the result is `UNMATCHED`, `TITLE_MATCH_ISSN_CONFLICT`, with the candidate listed for review;
  - the ISSN always wins over the title;
  - `&` in a title now matches `and`.
- **Historical source matching:**
  - `HISTORICAL_ONLY` covers every earlier SCImago snapshot, including 2026 papers and years in which a source is missing;
  - SCImago "(discontinued)" title labels are understood;
  - a source still present in the latest snapshot is never called historical-only.
- **Journal-only default screening:**
  - the main corpus defaults to journal + `ACTIVE_LATEST`;
  - `--include-historical-only` admits only `HISTORICAL_ONLY` (no unmatched or ambiguous rows);
  - `--all-source-types` and `--source-type` override the journal-only default.
- **config.json support:**
  - a private `config.json` holds the OpenAlex key, e-mail and defaults; precedence is CLI > config.json > built-in default;
  - `config.example.json` is the public template;
  - `run` stops with a clear message when no key is configured;
  - no personal e-mail is built into the code.
- **API key redaction:**
  - the key never appears in error messages or tracebacks;
  - run metadata records only `openalex_api_key_configured` and `openalex_api_key_source`.
- **Improved audit reporting:**
  - per-source audit aggregated over all papers (`MIXED`, `paper_status_counts`);
  - `match_diagnostic` / `issn_conflict_candidates`;
  - quartile `-` reported as not assigned;
  - exact relevant-category matching;
  - `run_metadata.json` for `enrich` as well;
  - re-enriching an old output gives the same result as enriching the raw file.
- **Improved cache/checksum verification:**
  - every SCImago file is SHA-256 verified; corrupted files are re-downloaded online and refused offline;
  - a missing remote file fails fast with a clear message;
  - unsafe file names in the manifest are rejected.

## Upgrade notes
- **Create `config.json`:** `Copy-Item config.example.json config.json`, then set `"api_key"`. The `OPENALEX_API_KEY` environment variable still works as a fallback but is deprecated.
- **E-mail:** the built-in OpenAlex `mailto` address was removed. Set `"email"` in `config.json` if you want one sent.
- **Renamed metadata fields:** `openalex_api_key_configured`, `openalex_api_key_source`, `openalex_api_key_stored_in_output`.
- **SCImago cache:** with `config.example.json` the cache lives in `cache/scimago`; without a config it stays `data/scimago`. The first `sync-scimago` into a new folder downloads about 180 MB.
- **Examples:** the example query file and scripts are now in `examples/`.

## Known limitations
1. OpenAlex search results can change over time, in both content and ranking order.
2. The SCImago mirror currently covers 2010-2025.
3. 2026 publications use the latest available (2025) metadata, marked `LATEST_AVAILABLE`, not a "2026 quartile".
4. Conference coverage in SCImago varies substantially across years. A conference missing from one year is not evidence of Scopus discontinuation.
5. SCImago presence is not identical to official current Scopus status. SCImago absence is not evidence that a venue is predatory.
6. `fwci` and `citation_normalized_percentile` are computed by OpenAlex at query time and change as citations accrue; a venue tier reflects only the list you give.
7. APC values are 2025 snapshot estimates, not guaranteed publisher prices.
8. Strict ISSN conflict protection may prefer false negatives over false-positive venue matches; review `TITLE_MATCH_ISSN_CONFLICT` rows in `source_audit.csv`.
9. The SCImago mirror repeats the current H-index in every year, so `h_index_at_publication` is not a historical H-index.
10. The mirror's server `manifest.json` still lists only four matching methods. This is documentation only; the client does not read it.
11. Venue tiers, `--admit-tier` and the preprint list use the source of the primary location that OpenAlex reports. When OpenAlex gives a repository copy as the primary location of a published paper, the paper is listed as a preprint and is not admitted by tier.
