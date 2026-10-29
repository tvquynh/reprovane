# ScholarFlow architecture

Product version: see scholarflow/_version.py. The candidate is 1.0.0rc1.

ReviewProject/ProjectRevision -> provider acquisition/snapshots/records -> immutable typed observations -> Stage03 publication and Stage04 venue resolution -> Stage08 discovery -> Stage09 frontier/search validation -> Stage10 human screening -> Stage11 immutable corpus -> Stage11B full-text acquisition. Stage12 adds only legacy adapters and operational safety/packaging.

Legacy v4 main/excluded/OA/venue output is historical evidence. Migration delegates publication identity to Stage03, never writes screening/corpus/fulltext decisions. Exact API request provenance is not fabricated. Modern SCImago ranking/presence, Scopus membership/status and WoS collection claims remain separate. AI, fuzzy identity, OCR and automatic screening do not own pre-freeze authority.

Detailed accepted architecture documents live under docs/architecture/. See in particular immutable_corpus_release.md and fulltext_acquisition.md. MIGRATION_TO_SCHOLARFLOW.md describes migration ledger semantics; OPERATIONS.md describes CLI locks, read-only snapshots and backup-first upgrade. Runtime verification reuses accepted layer verifiers. Whole-workspace doctor can sample or fully verify sealed objects; portable exports validate self-consistency rather than DB provenance or legal copyright status.

ScholarFlow product version1 is independent of schema13. The namespace relocation changes no scientific policy or SQL migration.
