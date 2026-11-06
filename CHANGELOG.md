# Changelog

## Unreleased

- Install from the GitHub repository (`main` branch) into a virtual environment; README, Quick Start and Security now describe that route instead of PyPI.
- Add unified PDF acquisition (`reprovane pdf`): import 4.1.0, Reprovane, OpenAlex and identifier inputs; freeze exact-identifier work identity and candidate plans; download open-access PDFs politely (robots.txt, per-host pacing, Retry-After cooldowns, persistent blocked hosts, SSRF guard); resume after interruption; export legacy-style reports with offline verification.
- Add manual PDF ingestion for files and folders with user mappings and a reconciliation queue; for sealed review runs manual PDFs stay supplementary and never become primary evidence.
- Import existing 4.1.0 `fetch-oa` output folders read-only.
- With OpenAlex lookups enabled, look up publications known only by DOI through an exact-DOI OpenAlex query, and fill empty title, DOI and year columns of manual-download lists from provider responses.
- `reprovane pdf review acquire` applies robots.txt, per-host pacing, Retry-After cooldowns and blocked hosts to Stage11B review acquisition, counting robots.txt requests against the run's frozen budget; `reprovane fulltext acquire` is unchanged.
- Never merge rows without identifiers on equal titles, never link a manual PDF through a key copied from an invalid identifier field, and send contradictory mapping rows to reconciliation.
- Keep 4.1.0 `fetch-oa` failures as history on legacy import, retry provider lookups that failed temporarily (including DNS failures), honour HTTP-date and long `Retry-After` values and long `Crawl-delay` values without shortening them, enforce the artifact budget before storing, and let `download --runtime-urls-from` supply signed URLs from the user's own input file without storing them.
- Never store remote page text, response-header text or signed-URL values in host-block reasons or request records; re-import own exports with URL identity and open-access flags; record every `Retry-After`, including provider lookups and robots.txt redirect hops, so later requests never ask earlier; block a host whose `Retry-After` repeats a private value or exceeds `max_cooldown_seconds`.
- Database schema 14 (migration 0014); migrations 0001-0013 are unchanged. A schema-14 workspace needs this or a later version, so the next release is a minor version.

## 2.0.1

- Fix documentation links in the package description shown on PyPI.
- Make the public documentation English-only and add a beginner-friendly setup guide.
- Generalize the secure release workflow for maintenance versions.

## 2.0.0

First public Reprovane release, bringing reproducible literature discovery, human screening, and evidence management into one review workflow.

- Collect literature from OpenAlex and expand searches through citations.
- Keep venue evidence and human review decisions traceable.
- Freeze a review corpus, organize eligible open-access full text, and track unresolved items in a manual queue.
- Verify workspace and export integrity.

Breaking package-name change: the distribution, Python import and CLI are now `reprovane`. Users of ScholarFlow 1.x can follow [the migration guide](MIGRATION_FROM_SCHOLARFLOW_1.md).

## 1.0.0

ScholarFlow 1.0.0 promotes the validated 1.0.0-rc1 architecture without scientific-semantic changes.

- Reproducible acquisition, typed observations and conservative publication/venue identity resolution.
- Separate provider intelligence, citation discovery, bounded frontier and search validation.
- Human screening/adjudication, immutable corpus releases and policy-bound OA full-text acquisition with a manual queue.
- Hash/provenance verification and copy-on-write predecessor migration.
- Product version becomes 1.0.0; database schema remains 13 and migrations 0001–0013 are unchanged. Stable tag/release publication is a separate reviewed step.

## 1.0.0-rc1

Initial ScholarFlow release candidate.

- Reproducible acquisition and immutable provider evidence/typed observations.
- Conservative publication/venue identity and separate provider intelligence.
- Citation discovery, bounded frontier and search validation.
- Human screening and conflict adjudication, immutable corpus releases.
- Policy-bound OA acquisition, verified PDF artifacts and manual queue.
- Copy-on-write legacy migration, offline verification/doctor, OS locks and backup-first upgrade.
- Canonical scholarflow namespace, one scholarflow console entry point and clean wheel/sdist packaging.

ScholarFlow succeeds openalex-literature-intelligence; historical lineage is in PROVENANCE.md. Product version1 does not reset schema13. Nothing is tagged or published by bootstrap preparation.
