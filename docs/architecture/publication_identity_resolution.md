# Conservative publication identity resolution (Stage03)

PublicationEntity is a run-scoped cluster judged to describe the same bibliographic
publication/manifestation. It is not the same scientific study, conceptual work,
topic, authors, preprint/journal family or conference/journal family. False merge
is more dangerous than false split. Provider records and typed observations are
never deleted, rewritten or moved into a surviving record.

## Feature boundary

ResolutionStore opts into schema4. Bare Database still defaults to1, and
ObservationStore targets3. Migration0004 adds seven tables and three views without
changing0001/0002/0003 or any current CLI. New code uses Python standard library
and SQLite. No provider network call, raw payload parsing, fuzzy matching,
embedding, AI, author/venue identity, discovery traversal, screening or release.

```python
from pathlib import Path
from reprovane.core.db import Database
from reprovane.resolution import ResolutionStore

with Database(Path("workspace")) as db:
    resolution = ResolutionStore(db)
    selector = {"profiles": [{"normalizer_name": "example-normalizer",
        "normalizer_version": "1", "observation_schema_version": "1"}]}
    # revision_id and record_ids must already exist; record snapshots must be sealed.
    # run = resolution.create_resolution_run(revision_id, selector)
    # rid = run["resolution_run_id"]
    # resolution.add_resolution_inputs(rid, record_ids)
    # resolution.generate_candidates(rid)
    # resolution.evaluate_candidates_automatically(rid)
    # Review NEEDS_REVIEW pairs, appending explicit manual supersessions as needed.
    # resolution.materialize_resolution_run(rid)
    # Optional manual/imported directed PublicationRelation claims here.
    # resolution.seal_resolution_run(rid)
```

## Policy and explicit observation selection

Every run stores canonical policy JSON, its SHA-256, policy name/version and
resolver name/version. conservative_publication_v1 enables provider-native ID
and singleton shared valid DOI as the only possible automatic SAME evidence.
No automatic DISTINCT. The built-in semantics cannot be replaced by arbitrary
caller JSON; only an integer block limit>=2 can vary (default25). Canonical JSON
is UTF-8, ensure_ascii=False, sorted keys, compact separators, no NaN.

ObservationSelector records exact normalizer_name/normalizer_version/
observation_schema_version tuples. Profiles are unique and sorted canonically.
There is no latest-version or timestamp preference. Publication metadata,
publication identifiers, ordered authorship and venue identifiers are read only
from selected typed rows. Native ID/semantic namespace come from immutable
ProviderRecord/ProviderSnapshot/Provider metadata, not raw provider JSON.

Adding an input freezes its extracted evidence_json and source observation IDs.
Later-appended observations are not silently adopted by that run. Start a new run
to select them. This additional input evidence snapshot is immutable and is hashed
into the input manifest. It avoids even a singleton's selected observation lineage
being lost. All batch input writes are atomic.

An ambiguous selected metadata set (>1 row) warns AMBIGUOUS_SELECTED_METADATA;
all its title/year keys may create candidates but it cannot automatically merge.
Duplicate selected author positions warn AMBIGUOUS_AUTHORSHIP_POSITION. This v1
policy conservatively blocks automatic SAME on any such pair, even if another
strong key exists; it does not silently select an author profile or row. Authorship
retains position/time/observation-ID order. Family name is a separate feature when
provider-supplied; display-name normalization is the fallback, never person identity.

## Versioned evidence transforms

doi_normalizer_v1 trims/casefolds the DOI, strips one common doi:/DOI URL prefix,
and accepts only ^10\.[0-9]{4,9}/\S+$. Invalid claims keep their observation IDs
but do not enter the valid DOI set. No DOI is invented. Duplicate equal DOI
claims collapse only in this derived feature set; all observations remain.

publication_title_normalizer_v1 performs NFKD, removes combining marks, casefolds,
expands & to and, replaces nonalphanumeric runs with spaces, collapses whitespace.
author_name_normalizer_v1 uses the same Unicode/punctuation process without &
expansion. Unicode alphanumeric characters are retained. No stemming, synonyms,
similarity threshold or fuzzy utility. publication_type_classifier_v1 recognizes
exact normalized notice tokens retraction/retraction notice/correction/correction
notice/erratum/expression of concern/withdrawal/removal; others remain
OTHER_OR_UNKNOWN. This is a safety classifier, not a universal type ontology.

## Blocking and decisions

Maps use scoped provider-native IDs, valid DOI, exact normalized title+year and
exact title+first-author+year. Blank native IDs are unusable. Before any pairs are
expanded, EVERY block is checked against the policy limit. An oversized block
raises CandidateBlockTooLargeError(method,size,limit) without the sensitive key;
no candidates or completion marker persist. There is no global N² pair pass.
Pairs use min/max internal record ID and sorted unique method codes. Duplicate
methods are merged in memory before insertion. Candidate generation is one atomic
operation, ordered by pair; subsequent calls verify and reuse it. Completing
generation freezes inputs (an earlier lock than materialization), because an
append-only candidate set must represent the entire fixed input set.

Hard conflicts: disjoint nonempty valid DOI sets; more than one distinct valid DOI
on either record; NOTICE vs OTHER_OR_UNKNOWN; ambiguous selected metadata; duplicate
selected authorship positions. A hard conflict always yields automatic AMBIGUOUS.
Otherwise scoped equal nonblank provider-native ID may yield SAME. DOI SAME
requires BOTH sets equal the same singleton, not merely a nonempty intersection.
Exact title/year/author evidence alone always remains AMBIGUOUS. No automatic
DISTINCT, even for different DOIs or a correction notice. Candidate evidence stores
features, source observation IDs and normalizer versions, without abstracts,
provider response JSON, retrieval URLs or configured credentials.

## Ledger, supersession and review queue

EntityResolutionDecision rows are immutable; IDs are deterministic hashes of
decision contents excluding creation time. Manual SAME/DISTINCT/AMBIGUOUS requires
explicit actor_id and reason_code. There is no OS-username inference. An active
decision must be explicitly named as supersedes_decision_id. A prior row can have
one direct child only; self-reference, missing history and wrong-pair supersession
are rejected. Existing-reference-only insertion plus immutable unique IDs makes
cycles impossible. SQL guards enforce these rules independently of the service.

Cross-run supersession is allowed only for the identical unordered record pair
from a prior SEALED run. It cannot reach into an OPEN/MATERIALIZED-unsealed run.
The active decision view is globally unsuperseded, so a cross-run correction can
make an old candidate have no globally active decision. The old run's immutable
decision snapshot and projection remain independently verifiable: verification
uses its frozen decision IDs/contents, not a mutable global-current lookup.

resolution_review_queue derives UNRESOLVED, AUTO_MATCHED, AUTO_DISTINCT,
NEEDS_REVIEW, MANUAL_MATCHED and MANUAL_DISTINCT. IMPORTED nonautomatic claims map
to the corresponding manual status. AUTO_DISTINCT is unreachable under v1.
The view also exposes materialized/sealed timestamps; consumers should filter to
OPEN runs for actionable review. Historical closed queues can show UNRESOLVED after
cross-run supersession; this does not invalidate the frozen historical projection.

## Atomic projection and hashes

Materialization requires OPEN, nonempty input, completed generation and exactly
one active decision for every persisted candidate. AMBIGUOUS is allowed if it
does not fall inside a SAME connected component. Deterministic union-find uses
SAME edges only, then checks every DISTINCT/AMBIGUOUS pair. A SAME B, B SAME C with
A DISTINCT C or A AMBIGUOUS C raises ResolutionConflictError before projection.
No transitive merge can erase explicit disagreement/uncertainty.

Every input receives exactly one membership, including singleton entities.
membership_sha256 = SHA-256(canonical_json(sorted(member_record_ids))).
entity_id = pub_ + first32 lowercase hash hex. Composite PK(run_id,entity_id)
allows the same deterministic cluster ID in different runs, without claiming
global eternal truth. Hash-prefix collision fails closed via the PK/guard.
membership_basis_json records component hash and incident active SAME decision IDs;
it is audited against the projection on verification, not a second canonical fact.

input_manifest_sha256 hashes sorted record IDs, policy/selector hashes,
resolver name/version and a SHA-256 over sorted frozen input evidence objects.
materialization_manifest_sha256 hashes that input hash, sorted active decision
IDs/contents (excluding created_at), sorted member lists, policy/selector hashes
and resolver version. No operation timestamp enters these hashes. Candidate and
decision IDs are run-scoped content hashes, so different runs with different
decision identities can have different materialization hashes even when cluster
IDs match; the promised determinism is for fixed run/inputs/config/observations.

Entity/membership insertion, input/materialization hashes, frozen decision
snapshot and materialized timestamp commit in one SQLite transaction. High-level
operations use a savepoint when inside a caller transaction, so caught exceptions
cannot leave partially inserted projections that the outer caller later commits.
No inputs/candidates/decisions or policy changes after materialization; changed
identity decisions require a new run.

## Relations and sealing

PublicationRelation is directed evidence between DISTINCT entity IDs in the same
MATERIALIZED but unsealed run. Allowed types: PREPRINT_OF, ACCEPTED_MANUSCRIPT_OF,
REPOSITORY_COPY_OF, CONFERENCE_PRECURSOR_OF, EXTENDED_VERSION_OF, CORRECTION_OF,
RETRACTION_OF, POSSIBLY_RELATED_VERSION. SAME_PUBLICATION is forbidden. Public
relation modes are MANUAL/IMPORTED only; no automatic relation inference. Manual
actor/reason are explicit. Supersession is append-only, same run and same ordered
source/target pair, with at most one direct superseder. Reversing direction is a
separate claim, not a silent type/direction collapse.

Sealing first verifies frozen decisions, materialization/candidate/input hashes,
membership completeness/identity/count/basis and relation structure. It atomically
sets sealed_at plus a separate relation-history hash (excluding timestamps), so
even valid-looking later relation tampering is detectable. SEALED run updates,
relations and supersessions are forbidden. Second seal raises ResolutionStateError.
verify_resolution_run accepts MATERIALIZED or SEALED; OPEN is not a completed
projection. It never silently repairs state.

## Database protections and limits

All seven tables have delete/replacement guards; all non-run rows reject UPDATE.
Duplicate guards cover every alternate unique key, not just row IDs, so REPLACE
with recursive_triggers OFF cannot bypass append-only semantics. Lifecycle phases
use a connection-scoped authorization UDF plus exact old/new field checks. Entity
insertion is restricted to the controlled materialization phase. External
connections without UDFs fail closed on guarded operations. SQLite owners can
still drop triggers/replace UDFs; this is a trusted local-store DML boundary.

One ResolutionStore should own lifecycle operations on a given connection;
separate connections are appropriate for workers. Foundation SQLite pragmas and
thread affinity remain unchanged. Candidate expansion is bounded; graph closure
is local union-find, not a graph database. Lists currently materialize rows in
memory. No POSIX/power-loss/multiprocess stress or live-provider integration is
claimed by the offline Windows test evidence.

Future venue resolution, author disambiguation, global work families, discovery
graphs, screening, release/freeze, full-text integration and AI remain deferred.
