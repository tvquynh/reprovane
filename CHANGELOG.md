# Changelog

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
