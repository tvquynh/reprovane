# Typed observations (Stage 02)

A typed observation is a normalized claim extracted from exactly one immutable
ProviderRecord. It is neither a canonical scholarly fact nor evidence that two
provider records identify the same publication or venue. Normalization here means
accepting and validating a caller's normalized values, not parsing live providers
or choosing winners among disagreements.

## Evidence and initialization

The evidence chain is observation.provider_record_id -> provider_record.snapshot_id
-> ProviderSnapshot -> attached RawArtifact. Stage01 requires a record's artifact
to be attached to that same snapshot. Every observation table has its own INSERT
trigger joining the record and snapshot and requiring sealed_at_utc IS NOT NULL.
Missing records and OPEN evidence are rejected even by raw SQL. A normalized claim
may be added after sealing; raw acquisition evidence itself remains unchanged.
This layer checks database seal state, not every raw byte on every insertion;
Foundation.verify_provider_snapshot detects later external byte tampering.

```python
from pathlib import Path
from reprovane.core.db import Database
from reprovane.observations import ObservationStore, NormalizationProvenance

with Database(Path("workspace")) as db:
    observations = ObservationStore(db)  # explicitly upgrades/validates schema v3
    normalizer = NormalizationProvenance("example-normalizer", "1.0", "1")
    # record_id must identify an existing record in a SEALED snapshot.
    # observations.add_publication_metadata_observation(
    #     record_id, normalizer, title="Example", publication_year=2024,
    #     field_provenance_json='{"title":"$.title"}')
```

The accepted66 Stage01 tests hardcode a fresh schema with only migration1 and nine
tables. Applying migration2/3 automatically on every bare Database would contradict
that unchanged contract. This concrete blocker is resolved by a minimal explicit
target: Database defaults to target_schema_version=1; callers may request3, and
ObservationStore now requests3 itself (FIX01). Existing v2/v3 workspaces reopen without downgrading even with
the lower default: all applied checksums are verified, no downgrade occurs. The
migrator validates all discovered historical files before applying pending versions
up to the requested target. ObservationStore must be created on an idle connection
before a caller's data transaction. No test files from prior stages were edited.

## Nine typed claim tables

* publication_metadata_observation: bibliographic fields including title, abstract,
  type, year/date, language, volume/issue/pages/article number/publisher. At least
  one field is non-NULL. Date/year disagreement is permitted. No missing fields are
  inferred and no closed global publication-type vocabulary is imposed.
* publication_identifier_observation: original and normalized identifier strings,
  open namespace token and optional source field address. Identical DOI values may
  appear in many records, or repeat in one record, without creating identity.
* venue_observation: source description with at least one of title, publisher,
  source type or country. No canonical venue ID or provider indexing status here.
* venue_identifier_observation: original/normalized venue identifier claims, not
  ISSN relationships or venue resolution.
* venue_membership_observation: provider-namespaced collection/status claims with
  optional observed date/year coverage. Scopus, WoS and SCImago are distinct. There
  is no generic indexed boolean; namespace=scimago/status=ACTIVE_LATEST is only
  that provider claim. Current status never synthesizes historical coverage.
* venue_metric_observation: provider namespace, metric code, optional year/category,
  exactly one typed TEXT/INTEGER/REAL value and optional unit. There is no automatic
  best-quartile, best-source, ranking comparison or universal metric meaning.
* citation_edge_observation: unresolved source/target namespace+identifier pairs,
  relation CITES only. Scholarly citation evidence is not a DiscoveryEdge and has
  no traversal/run/frontier fields. PREPRINT_OF, EXTENDED_VERSION_OF, CORRECTION_OF
  and RETRACTION_OF are not citation relations in this layer.
* integrity_event_observation: RETRACTION/CORRECTION/EXPRESSION_OF_CONCERN/WITHDRAWAL/
  REMOVAL/OTHER claim about an identifier, optionally paired notice identifier,
  date/status. No canonical is_retracted, integrity summary or automatic exclusion.
* publication_authorship_observation (FIX01): one provider-observed authorship
  position for one publication ProviderRecord, with display name, optional given/
  family names, provider-author identifier triple and ORCID pair. It is not a
  canonical person or global author-resolution decision. Collective/group names
  are allowed without person fields. Position is positive, 1-based provider order;
  no alphabetical reordering, corresponding/senior-author inference or name
  fingerprint is produced. Names/accents/case/spacing are preserved as supplied;
  given/family names are not inferred by splitting a display name.

The tables are deliberately domain-specific, not a universal Assertion/EAV store.
There is no generic confidence column: deterministic normalized provider claims
are not probabilities. Provider disagreement is retained rather than resolved.

## Normalization provenance and immutability

Each row has one provider_record_id; required normalizer_name, normalizer_version
and observation_schema_version; optional lowercase64-hex normalization_config_sha256;
and UTC normalized_at_utc ending Z. NormalizationProvenance is a frozen dataclass;
the API generates the timestamp per insertion. Original/normalized identifier
strings are supplied explicitly and preserved; no DOI or ISSN resolver runs.
Field provenance JSON is an object of source field addresses, not another assertion
store. It is validated and preserved as text without semantic rewriting.

IDs are UUID4 hex prefixed pmo_, pio_, vo_, vio_, vmo_, vmeo_, ceo_, ieo_, pao_. SQL
CHECKs enforce the table-specific prefix. UPDATE and DELETE triggers reject any
mutation; a duplicate-ID BEFORE INSERT guard also defeats INSERT OR REPLACE when
an external connection has recursive_triggers OFF. All nine tables use the same
four-guard pattern. No global identifier/value uniqueness exists. New normalizer
versions append new observation sets for the same ProviderRecord.

## Validation boundary

Service validation rejects empty required/optional supplied text, unsafe or
nonlowercase namespace tokens ([a-z][a-z0-9_.-]*), invalid hashes, bool/float years,
out-of-range years, invalid real/64-bit integer metric values, mismatched typed
value columns, unpaired notice IDs and reversed coverage years. Optional fields
stay NULL; there is no implicit trimming, casefolding, year inference or history
projection. Namespace vocabulary is extensible. Codes are provider-specific text.

Python validates complete Gregorian YYYY-MM-DD dates using date.fromisoformat
after a strict lexical check. Pure SQL validates date shape, nonzero four-digit
year, month1..12/day1..31; it does not independently reject February30. This is
the documented calendar boundary permitted by Stage02. SQL also enforces required
fields, sealed evidence, JSON objects, config hash, integer years/coverage ordering,
metric typed columns, notice pairing, relation/event types and immutability.
SQLite affinity may coerce numeric-looking direct SQL strings; Python is stricter
about input types. Stage01 connection pragmas and explicit transactions remain.

Methods return detached dictionaries. Nine add_<typed> methods, corresponding
list_<typed> methods, get_observation(typed_id), and
list_observations_for_provider_record(id) expose fixed whitelisted tables only.
Service writes compose inside caller transactions, otherwise commit their own.
Errors use existing ValidationError/ImmutabilityError/FoundationError categories
without echoing values or raw JSON. Reads order by timestamp and ID; this is
deterministic storage order, not scholarly precedence.

Indexes cover provider_record on each table, namespace/value on identifiers,
membership namespace, metric namespace/code/year, citation source/target identifiers
and integrity subject identifiers. No full-text search, graph index or canonical
view is built. Normalized records are local SQLite rows, with no network calls.

FIX01 authorship adds three non-UNIQUE lookup indexes: provider_record_id plus
author_position; provider-author namespace plus normalized ID; normalized ORCID.
Provider-author namespace/original/normalized must be all NULL or all non-NULL.
ORCID original/normalized must be paired; normalized ORCID has lexical form
0000-0000-0000-000X (last character digit or uppercase X). Service and SQL check
shape only, never checksum semantics. Original provider claims remain traceable.
Same name, ORCID, provider ID and position may recur across records and normalizer
versions without overwrite or identity inference. add_publication_authorship_observation
accepts explicit fields and standard NormalizationProvenance; its typed list
filters by provider_record_id and orders author_position, normalized_at_utc, then
observation_id. Generic get/list APIs also include pao_. This order does not pick
a preferred normalizer version. SQL0001/0002 remain unchanged; append-only0003
adds the authorship table, and observation-enabled schema/ledger become3/[1,2,3].

## Disagreement examples and future consumers

Synthetic recordA can claim DOI10.example/one, title Example, year2024; recordB can
claim the same DOI, title Example revised, year2025. Two publication metadata rows
and two identifier rows survive, anchored to their own provider records. Similarly,
namespace=scopus/status=ACTIVE, namespace=wos/collection=SCIE and
metric_namespace=scimago/code=QUARTILE/category=Software/value=Q1 are three independent
claims, not a combined indexing decision. Conflicting integrity claims survive.

Stage03 may consume these claims as evidence for future entity/relation decisions;
it is not implemented. No PublicationEntity, VenueEntity, EntityMembership,
EntityResolutionDecision, matching/dedup, work-family inference or author/institution
resolution exists here. No acquisition/parser migration, graph traversal, screening,
release, full-text queue, generative AI, embedding, ORM or CLI integration is added.
Future code must append new observations rather than changing old normalizer output.

AuthorshipObservation preserves provider-observed authorship evidence while global
author entity resolution remains deferred. Stage03 may consume exact author
sequence, first/last listed author, provider IDs and ORCID alongside bibliographic
claims; this layer decides neither same author, same publication, duplicate
publication nor publication family. Future resolution decisions must still be
recorded explicitly in that later stage.

All Stage02 fixtures use synthetic identifiers/domains and offline bytes. This
API does not universally scrub arbitrary titles/abstracts/source paths/metadata:
callers must avoid credentials in supplied values. It makes no live-provider calls
and does not read user configuration, email, API keys, cookies or browser profiles.
