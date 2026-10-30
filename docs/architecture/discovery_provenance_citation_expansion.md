# Discovery provenance and explicit citation expansion

Stage08 starts from accepted Stage07 commit
`ab3f3e1586f718fab4daf5d702188e39aeb892bf`. It adds migration0008 and
`reprovane.discovery`. Historical code, migrations, resolver rules, dependencies
and CLI commands are unchanged.

## Identity and evidence

An explicit research ProjectRevision owns each discovery run. The API never calls
Stage07's standalone acquisition-project convenience method. Registration accepts
only COMPLETE Stage07 work acquisitions in that revision and does no HTTP request.
The acquisition ID references its canonical request specification and SHA256,
sealed snapshot, exact raw pages and typed ProviderRecords. An empty primary
query still has an operation and acquisition binding.

PRIMARY_QUERY, BACKWARD_CITATION and FORWARD_CITATION events retain target records
where available, acquisition/raw context, seed entity with sealed resolution-run
context, and explicit depth. Missing/malformed targets stay as events without
fabricated ProviderRecords. Discovery never decides eligibility or corpus membership.

Stage03 alone resolves publications. `resolve_seeds` creates a sealed Stage03
checkpoint; final materialization creates a sealed Stage03 run over target and seed
records, with the accepted OpenAlex work selector and conservative resolver policy.
Discovery consumes frozen Stage03 decisions, rather than later globally active
decisions. Components incident to an AMBIGUOUS decision or an existing Stage03
input evidence warning are withheld from authoritative discovery membership.
Stage03's retained singleton entities in such cases are not promoted as resolved
discovery identities. Raw routes remain inspectable. No new identity rule is added.

One membership is keyed by run and Stage03 entity. All legitimate events remain,
including multiple queries, seeds and route types. IDs retain resolution-run
context: adding evidence may change a final entity ID relative to a checkpoint;
historical seed references are not rewritten.

## Bounded expansion

Both operations perform one explicit hop. Supply descriptors with
`resolution_run_id`, `publication_entity_id`, `provider_record_id` and integer
`depth`. Depth is caller-supplied provenance of the chosen seed route, not inferred
shortest-path distance. Primary depth is zero; citation depth is explicit seed
depth plus one. A seed found on multiple routes may be submitted at different
depths. Bounds are 1–100 seed descriptors, 1–10000 results per seed, and1–100 batch/
page size. These are operational bounds, not frontier or recursive policies.

Backward reads raw `referenced_works`. Omitted arrays cause a separate Stage07
seed-ID acquisition selecting references; malformed/absent supplemental arrays
fail closed. Original snapshots are untouched. Duplicate IDs become one route
with every original array index retained; malformed values become unresolved
events. A sorted lexicographic prefix enforces the explicit bound and records
omitted counts. `related_works` is never citation evidence. COMPLETE same-revision
native-ID records are reused; other targets use sorted OR batches <=100. The
local-vs-fetch plan freezes before target requests so retry cannot change identity
because other local records arrived.

The backward edge is seed → requested reference ID. A single-ID request returning
one changed ID records both requested and returned IDs with single-filter mapping
evidence, without asserting identity equivalence. Unknown returned IDs in multi-ID
batches are not guessed into routes: requested targets remain UNATTRIBUTED_ID_CHANGE
and child raw acquisitions retain unassigned results. Missing targets retain edges
without entities. Supplemental seed ID changes require explicit new seed resolution.

Forward delegates `cites:W...` to Stage07. Returned arrays must contain the seed;
contradiction or malformed arrays fail the whole operation. If the API omits the
array, the exact acquisition/filter is the evidence basis; no array is fabricated.
The edge is citing work → seed; the discovery route goes seed → citing work.
Stage07 handles cursor, limits, retry, credentials, raw bytes and normalization.

Both reuse Stage02 CitationEdgeObservation via the accepted deterministic
AcquisitionObservationStore, profile `openalex-citation/1/1`. Raw edges point to
`/results/i/referenced_works/j`; filter-only evidence references the immutable
acquisition specification. Citation is provider evidence, not canonical identity.
Self-loops retain marked events and edges without creating membership. Cycles
are retained and never traversed automatically.

## Persistence and recovery

Six tables store runs, operations, acquisition bindings, events, memberships and
replay receipts. A view joins events to final membership. Run lifecycle is
OPEN → MATERIALIZED → SEALED. Evidence appends only in OPEN; operation states are
OPEN/FAILED/COMPLETE with a once-frozen plan. Completed operations and materialized
runs lock. Further work needs a new explicitly linked run or revision. Parents
must be sealed in the same project; acquisitions/seeds still require the new run's
exact revision, so there is no implicit cross-revision import.

Operations use canonical seed/bounds/corpus identity. Exact replay produces no
HTTP/events and adds an immutable suppression receipt. Distinct seed, type, depth
or query acquisition remains distinct. Citation observations are deterministic.

Child acquisitions/bindings persist before route registration; Stage07 reuses
same-revision/spec requests and resumes its receipt chain. Citation observations,
events, receipt and operation completion commit atomically. Failure leaves FAILED
operation and auditable child acquisitions, with no partial routes; retry uses the
same identities. Unfinished operations block materialize/seal. Primary registration,
final Stage03/membership/manifest, and auto-materialize-plus-seal are atomic.
Verified orphan CAS bytes can remain after rollback, as in Stage07.

SQL guards reject immutable UPDATE/DELETE/REPLACE even with recursive triggers
disabled. Lifecycle writes require scoped connection-local authorization. This
protects ordinary API/SQL clients, not schema/function replacement by an adversary.
One connection/worker is supported; distributed/power-loss certification is not claimed.

`verify` recomputes membership/manifest, checks sealed Stage03 resolution and raw
snapshots behind linked acquisitions/records. Inspect derives persisted route,
membership, unresolved, edge, replay, multi-route, depth, seed, missing and child
counts. Route counts may exceed unique edges. Before materialization authoritative
membership count is zero, not an estimate.

## API and CLI

`DiscoveryStore(database)` offers create(project_revision_id), register_primary,
resolve_seeds, expand_backward/forward, materialize, seal, inspect and verify.
`python -m reprovane.discovery --help` exposes matching commands except verify.
All need `--workspace`; create needs `--project-revision`; expansion needs `--seeds`
(UTF-8 JSON descriptors) and `--max-results`. `--batch-size` means backward OR
batch or forward page size. Optional local `--config` delegates to Stage07.
There is no credential argument. Help/inspect resolve no credentials/network.
Errors are generic and secret-safe; inspect persisted operation/child state.

## Official documentation and limits

Checked2026-10-29:

- https://help.openalex.org/data/works/citations/
- https://help.openalex.org/data/works/attributes/
- https://help.openalex.org/how-to/api-recipes/
- https://help.openalex.org/api/get-single-entities/
- https://help.openalex.org/api/filtering/
- https://help.openalex.org/api/paging/
- https://help.openalex.org/api/authentication/

These establish citation direction, cites filtering, OR maximum100, paging and
merged IDs. Mutable API data are retrieval evidence, not complete/timeless graph
coverage. First-occurrence raw metadata follows Stage07; all other occurrences
remain in its snapshot. No automatic refresh, redirect-based identity inference,
fuzzy matching, embeddings, AI rewrite, screening, corpus release, PDF/full-text/
Unpaywall, frontier or Stage09 work is added.
