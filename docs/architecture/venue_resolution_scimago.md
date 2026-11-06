# Venue resolution and local SCImago migration (Stage04)

VenueEntity is a run-scoped identity projection over provider-local venue nodes.
It has no indexed/active/Q1 boolean. SCImago presence, Scopus membership, WoS
collections, metrics and screening policy are distinct. No current v4 command
uses this layer; no legacy source/test or historical migration is edited.

## Local-only operating model

Operational distribution is a caller-provided local checkout/export of
tvquynh/wosaide-journal-lists, requested branch control, directory SCIMAGO/.
Neither provider adapter nor venue resolver performs HTTP/GitHub/Git acquisition.
Stage05 will resolve a mutable branch to a commit, fetch all bytes at that same
commit, verify them and invoke this local layer. A full40-hex commit supplied by
the caller is recorded in mirror_commit_sha and the GitHub locator. This is a
caller-asserted association, not Stage04 network verification. Branch-only
provenance is explicitly marked nonimmutable. Git commit SHA and file SHA-256
are different concepts.

validate_local_package reads manifest.json and its checksum file; it never reads
repository_state.json as authority. Schema1 uses the inclusive manifest year
range and url_pattern. Schema2 requires unique sorted integer available_years,
latest=max(years), file_pattern and optional independently year-versioned Journal
Value snapshots. v1 latest must lie within its declared range. No production year,
annual count or total-file count is hardcoded. Optional manifest count checks are
computed from declared filenames. Unsupported schema versions, duplicate/unsafe
checksum entries, traversal/symlink escapes, missing files/hashes and mismatches
fail closed. The old manifest base_url is sanitized historical provenance only,
never an operational endpoint. Control-file hashes are pinned before placement;
source/control changes during import are detected.

import_scimago_source_snapshot(db, package_root, year, ...) and
import_scimago_apc_snapshot(...) consume only declared SHA-256-verified bytes.
iter_import_scimago_package imports every manifest-declared annual/APC snapshot,
one file at a time, so the same call discovers newly added years automatically.
Optional source_csv_path must equal the manifest-derived path. Delimiter/encoding
can be supplied and are recorded; UTF-8 BOM is supported. Repository/branch/path/
optional resolved commit and manifest/checksum raw artifacts are retained.

The importer puts exact bytes in the content-addressed store, validates the whole
file before any DB snapshot, then creates a scimago ProviderSnapshot with FILE
acquisition, source_list or journal_value dataset and year release. Each CSV row
becomes a ProviderRecord with native Sourceid and stable logical CSV row locator.
The snapshot seals BEFORE typed observations are inserted. All DB operations share
one transaction/savepoint; failures roll back the snapshot and claims. Filesystem
placement may leave safe unreferenced raw objects on failure, as in Stage01.
Parsing streams each file in passes; Foundation's per-file record return list is
materialized, but annual datasets are not all loaded into one giant data object.

## Schema profiles and observations

scimago_source_list_v1 requires Sourceid, Title, Type, Issn, SJR, SJR Best Quartile,
H index, Country, Region, Coverage, Categories, Areas, Rank, Open Access, Open
Access Diamond, Publisher. Repeated headers get suffixes; known duplicate
Publisher fallback is supported. Duplicate required semantics other than Publisher
fail closed. Unknown optional headers are permitted and recorded. Short rows are
padded deterministically; missing/malformed Sourceid fails, never silently skips.
IDs are nonempty safe provider tokens, not inferred from row position. Decimal
comma, blank/dash numeric absence and Q1-Q4 (case-normalized) are supported.

Title/type/publisher/Country are provider literals in VenueObservation. Country is
not derived from Region; the generic country_code field retains provider Country
text without inventing an ISO mapping. Field provenance points to CSV headers.
Identifiers include scimago_source_id and each ISSN, never an invented ISSN-L.
Malformed ISSN claims remain typed claims; venue evidence later validates checksum.
Membership scimago/PRESENT_IN_SNAPSHOT is emitted; OA and Diamond flags use separate
scimago_open_access namespaces, never doaj. Simple raw Coverage is retained in raw
evidence; bounds are not inferred in this profile.

SJR REAL, BEST_QUARTILE TEXT, H_INDEX_REPORTED INTEGER, RANK INTEGER and exact
category QUARTILE claims use the snapshot year. Dash means no quartile observation.
Categories are semicolon-delimited and only explicit category(Q1-Q4) tokens emit
quartiles; unranked categories and Areas remain raw evidence. H_INDEX_REPORTED
means reported in that row, not proven historical point-in-time H-index.

Journal Value is optional and separately dated, including older years than latest
SJR. The known shifted Publisher/OA/Diamond layout is corrected only under the
explicit matching journal-value header profile and row flags, recorded as
known_journal_value_shift_v1. Numeric APC yields APC_USD_ESTIMATE, REAL/USD. It is
an estimate, never official publisher price and never a screening decision.

## Venue evidence and provider nodes

VenueResolutionStore initializes schema5 (bootstrapping accepted lower features
without replacing an existing publication store's authorization functions).
Database/ObservationStore/ResolutionStore retain their1/3/4 feature defaults.
Inputs require sealed snapshots and freeze exact selected typed profile evidence.
Only venue descriptions, identifiers, membership and metric observations are read;
raw provider bytes are never parsed by venue resolution. Snapshot acquisition
metadata and provider namespace/native ID are allowed anchors.

Group only (provider semantic_namespace, nonblank native ID); no native ID means
one singleton record. ProviderNode ID is node_+first32 SHA256(canonical object
with namespace, nullable native ID and sorted member record IDs). Historical
title/ISSN changes union across records with the same native ID; they do not split
that provider-local node. This is provider-local identity, not global venue truth.
Nodes retain all titles, separate ISSN/eISSN/ISSN-L sets and union, types,
publishers, countries, releases and source observation IDs. Every input belongs
exactly once. Node generation is atomic and freezes inputs.

## Matching and ambiguity

issn_normalizer_v1 removes punctuation/spacing, uppercases X, requires seven
digits plus digit/X and validates the modulus11 check digit. Invalid IDs and their
observation IDs remain as warnings, not strong evidence. This intentionally
strengthens legacy shape-only matching. Title exact uses casefold/whitespace;
normalized adds NFKD/accent removal/&->and/alphanumeric spaces; status-normalized
only strips terminal (discontinued)/(ceased). No fuzzy utility or AI.

conservative_venue_v1 precedence: ISSN_EXACT (regular ISSN/eISSN), ISSNL_EXACT
(linking against any family member), TITLE_EXACT, TITLE_NORMALIZED,
TITLE_STATUS_NORMALIZED. Candidates operate on nodes, not annual rows. The strongest
matching method is primary; weaker methods cannot override it. All block sizes
are validated before pair expansion (default50); oversized blocks abort without
partial candidates. Block context retains sizes, node IDs and deterministic key
hashes. Primary-method blocks must be exactly two nodes, or automatic matching is
ambiguous. Even if one key is unique, another ambiguous block at that primary
method makes the pair fail closed.

Different nonempty native IDs in the same provider namespace block auto-SAME.
For title-primary pairs, disjoint nonempty valid ISSN unions give
TITLE_MATCH_ISSN_CONFLICT. Missing/invalid identifiers do not invent contradictions.
Valid ISSN primary matches tolerate different titles. Unique primary block with
no conflict auto-SAME (including unique-title fallback); otherwise AMBIGUOUS.
No auto-DISTINCT, arbitrary ranking or fuzzy auto-merge.

## Ledger, entities and lifecycle

Venue decisions are append-only, with explicit manual actor/reason/current-active
supersession, one child and same node pair. Cross-run supersession requires an
identical pair from a sealed predecessor. Global active views/review statuses are
distinct from frozen per-run decision snapshots. Materialization builds SAME
components and rejects any internal DISTINCT/AMBIGUOUS candidate pair. Every node
gets one VenueEntity membership; record membership derives through node members.
ven_+first32 SHA256(canonical sorted node IDs) is deterministic but run-scoped.
Entity rows count nodes and raw records, not ranking quality.

OPEN -> nodes generated (inputs frozen) -> candidates generated (nodes frozen)
-> MATERIALIZED (identity locked) -> SEALED. SQL capability/immutability/duplicate
guards and savepoint-safe transactions protect transitions, node/entity writes and
REPLACE even with recursive triggers OFF. Hashes exclude generated timestamps and
snapshot retrieval time; frozen evidence and full membership/decision provenance
remain hashed. Verification checks node identities/members, candidates, decisions,
entity membership/counts/basis and relation history.

VenueRelation permits manual/imported PREDECESSOR_OF, CONTINUATION_OF, MERGED_INTO,
SPLIT_INTO, POSSIBLY_RELATED_VENUE only after materialization and before sealing.
Distinct entities/same run/ordered-pair supersession are required. No automatic
venue relation inference or destructive identity edits.

## SCImago projection

ScimagoVenueProjection reads frozen VenueEntity -> provider node members -> source
records -> selected typed claims. No independent truth table. Global latest is
the maximum validated manifest latest-year context carried by source-list inputs,
falling back to their release year for standalone sources. It never substitutes a
venue's personal last-seen year. Thus a declared latest snapshot not selected into
the run yields unavailable latest metrics rather than borrowing older values.
The run's context is immutable; a new annual import is reflected in a new run.

SCIMAGO_PRESENT_LATEST means a resolved source-list member in that global year;
earlier-only means SCIMAGO_HISTORICAL_ONLY; no source-list member means
SCIMAGO_ABSENT. Non-SCImago nodes separately report SCIMAGO_MATCHED/AMBIGUOUS/
UNMATCHED from identity decisions. APC-only evidence never creates SJR presence.
Only the compatibility helper returns legacy ACTIVE_LATEST/HISTORICAL_ONLY/
AMBIGUOUS/UNMATCHED; none means active Scopus indexing.

Exact-year metrics require both source snapshot release Y and period_year Y.
Latest uses the global year, never a personal last-seen snapshot. No interpolation,
carry-forward or future-year publication quartile. Conflicting metric claims remain
lists, not a selected truth. Exact-category helper uses normalized equality only
and exposes all matches plus an explicitly derived best quartile across requested
categories. It is not stored as a canonical metric or screening decision.

## Annual operator maintenance

When yearY is published: manually export SJR Y; add the manifest-defined annual
filename; never rewrite older annual CSVs; hash exact bytes; update SHA256SUMS;
addY to manifest metadata and latest when newest; optionally add independently
dated Journal Value; commit GitHub. Re-run the same local iterator after a new
verified checkout/export. Application code/config/migration/release change: NONE.
CSV schema change is different: unsupported required semantic drift fails closed
until a deliberate schema-profile/parser version is added.

Stage05 adds network commit pinning/sync; Stage06 Scopus/WoS ingestion; Stage07
OpenAlex acquisition; Stage10 venue tiers/admission/screening. None implemented.
