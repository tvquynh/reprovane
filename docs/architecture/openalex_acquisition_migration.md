# Stage07: OpenAlex acquisition migration

The new explicit acquisition API produces ProjectRevision→openalex Provider→
ProviderSnapshot→page RawArtifacts→native-ID ProviderRecords→typed observations.
It performs no publication/author/venue entity resolution, citation traversal,
reference queueing, screening, PDF/Unpaywall acquisition, or AI query rewriting.

## Official contract checked 2026-10-29

Current primary documentation, rather than the older openalex-docs credit model:

* https://help.openalex.org/api/authentication/
* https://help.openalex.org/api/paging/
* https://help.openalex.org/api/errors/
* https://help.openalex.org/api/filtering/
* https://help.openalex.org/api/searching/
* https://help.openalex.org/api/sorting/
* https://help.openalex.org/api/selecting-fields/
* https://help.openalex.org/data/works/corpus/

Supported per_page is1..100. Cursor begins with *, follows the exact next_cursor;
post-first-page paging requires a key. Core is default; all/expansion are explicit.
No include_xpac/is_xpac controls in new specifications. Select is root-level and
always includes id. Ordinary literal search remains ordinary API text search;
title_and_abstract uses current search.title_and_abstract. No semantic search,
rerank, bulk harvesting, or automatic changes to query/corpus/sort.

## Legacy audit and compatibility

Audited reprovane/openalex.py, config.py, pipeline.py, literature_pipeline.py and
fulltext.py. Existing PER_PAGE already100; no200 migration is needed. Legacy
download_queries uses one query-specific checkpoint signature, per-query limits,
date/abstract filters, cursor resume, RecordStore DOI/ID aliases and flat JSONL/CSV
records including OA, source and citation metadata. Its checkpoint can resume with
a larger max_per_query; Stage07's strict request identity intentionally includes
max_records instead. No Stage07 cursor is shared with that legacy checkpoint.

Existing run/enrich/inspect-source/fetch-oa commands, help, output schemas, query
parameter ordering, literal search/filter construction and legacy RecordStore
behavior remain intact. inspect-source is a SCImago trajectory operation, not a
legacy /sources API acquisition. No pre-existing /sources HTTP workflow requires
migration. fetch-oa remains a separate legacy command; Stage07 never invokes it.

OpenAlexClient now wraps one shared byte-preserving transport in openalex_http.py.
Legacy request keeps its historical parameter order/query credential interface
for compatibility; new persisted request_page sends key only as an Authorization
header. Retry bounds/caps, response closure, private-value rejection and safe
errors apply through the same implementation. Arbitrary production endpoint
overrides are rejected; explicit loopback endpoints remain for accepted tests.
No current v4 command is silently switched to a new scientific persistence model;
the explicit feature CLI below uses the new architecture. This is a deliberate
compatibility boundary, not a claim that legacy JSONL checkpoints are snapshots.

## Request and credential identity

AcquisitionSpec stores canonical JSON for works endpoint, literal search, effective
filter expression, sort, root-level select, corpus, query mode, maximum unique
works, page size and page cap. Mapping filters render deterministically, numbers/
booleans normalize to wire strings; select order/duplicates normalize to a sorted
set plus id. Literal filter expressions retain their exact text; no unsafe
comma/quote parsing or reordering. SHA-256 excludes api_key/mailto/email transport
aliases, timestamps and credentials. Materially changed requests are different.
A narrowed search/filter and finite positive maximum are mandatory; default page
cap1000 (maximum10000) bounds pathological duplicate-only paging.

Local credential convention is reused: ignored config.json openalex.api_key first,
OPENALEX_API_KEY process-environment fallback, then the named Windows User-level
environment value if the desktop process predates an update, or runtime-only
OpenAlexClient injection. Only that registry value is read, never enumerated or
exported. Email is only
a private runtime guard value in the new acquisition path, never an API key,
request identity or persisted polite-pool field. New CLI has no --api-key option.
Help/status does not load credentials or make requests. No local private values
are shown in repr/errors/logs. A runtime guard rejects private values, their URL
encoding or decoded JSON escapes in specs/responses before persistence. Returned
raw bytes remain exact; credential-bearing bodies fail closed rather than being
redacted and misleadingly hashed as the original response.

Production requests target HTTPS api.openalex.org/works only, redirects disabled,
trust_env=False/auth=None, explicit project User-Agent and connect/read10/60s.
New acquisition defaults3 attempts, legacy retains its accepted bounded8-attempt
setting. Retry429 and500/502/503/504 plus connection/timeouts/truncated streams;
numeric Retry-After and backoff cap10s. Permanent4xx/TLS/redirects do not retry.
Successful pages default16 MiB cap, enforced on actual streamed bytes. Rate-limit
headers retain only finite nonnegative numeric allowlisted fields; credentials,
cookies/arbitrary headers/body errors are not logged. Operational rates are not
semantic identity. No keyless deep-cursor attempt is sent.

## Staging, resume and publication

Migration0007 adds only acquisition-run state and immutable page receipts, plus a
Stage07-scoped unique native-record index. Historical0001–0006 remain byte exact.
Existing Foundation, artifact store, snapshots and typed tables are reused. The
explicit acquisition store upgrades schema7 without changing old feature targets.

Each acquisition belongs to a ProjectRevision and exact request-spec SHA. Same
revision/spec reuses one acquisition; changed spec cannot resume it. CLI creates
or reuses a revision with that canonical manifest. A fresh time-based acquisition
of the same request requires an explicitly new project revision through Foundation.
Completed acquisition never reopens. OPEN/INTERRUPTED state has no final snapshot.

Each accepted page is stored byte-exact in Foundation CAS, registered and committed
with a receipt and progress/cursor update in one transaction. Receipts preserve
request parameters, exact input/output cursor, ordinal, SHA/size, UTC retrieval,
status, parser version and numeric rate metadata. Invalid payload/envelope/cursor
cycle is not accepted as a page; failed unreferenced CAS objects may remain.
Resume rehashes/replays every accepted receipt, validates its request/spec/cursor
chain and unique-ID counts. Failed normalization after a terminal page can retry
without HTTP. Process death before receipt commit repeats that cursor, without
duplicating semantic records. One acquisition worker/connection is recommended;
constraints reject competing page identities, but no distributed-worker guarantee.

Termination is NATURAL (null cursor or empty results), RECORD_LIMIT or PAGE_LIMIT.
COMPLETE means this explicitly bounded acquisition completed, not necessarily the
remote query exhausted. Limit changes create a new request identity; they do not
reuse a saved cursor. Failure stores INTERRUPTED/ACQUISITION_INCOMPLETE and never
produces a visible sealed-complete acquisition. A completed corrupt artifact fails
verification without rewriting history.

After pages verify and termination is known, one transaction creates records,
seals snapshots, writes required typed claims, reconciles counts and commits the
COMPLETE pointer/audit event. Accepted typed-observation triggers require a sealed
ProviderRecord anchor, so the internal seal precedes typed writes inside that
transaction. This is the necessary ordering adaptation: no other connection can
see that seal before all normalization and reconciliation succeed. Any failure
rolls back both snapshots, records, observations and completion pointer; receipts
remain resumable. A corrupt/incompatible retained page fails closed and may need
an explicitly new acquisition/schema version rather than silently refetching it.

## Mapping and semantic boundaries

Work IDs are canonical https://openalex.org/Wdigits, never synthesized from title
or DOI. One record per native ID/acquisition; duplicates retain every page/index
anchor, with the first work occurrence defining typed claims. IDs are deterministic
from acquisition ID plus native ID; retry after rollback produces the same record
IDs. Raw duplicate versions are retained, not merged by DOI/title. Stage03 alone
owns publication identity resolution.

AcquisitionObservationStore subclasses the accepted typed API solely to derive
observation IDs from record ID, exact normalizer profile and semantic fields.
Every existing field validation/SQL guard is reused. Repeated identical normalized
writes verify/reuse immutable rows, including their original timestamp; no REPLACE
or mutable upsert is used. Retry after rollback is deterministic.

Metadata uses typed title/date/year/type/language/valid abstract reconstruction.
Identifiers preserve Work ID and present DOI; malformed DOI remains invalid
evidence for Stage03, absent DOI emits no claim. Authorship follows source array
order, preserving gaps when a provider omits a display name rather than inventing
one. Author IDs/lexically valid ORCID use the accepted authorship schema; no author
entities, name splitting or disambiguation. Retracted=true emits RETRACTION with
REPORTED_BY_OPENALEX and no invented effective date; false stays raw metadata.

Primary source descriptions/ISSN/ISSN-L are evidence only. Separate embedded_sources
snapshot holds observed Source IDs and raw fragment locators, suitable for explicit
Stage04 venue inputs. Work records also carry their primary-venue evidence for
publication context; use works snapshot for Stage03, embedded_sources for Stage04.
Repeated embedded sources select their first observed fields and preserve all exact
source-fragment anchors. No /sources HTTP mirror is added. OpenAlex membership-like
flags, listed_in and OA data stay raw and never create Scopus/WoS/SCImago memberships.
Cited-by count belongs in ProviderRecord reported metadata because no accepted
publication metric table exists; it is not forced into venue metrics. Reference/
related works and OA/PDF links remain raw, never queued/fetched.

## Explicit API/CLI

```python
from reprovane.core.db import Database
from reprovane.openalex_acquisition import OpenAlexAcquisitionStore, local_client
spec = {"search": "bibliographic metadata", "filters": {"publication_year": 2024},
        "corpus": "core", "page_size": 100, "max_records": 20}
with Database(workspace) as db, local_client() as client:
    result = OpenAlexAcquisitionStore(db).acquire(spec, client)
```

```powershell
python -m reprovane.openalex_acquisition acquire --workspace C:/research/project --spec request.json
python -m reprovane.openalex_acquisition inspect --workspace C:/research/project --acquisition-id <id>
python -m reprovane.openalex_acquisition resume --workspace C:/research/project --acquisition-id <id> --spec request.json
```

The saved request file must exclude secrets; credentials remain environment/ignored
config/runtime only. No source-network work happens on help/inspect. Live validation
uses a deliberately tiny request and reports authenticated bounded cursor or
keyless first-page mode honestly; raw live responses and private config are omitted
from delivery. Stage08 discovery/frontier/full-text work is not started.
