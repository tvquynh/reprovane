# Stage11: immutable corpus releases

Baseline Stage10: `5eef8738283669a8e39301ca1816a8a8eb3cede8`, tree
`fe81c80d24de8a9efb9a4951226d9cd83200cd9e`,1025 tests. Only new Stage11 files and
migration0011 are added. All upstream implementations/migrations/tests remain.

## Authority and lifecycle

CorpusReleaseStore.create requires exact same-ProjectRevision SEALED Stage10
screening and verifies the entire accepted upstream chain. Only its current final
SCREEN_INCLUDE outcomes become items. No metadata/DOI/OA/retraction/venue/year/
citation/AI criterion is applied. SCREEN_EXCLUDE is non-membership audit only;
unresolved discovery evidence remains a separately hashed count/reference summary.
Stage10 decisions are neither rewritten nor reinterpreted.

Lifecycle: OPEN → MATERIALIZED → SEALED. Create freezes protocol/source context;
materialize appends all items/export metadata atomically, computes manifests;
finalize verifies and seals. Auto-materialize-plus-seal is atomic too. Items and
export metadata are append-once before seal and immutable afterward. The
corpus_release_membership view exposes only SEALED releases; materialized items
are provisional until seal. Export/handoff require SEALED. An empty include set is
valid:0 items, empty JSONL, CSV header, retained exclusion/unresolved counts.

Three tables store release header, normalized release items and one canonical
export description. SQL guards protect CREATE/MATERIALIZE/SEAL context, exact
screening/include FKs, unique entity/ordinal identities and UPDATE/DELETE/REPLACE,
including recursive_triggers=OFF. Scoped connection-local authorization permits
service transitions. This is not defense against an actor replacing schema/UDFs.
No distributed/power-loss certification is claimed.

Same screening ID and exact plan SHA reuse one release, including interrupted OPEN
contexts. Changed plan/label creates a new release without mutating the old one.
Operational release ID may be generated; content SHA binds exact input, projection
and membership. Item ID hashes release ID+publication entity, never replacing
Stage03 identity. Optional parent/delta inference is not implemented.

## Explicit plan and source chain

CorpusReleasePlan requires release_label, release_schema_version, metadata_fields,
identifier_policy, authorship_policy, venue_policy, provenance_policy, export_formats,
ordering_policy, identity_policy, screening_source_policy, oa_policy,
search_validation_refs and export_directory_policy. Canonical JSON/SHA freeze all.
Schema1 supports JSONL+CSV, exact files only, PUBLICATION_ENTITY_ID or CORPUS_ITEM_ID
ordering, USE_STAGE03_FINAL_IDENTITY and EXACT_SEALED_STAGE10. Labels are bounded
safe descriptive labels, not primary identity or integrity authority.

Source context includes revision/project, exact screening/final resolution/plan/
candidate/assignment hashes, final include references, SCREENING_EXCLUSION_AUDIT
references/count, unresolved provider summary/hash, discovery materialization SHA
and owning frontier ID/final hash. Optional explicit same-revision SEALED search
validation IDs are verified and frozen as audit references; scores never select
members. New unrelated validation runs are not silently added to a release.

## Frozen item snapshots

Identity freezes final Stage03 entity/resolution and all member ProviderRecords,
namespace/native IDs, snapshots, raw locators, identity membership basis and exact
identifier observations from the sealed Stage03 input manifest. DOI uses accepted
normalize_doi only; original/invalid/multiple conflicting claims remain available.
All valid exact DOI values are retained; none is arbitrarily selected. OpenAlex
Work IDs come from existing canonical provider identity, never synthesized from
DOI. No identity resolver or AuthorEntity is created.

Metadata is a release-specific per-record projection. Core metadata/identifier/
authorship fields read only observation IDs frozen in final Stage03 inputs, even
if the screening packet omitted a field. Venue descriptions and integrity claims
use exact IDs already frozen by Stage10. If Stage10 did not freeze those optional
extra claims they remain missing, rather than reading later/latest rows. Authors
retain positions, names/IDs and provider scope. Fields remain null when absent;
multi-provider claims are not collapsed into a guessed canonical title.

The explicit authorship policy is PER_PROVIDER_ORDERED or OMIT, consistent with
the requested fields. Venue policy is FROZEN_PROVIDER_DESCRIPTION or OMIT. New
Stage04/06 intelligence joins are not implemented; no unified indexed flag exists.
Metadata fields govern metadata, while identifier_policy always preserves exact
identity identifiers (including CSV/handoff identifiers) even if a metadata DOI
column projection was omitted.

Screening provenance freezes source screening SHA, candidate and outcome/resolution
IDs/hash, AUTO_CONSENSUS/ADJUDICATED basis, effective reviewer decision IDs and
adjudication ID, with no exclusion codes on members. Optional actor labels are
safe run-scoped Stage10 labels; notes and private contact details are never copied.
Discovery provenance references exact materialization/membership and its route
event IDs, preserving traceability without duplicating the authoritative graph.

OA policy may OMIT or retain existing raw work open_access/primary_location/
locations/best_oa_location fragments, with original artifact/page pointer. These
are read from the same accepted ProviderRecord's first retained Stage07 occurrence,
never a new query. Location/source fields remain descriptive provider claims,
including any raw provider flags, not verified Scopus/WoS/SCImago truth or full-text
authority selection. The separate explicit OA policy can retain raw source/location
descriptions even when the metadata venue field is omitted. Missing OA stays missing
and never changes membership. No URL is fetched or PDF acquired.

Each item hashes identity, metadata, screening provenance, discovery provenance,
OA locations and combined context/ordinal. Exact projection references persist.
The semantic release manifest binds plan/source, ordered item identities/hashes,
full item-set SHA, include/exclude/unresolved counts, upstream hashes and export
schema. Volatile UTC values are absent from semantic exports/hashes. Rebuild and
DB verification recompute every item from exact frozen sources, enforce exact
include membership and compare release/export manifests. Later unrelated typed
claims cannot mutate a sealed snapshot. Scientific repair requires a new upstream
screening/release context.

## Portable representation and checksums

Sealed releases export exactly corpus.jsonl, corpus.csv, manifest.json and
SHA256SUMS.txt. JSONL is lossless canonical UTF-8, one item/line in frozen order.
CSV is convenience only; multivalued title/year/DOI/Work ID/venue/type cells are
deterministic JSON arrays (missing=[]), not destructive representative choices.
Authors and full claim provenance remain lossless in JSONL. UTF-8 and LF are fixed,
with no generated-at timestamp, absolute directory or machine path in semantic data.

The semantic release manifest hashes item/source semantics and export schema; it
does not contain recursive hashes of its own portable wrapper. manifest.json wraps
that manifest/SHA, source/count context and exact byte hashes/sizes of the two data
files. The immutable DB export row hashes the complete portable manifest bytes.
SHA256SUMS covers corpus.csv, corpus.jsonl and manifest.json. The checksum file
cannot hash itself; the semantic schema explicitly records this standard exception.
Manifest inventory similarly excludes its own file and the checksum file to avoid
self-reference. These layers cover all portable content without circular hashes.

Exports preflight every existing entry. Different bytes, extra files/directories
or symlink files fail without overwrite/deletion. Same exact export reuses bytes.
A matching partial export may fill missing files through exclusive creation;
an I/O failure is detectable as incomplete and can be retried. Export files are
not a multi-file filesystem transaction; no success is returned until all four
files verify. Existing human edits are preserved.

verify_export needs no project DB. It enforces exact file inventory, SHA256SUMS,
manifest data hashes/sizes, strict JSON, canonical JSONL/CSV bytes, item IDs/ordinals/
hashes, source include/exclude consistency and rebuilt semantic manifest. Optional
expected_manifest_sha256 binds to an out-of-band trusted release hash. Portable
verification is self-consistency, not a signature/authenticity claim or independent
proof of upstream human judgments. Full DB verification remains authoritative for
the accepted source chain; a wholly regenerated untrusted package needs the trusted
expected hash or database to establish identity.

Privacy checks reject defined credential/header/email/file-URI/drive/private-Unix
path signatures, with optional caller-supplied forbidden_values. Project credentials
are never resolved/read to perform release operations. Unknown secrets are not
universally identifiable. Privacy refusal blocks the whole materialization/export;
it never silently removes individual screened members. A new explicit projection
plan/context is needed for omitting problematic descriptive fields.

## Inspection, handoff and limits

Statistics reconcile screening candidates/includes/excludes/corpus items and
unresolved provider evidence; DOI/Work ID presence derives from identity. Year/type
distributions count each distinct observed value once per item, so multi-valued
claims can make category totals exceed item count. Omitted metadata fields give
empty distributions. No diagnostic alters membership or creates an indexed count.

handoff is read-only and requires a verified SEALED release. Each member carries
item/entity/release IDs, release/item SHAs, exact DOI/Work/provider identifiers,
existing OA/location data, title/venue evidence. No excluded item enters it. It
implements no Stage11B acquisition, authority decision or Stage12 analysis.

`python -m reprovane.corpus_release` exposes create/materialize/inspect/finalize/
verify/export/verify-export/handoff. Create needs --workspace, --project-revision,
--screening-run and --plan JSON. Existing releases use --workspace and --release-id;
exports use --directory. verify-export only needs --directory and optionally
--expected-manifest-sha256; it does not instantiate a Database. All commands are
offline and have no API/AI credential argument or lookup. Errors never echo private
payloads. Accepted store modules may load for local verification; their acquisition,
resolver execution, screening mutation and frontier methods are never invoked.

Known limits: schema1 fixed formats/policies, no parent/delta inference, no new venue
intelligence join, first retained raw acquisition occurrence, in-memory bounded-by-
explicit-input projections and no large-scale performance certification. Existing
Stage03 identity limits remain upstream. Checksum integrity is not authentication.
Local validation uses only scripted synthetic keyless upstream fixtures with
socket/HTTP/config/credential access disabled:2 SCREEN_INCLUDE,1 SCREEN_EXCLUDE,
2 released members, portable/DB verification and a2-member handoff. No real corpus,
real review DB or human scientific judgments are delivered. Stage11B/12 not started.
