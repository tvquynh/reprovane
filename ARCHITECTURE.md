# Reprovane architecture

Reprovane keeps evidence from each review stage separate so a later decision can be
traced back to its inputs. Begin with [Quick Start](QUICKSTART.md) and [User Guide](USER_GUIDE.md);
this page is the technical map, not a prerequisite for a first project.

Product version is **2.0.1**, from `reprovane/_version.py`. Database schema remains
**13**. A software version is not a database-format version.

The flow is project/revision -> acquisition/snapshot/provider record -> typed
observations -> publication/venue resolution -> discovery -> frontier/search
validation -> human screening/adjudication -> immutable corpus -> open-access
full text. Each stage preserves evidence and references to earlier stages.
Finding a record does not grant authority to include a study.

## Technical contracts

- [Persistence](docs/architecture/persistence_foundation.md)
- [Typed observations](docs/architecture/typed_observations.md)
- [Publication identity](docs/architecture/publication_identity_resolution.md)
- [Venue resolution](docs/architecture/venue_resolution_scimago.md)
- [Provider intelligence](docs/architecture/scopus_wos_venue_intelligence.md)
- [Acquisition](docs/architecture/openalex_acquisition_migration.md)
- [Discovery](docs/architecture/discovery_provenance_citation_expansion.md)
- [Frontier/search validation](docs/architecture/frontier_policy_search_validation.md)
- [Screening](docs/architecture/screening_separation.md)
- [Corpus](docs/architecture/immutable_corpus_release.md)
- [Full text](docs/architecture/fulltext_acquisition.md)
- [Pinned mirror](docs/architecture/pinned_github_mirror_sync.md)

No fuzzy-title rule automatically merges ambiguous publications. SCImago, Scopus
and Web of Science semantics remain separate. Human reviewers control screening;
AI/OCR (artificial intelligence/optical character recognition) do not own inclusion.
Full-text failure cannot change corpus membership. Security, content-addressed
storage, version policy and migration bytes are unchanged by documentation work.

The legacy v4 adapter places historical evidence in a separate context; old machine
classifications do not become modern human screening decisions or corpus members.
The [current migration guide](MIGRATION_FROM_SCHOLARFLOW_1.md) differs from the
[historical transition record](MIGRATION_TO_SCHOLARFLOW.md). `db/litintel.sqlite`
and `.litintel-writer.lock` remain compatibility locators. [Operations](OPERATIONS.md)
describes snapshots, backup, upgrade and filesystem limits.
