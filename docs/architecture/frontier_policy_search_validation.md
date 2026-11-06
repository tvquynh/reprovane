# Stage09: structural frontier and explicit search validation

Baseline: Stage08 `61f3dabf6d1bfacfd9b9f4196e69dd507141530d`, tree
`7c3c151fbaa794d6e998f2842fc2e767da2274c5`,818 accepted tests.
Migration0009 adds seven tables. All historical migrations, tests, transport,
discovery and identity implementations remain unchanged.

## Explicit policy and ordering

`FrontierPolicy.from_value` requires directions, max_depth, max_rounds,
max_seeds_per_round, max_results_per_seed, batch_or_page_size, corpus (null to
inherit), global_new_work_budget, global_child_acquisition_budget, and saturation
(null disables it). Bounds must be positive finite integers; Stage08 maxima remain
100 seeds,10000 results/depth and100 page/batch size. The only ordering is
DEPTH_EVENT_WORK. Canonical JSON and SHA256 freeze every parameter. Worst-case
actions, selected seeds, returned works and child acquisitions are computable
without network; conservative upper bounds include a supplemental seed fetch.

One frontier owns one OPEN same-ProjectRevision discovery run. Changed policy
requires a new explicitly linked discovery/frontier context. Existing discovery
operations must be complete at binding. Initial work/event/operation/child sets
are frozen. Unrelated local acquisition records do not change a retry plan;
unowned Stage08 operations on the bound discovery are rejected as context drift.
The supported execution model is one worker/connection, with no competing writers
to that discovery while a frontier runs.

Stage08 `resolve_seeds` supplies safe sealed Stage03 checkpoint records. Stage09
groups only those already-discovered resolved records by that checkpoint entity;
it adds no identity rule. Unresolved/ambiguous evidence stays visible and cannot
seed expansion. The minimum actual retained event depth across each component
is used, ignoring self-loop-only routes. Choose minimum canonical native Work ID,
then ProviderRecord ID, as seed. Sort candidates by depth, minimum stable event
key, chosen Work ID and record ID. Ties use immutable keys, not wall-clock timing.
No titles, abstracts, citation counts, venues or relevance scores affect selection.

History records all component Work IDs at execution time, direction and seed depth,
with Stage08 operation identity. Default suppression is once per overlapping stable
Work-ID component/direction in a frontier, regardless of later depth improvements.
A later merge or a changed minimum native ID therefore cannot cause re-expansion
of that direction merely because checkpoint entity IDs changed. Another direction
may remain available. Stage08 retains every discovery route independently.

## Frozen rounds and hard budgets

`plan_next` freezes all candidates, unresolved records, selected/deferred direction
reasons, checkpoint, chosen seed descriptors, requested/effective limits, corpus,
budget reservations and expected Stage08 operation specifications/IDs before any
network. It creates the actual pending Stage08 operations through the accepted
store; it does not implement a second event or expansion engine. BACKWARD executes
before FORWARD when both are enabled. Operational reasons are SELECTED,
ALREADY_EXPANDED, MAX_DEPTH_REACHED, NO_RESOLVED_OPENALEX_SEED, ROUND_SEED_LIMIT,
GLOBAL_BUDGET_LIMIT and DIRECTION_DISABLED. None is scientific screening.

Each action invokes exactly one existing Stage08 one-hop method on one seed.
Backward may create at most ceil(effective_limit/batch_size) target acquisitions
plus one supplemental seed acquisition if references were omitted. Forward may
create at most one child acquisition. Reserve at most effective_limit new Work IDs,
plus one for a possible changed supplemental response. This conservative reservation
is made even when all results might already exist locally or a query might be empty.
It can stop earlier than actual budget exhaustion; it never speculates on unknown
remote yield. Effective limits are clipped deterministically to remaining work and
child capacity, with original/effective values retained. Unused reservations become
available in the next round; frozen plans never grow during execution.

Run-level usage counts unique returned native Work IDs beyond the frozen initial
set. It includes local Stage08 target events and all Stage07 child raw result IDs,
including supplemental, unassigned merged-ID and interrupted-page results. Child
usage is unique newly bound Stage07 child acquisition IDs beyond the initial set,
including reused cached acquisitions; it is not HTTP-request count. Initial primary
results are not charged as newly expanded work. Requested-but-missing identifiers
are reported separately and never masquerade as returned work. Selected seeds are
also accumulated. This accounting is independent of temporary Stage03 entity IDs.

Completed action receipts link to Stage08 operations and their Stage07 acquisition
bindings; no discovery events are copied into a Stage09 authoritative event table.
If HTTP or local registration fails, the round/run becomes FAILED with the frozen
plan retained. Retry skips completed receipts, recognizes an already-complete
Stage08 operation even if its action receipt was interrupted, and delegates pending
operation recovery to Stage08/07. A round completes atomically with structural
metrics, checkpoint, new event/work/missing IDs, newly safe provider record IDs,
cumulative usage, saturation streak and stop state. Completed rounds are immutable.

Policy stops are explicit: NO_ELIGIBLE_FRONTIER, MAX_DEPTH, MAX_ROUNDS,
GLOBAL_NEW_WORK_BUDGET, GLOBAL_CHILD_ACQUISITION_BUDGET, STRUCTURAL_SATURATION.
FAILED is resumable and never success. For a completed round hard work, child and
round caps take priority over saturation. An empty planned round preserves candidate
diagnostics and its explicit terminal reason. No automatic scientific sufficiency
test exists. `run_to_policy_stop` is synchronous and bounded by max_rounds.

Optional saturation requires explicit minimum_new_items and consecutive_rounds.
A round whose new unique native Work IDs <= minimum_new_items increments the
streak; greater yield resets it. The configured consecutive streak stops structural
expansion. It is not recall of all literature, completeness or relevance saturation.
All hard caps remain mandatory when saturation is disabled or enabled.

Finalization requires STOPPED and complete rounds/operations. It verifies intermediate
Stage03 checkpoints and atomically seals Stage08 discovery plus the frontier.
Manifest hashes cover policy/revision/initial context, plans, results, action
receipts, operation/child identities, checkpoint manifests and final discovery
manifest, excluding volatile timestamps. Failed finalization rolls back discovery
sealing too. Finalized replay performs no HTTP.

## Frozen search-validation plans

`SearchValidationStore.create(revision, variants, sentinels, thresholds=None)` freezes
the whole plan at creation. To add/change a variant, sentinel or threshold, create
a new run; no in-place plan editor exists. A variant has a distinct safe label,
source USER/PROTOCOL/SEALED_ACQUISITION, and exactly one explicit Stage07 spec or
completed acquisition ID. Specs use accepted Stage07 canonicalization, request SHA,
finite bounds, corpus, sort, page size, select and literal search behavior. New
explicit specs create/reuse the Stage07 acquisition without fetching until execute.
Reused acquisitions must be complete and in the exact ProjectRevision. No custom
HTTP, generated variant, semantic search or automatic rerank is initiated.

Execution acquires only unfinished explicitly supplied variants through Stage07,
then derives all sets from persisted work ProviderRecords/snapshots. Comparison
may run without any Stage08 discovery run. It never registers a primary discovery,
chooses a best variant or promotes validation results. A later promotion is an
explicit separate Stage08 register_primary action and cannot mutate validation.

Sentinels accept exact canonical OpenAlex Work IDs or the accepted Stage03 normalized
DOI only. Original identifier forms and optional non-sensitive notes are preserved.
Repeated identical sentinels deduplicate; changed definitions alter the plan hash.
Malformed/title-only identifiers fail before persistence. Stage03 resolution of
variant records provides safe-vs-ambiguous evidence for matching; no title fallback
or duplicated DOI equivalence rule exists. Hard-conflicting exact hits are UNRESOLVED.
Normalizing the DOI uses the accepted function, not a new matching algorithm.

States are RETRIEVED, NOT_OBSERVED_IN_ACQUISITION and UNRESOLVED. A single-ID Stage07
filter response with a different native ID preserves the requested sentinel and
returned ID, marked SINGLE_ID_FILTER_RESPONSE_NOT_IDENTITY_EQUIVALENCE. It remains
unresolved rather than being silently treated as an exact hit. No extra sentinel
lookup or redirect is followed. In multi-ID requests an unassigned merged response
cannot establish a mapping; missing means only not observed in that acquisition.

For each variant, known_item_recall = retrieved / (retrieved + missing). Report
unresolved, total_expected and resolvable_expected explicitly; denominator0 gives
null, not a perfect score. This is **known-item recall, not recall of all relevant
literature**. Empty sentinel sets are informational, with null recall.

Each variant retains exact acquisition/spec SHA, canonical spec, snapshot, original
termination, persisted record/native-ID count, sorted native-ID set and observed
exclusive count. Stage07 NATURAL is displayed as QUERY_EXHAUSTED; RECORD_LIMIT and
PAGE_LIMIT are bounded observations, never exhaustive. Pairwise intersections,
unions, Jaccard (empty/empty=1), exclusive sizes and a bounded-comparison flag are
deterministic. Global variant-exclusive count subtracts the union of all others.
Changed request fields show sort/limits/corpus/select/search differences. The
query_terms_differ flag compares literal search parameters only; it does not parse
arbitrary filter-based search expressions or claim scientific query sensitivity.

Thresholds are optional, explicit minimum_known_item_recall, maximum_unresolved
and required_sentinels. They freeze before acquisition/metrics. Output is
INFORMATIONAL_ONLY, VALIDATION_THRESHOLD_MET or VALIDATION_THRESHOLD_NOT_MET per
variant. There is no inferred threshold, combined relevance score or automatic winner.

Run lifecycle is OPEN/FAILED → COMPLETE → SEALED. Child acquisition state survives
failure; result and Stage03 materialization commit atomically. Results never mutate.
Final verification recomputes metrics from snapshots and typed evidence, verifies
definitions/request identity/checkpoint, and compares semantic/final hashes.

## Interfaces, constraints and documentation

`python -m reprovane.frontier`: create, plan-next, run-next, run-to-policy-stop,
inspect, finalize. Create requires --project-revision, --discovery-run and --policy
JSON. `python -m reprovane.search_validation`: create, execute, inspect, finalize.
Create requires --project-revision and --plan JSON containing variants/sentinels/
optional thresholds. All commands require --workspace; existing runs use --run-id.
Execution may read ignored local --config via Stage07 credential resolution.
Help/inspection never resolve credentials or network. Errors are generic and safe.

SQLite guards protect insert context, immutable identities, policy/plan fields,
completed rounds, results, DELETE and REPLACE (also recursive_triggers=OFF).
Authorized service transitions use a scoped connection-local function. Multiple
Stage09 services share the accepted discovery store/authorization on one Database.
This is not protection against an actor replacing schema/UDFs. One worker is
supported; no concurrent/distributed or power-loss certification is claimed.

Current official OpenAlex Help Center checked2026-10-29 before implementation:

- https://help.openalex.org/api/searching/
- https://help.openalex.org/api/sorting/
- https://help.openalex.org/api/paging/
- https://help.openalex.org/api/authentication/
- https://help.openalex.org/api/filtering/
- https://help.openalex.org/data/works/citations/
- https://help.openalex.org/how-to/api-recipes/
- https://help.openalex.org/data/works/corpus/

Broad search searches text and provider keywords, using provider processing and
default relevance order. Uppercase AND/OR/NOT and quoted phrases are provider
syntax; this project passes them literally. Explicit sort remains authoritative.
Stage07 supports search and title_and_abstract; the documented newer
search.title_abstract_keywords root parameter is not newly added here. The project
does not invent synonyms despite provider-side keyword matching. Paging supports
per_page<=100 and exact cursor continuation with an API key beyond page1. Forward
uses cites; ID filtering supports up to100 OR IDs. Default corpus is core; all and
expansion overrides remain explicit. No automatic rerank/semantic endpoint is added.

Upstream data are mutable: reproducibility is retained acquisitions, not timeless
coverage. Refresh needs a new explicit acquisition/revision. Conservative budgets
may under-use remote capacity. Stage03's accepted default candidate block limit25
remains and oversized blocks fail closed. Large finite validation sets may be
memory-intensive; no bulk/performance certification is claimed. There are no
screening/reviewer/eligibility decisions, corpus release, PDF/full-text/Unpaywall,
AI query rewrite, embeddings or Stage10 implementation.
