# Stage10: offline human screening, separate from discovery and release

Baseline: Stage09 `077bacd566c4260b7e6ee74138bd733107c71970`, tree
`cd556390788e401538bcd8c1ac5f6e2fbabe7695`,918 tests. Stage10 adds migration0010
and `reprovane.screening`; all historical implementations/migrations/tests stay intact.

## Input and frozen candidates

`ScreeningStore.create(project_revision_id, discovery_run_id, plan)` requires a
SEALED same-revision Stage08 discovery and verifies its materialization. If an
owning Stage09 frontier exists it too must be SEALED and verify. Search validation
is optional and cannot select/filter the screening population. No acquisition,
frontier execution or identity resolution is invoked by screening.

Every final resolved discovery membership becomes exactly one candidate, sorted
by publication_entity_id. Candidate ID hashes screening-run ID plus entity ID;
it does not replace Stage03 identity. The membership basis and final resolution
context are retained. Multiple discovery routes stay in Stage08 and cannot multiply
candidates. Unresolved provider evidence becomes no reviewer task; a separate
count/event-reference summary and hash persist, including in the final manifest.
An empty canonical candidate population is valid and retains that diagnostic;
it never means all unresolved provider evidence was screened.

Candidate-set SHA hashes run context plus ordered immutable candidate rows,
including exact evidence/hash/claim references. Assignment SHA hashes ordered
candidate/reviewer assignments. These hashes are deterministic within the frozen
run context; a new run has new operational IDs and a different candidate-set hash.

## Explicit protocol and human actors

ScreeningPlan requires every operative field: level TITLE_ABSTRACT, allowed
SCREEN_INCLUDE/SCREEN_EXCLUDE/SCREEN_UNSURE vocabulary, criteria definitions
(code/label/description/kind), required_reviewers, assignment_policy/assignments,
reviewers/adjudicators, consensus_policy, exclusion_reason_policy,
adjudication_policy, allowed_evidence_fields, note_policy and both correction
policies. JSON is canonicalized/SHA256 hashed. No scientific criterion is supplied
by engine defaults. Changed protocol requires a new ScreeningRun/context.

Actors explicitly declare HUMAN and safe non-email labels, scoped to the run.
AI/LLM/AUTOMATED_CLASSIFIER declarations fail. Reviewer/adjudicator labels are
distinct. Labels are attribution in a trusted local workflow, not authentication
or proof of a human's identity; callers must control access and label ownership.
Synthetic test actors/decisions are explicitly implementation fixtures, not actual
human scientific judgments or automated screening of real publications.

ALL_REVIEWERS requires required_reviewers equal the reviewer list size. Alternatively
EXPLICIT_ASSIGNMENT maps every final publication entity to exactly that many
distinct defined reviewer labels. Both assignments and actors freeze atomically
before any decision. Text/content/venue/difficulty never affects assignments.
Operational frontier codes are refused as scientific criterion codes; Stage09
metrics and stopping reasons never become screening decisions.

## Evidence seen by reviewers

The plan explicitly selects supported fields: title, abstract, publication_year,
publication_date, doi, authors, venue, work_type, integrity, provider_identifiers.
Core publication claims use observation IDs frozen in the final Stage03 input
manifest. Venue description and integrity claims from the same accepted profiles
are frozen by exact IDs at screening creation. All source records belong to the
final entity; no fields are fused or inferred across conflicting provider claims.
Claims remain per ProviderRecord with original IDs, source snapshot/raw locator
and observation provenance. Missing values remain null. Provider identifiers omit
DOI unless the separate doi field is allowed. Allowed venue output is existing
provider venue description; Stage04/06 intelligence joins are not implemented.

Exact frozen references are re-read for verification; later added observations do
not alter prior packets. There is no live metadata fetch. Retraction, missing DOI/
abstract, language/year/type, venue status or any other evidence never creates an
automatic exclusion. New intended evidence requires a new screening/project context.

## Immutable decisions and concurrency

Decisions are rows attributed to run/candidate/assigned reviewer, with typed
decision, sorted frozen exclusion codes, optional bounded note, predecessor ID and
UTC. SCREEN_EXCLUDE requires a valid EXCLUSION_REASON code; inclusion guidance is
not an exclusion code. INCLUDE and UNSURE cannot carry exclusion codes. Multiple
codes per vote/adjudication require explicit plan permission. Consensus exclusion
uses the plan's UNION rule, which can combine distinct reviewers' individually
valid codes without inventing a new code.

Every semantic decision ID hashes all immutable decision content except UTC.
Exact replay returns the original row. Changed input requires explicit supersession
of the current same-reviewer/candidate head, and the plan must permit corrections.
Both rows remain forever. An exact old replay remains historical; it does not
restore that old decision as current. Post-seal submissions/corrections fail.

BEGIN IMMEDIATE serializes write operations. Unique root and predecessor constraints,
tail/context checks and immutable triggers prohibit two effective heads or a fork.
Racing different corrections fail closed for the stale writer; they require refresh.
Concurrent identical submissions deduplicate. No application-level majority/random
choice resolves a write race. Use one Database per connection/thread.

Notes are annotations, not criteria. The frozen policy bounds them (0–4000 maximum
supported characters), or disallows them. Email/credential/header/config signatures
and compact token-like strings are rejected, with optional caller-supplied
runtime-only forbidden_values for additional known values. Screening never reads
credential files/environment to obtain those values. This is a defined signature
guard, not universal secret detection or encryption; annotations must remain
non-sensitive. Packet exports refuse to overwrite differing existing files,
including a human-completed decision template.

## Consensus, conflicts, adjudication and outcome versions

All assigned effective decisions must be present. Under explicit UNANIMOUS policy,
all INCLUDE gives AUTO_CONSENSUS SCREEN_INCLUDE; all EXCLUDE gives AUTO_CONSENSUS
SCREEN_EXCLUDE with code union. A missing reviewer stays PENDING. Mixed outcomes
are CONFLICT; all UNSURE is UNSURE. UNSURE is never coerced. DISABLED consensus
requires explicit manual resolution even for agreement. No majority vote or
scientific content rule is implemented.

EXPLICIT adjudication requires a frozen human adjudicator, all exact effective
reviewer IDs and a final INCLUDE/EXCLUDE with the same reason/note validation.
It is separate immutable evidence and cannot alter votes. PENDING assignments and
automatic consensus cannot be overridden by adjudication. Optional correction
requires the current adjudication predecessor and an allowed adjudicator; history
is retained. If a reviewer later corrects a vote, an adjudication citing old IDs
is stale and no longer resolves the candidate. A new explicit adjudication or
changed reviewer consensus is required.

Every successful decision batch or adjudication atomically materializes a new
immutable ScreeningResolution and normalized ScreeningOutcome rows, keyed by the
effective-input hash. Repeated same inputs reuse their materialization. This allows
pre-seal corrections without editing old outcomes: only the run's current-resolution
pointer changes. One active outcome per candidate contains SCREEN_INCLUDE,
SCREEN_EXCLUDE or UNRESOLVED; status FINAL/PENDING/CONFLICT/UNSURE; basis
AUTO_CONSENSUS/ADJUDICATED/PENDING; exact decision/adjudication IDs, codes and hash.
Historical snapshots are audit evidence, not extra current screening outcomes.

Run state recomputes as OPEN when votes are missing, REVIEW_COMPLETE when all votes
exist but at least one candidate remains unresolved, and RESOLVED when every
candidate has a final outcome. Corrections can move a pre-seal run back to an
earlier state while preserving all snapshots. resolve explicitly recomputes the
same projection. SEALED requires every assignment filled, no conflict/UNSURE/
pending outcome, and successful input/evidence/plan/outcome verification. It is
atomic; failure does not leave a falsely complete/sealed run.

Final semantic SHA includes revision/discovery hash, owning frontier/hash, plan,
candidate/assignment/unresolved-summary hashes, current projection and all immutable
decision/adjudication/materialization history. UTC fields are excluded where
appropriate. Verification recomputes from frozen claims and human evidence and
detects semantic tampering. SQL guards reject UPDATE/DELETE/REPLACE even with
recursive triggers disabled; lifecycle writes use a scoped connection-local guard.
They do not defend against an adversary replacing schema/UDFs or modifying the
database file. No distributed/power-loss certification is claimed.

## Offline packets and interfaces

Reviewer packet JSONL contains a manifest plus assigned candidates only, frozen
allowed evidence, allowed decisions/criteria/reasons and per-candidate hashes.
Peer decisions, notes and adjudications are never included, even after later votes.
Packet SHA commits manifest and all items. A separate JSONL decision template has
blank decision/reasons/note fields and the exact run/plan/candidate/assignment/
reviewer/evidence/packet hashes. No answer or recommendation is pre-filled.

Import requires an explicitly named reviewer, exact context fields, assignment,
vocabulary/reasons/notes and supersession semantics. The whole batch is one
transaction; any invalid row rolls back all changes and outcome materializations.
Exact batch replay is safe. Export-conflicts is explicitly adjudicator-scoped and
may include effective conflicting votes. inspect gives effective vote distributions,
completion, current conflict/UNSURE counts/rates, historical adjudication count,
active adjudicated rate, include/exclude/unresolved counts and final exclusion code
counts. Rates use assignment slots or canonical candidate count as appropriate;
empty completion=1, empty conflict/adjudication rate=0. No kappa/quality score is added.

`python -m reprovane.screening` commands: create, inspect, export-review-packet,
import-decisions, submit-decision, resolve, submit-adjudication, export-conflicts,
finalize, verify, handoff. All require --workspace; create needs --project-revision,
--discovery-run and --plan JSON; other commands use --run-id. Submission JSON files
contain the API keyword fields. Reviewer export/import use --reviewer and directory/
input. Conflict export uses --adjudicator. Every command is offline and has no
credential argument/resolution. Errors do not echo submitted private data.

handoff requires SEALED and returns screening manifest, final entity IDs separated
by SCREEN_INCLUDE/SCREEN_EXCLUDE, outcomes, reason codes and exact human decision
provenance. It is a read-only Stage10 interface. It creates no corpus tables or
release and implements no Stage11 policy.

Local validation blocks socket/HTTP/config/credential resolution, creates tiny
synthetic keyless upstream fixtures, exports/imports two reviewers' packets, records
one unanimous include and one conflict, explicitly adjudicates exclusion, seals and
verifies screening. It is implementation validation, not real human screening.
No AI, embeddings, metadata enrichment, PDF/full-text/Unpaywall, new resolver,
discovery/frontier operation or Stage11 implementation is added.
