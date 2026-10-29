# Persistence foundation (Stage 01)

This isolated standard-library layer stores project revisions, provider snapshots,
immutable source bytes and provenance. It is unused by the v4.1 CLI. There is no
scholarly observation schema, publication dedup, entity-resolution decision,
discovery run, graph, screening integration, release workflow or AI here.

## Workspace and API

An explicit `pathlib.Path` workspace owns `db/scholarflow.sqlite` (plus SQLite WAL/SHM)
and `raw/sha256/<first-two-hex>/<64-lowercase-hex>`. Bytes have no URL-derived
filename. Workspace paths are resolved; artifact hashes and symlink escapes are
checked. Local workspace directories must be trusted against concurrent hostile
filesystem modifications. Standard-library symlink checks are not a sandbox.

```python
from pathlib import Path
from scholarflow.core.db import Database
from scholarflow.core.foundation import Foundation

with Database(Path("workspace")) as db:
    service = Foundation(db)
    project = service.create_project("example_review", "Example review")
    revision = service.create_project_revision(
        project["project_id"], 1, "yaml", "topic: example\n")
    provider = service.register_provider("example", "Example provider")
    snapshot = service.create_provider_snapshot(
        provider["provider_id"], "example-data", "MANUAL_IMPORT",
        parser_name="example-import", parser_version="1",
        schema_profile="opaque", schema_version="1")
    artifact = service.artifacts.put_bytes(b'{"items":[]}')
    with db.transaction():
        service.register_raw_artifact(artifact, media_type="application/json")
        service.attach_artifact_to_snapshot(
            snapshot["snapshot_id"], artifact.artifact_sha256, "response", 0)
        # Add located provider records here if the payload actually contains any.
    service.seal_provider_snapshot(snapshot["snapshot_id"])
    assert service.verify_provider_snapshot(snapshot["snapshot_id"])
```

`Foundation` exposes create/get project, create/get/list revisions,
register/get provider, create/get snapshot, register raw artifact, attach artifact,
add/list provider records, seal/verify snapshot, append/list audit events. Return
objects are detached `dict[str, Any]` rows; `StoredArtifact` is a frozen dataclass.
Get methods take internal IDs and raise ValidationError when absent. Lists are
ordered. No provider is seeded. Registration of the same artifact is idempotent
when immutable metadata agrees; supplied conflicting media/compression is rejected.

IDs use UUID4 hex with `prj_`, `rev_`, `prov_`, `snap_`, `rec_`, `evt_`. Persisted
service timestamps are explicit UTC ISO-8601 with microseconds and `Z` suffix.
Project manifests preserve exact UTF-8 text (including whitespace/BOM/newlines),
hash those exact bytes, and do not parse or normalize YAML/JSON. A parent must
belong to the same project and have a lower revision number. Empty manifest text
is allowed; format is a descriptive label, not a parser selector.

## Connection and transaction contract

Each writable Database connection uses foreign_keys=ON, journal_mode=WAL,
synchronous=FULL, busy_timeout=5000 and recursive_triggers=ON. Required WAL/FK
availability is checked. `isolation_level=None` disables implicit transaction
management; `db.transaction()` uses BEGIN IMMEDIATE / COMMIT and rolls back on
any escaping BaseException. Explicit nested transactions raise FoundationError.
Service writes join an existing caller transaction, otherwise open their own;
callers must let errors escape their transaction if the whole batch should roll
back. A single connection is thread-affine; use separate Database objects for
concurrent workers. BEGIN IMMEDIATE serializes migrations and service writers.

Database.execute maps constraint failures to ValidationError, immutable guards to
ImmutabilityError, and sealed guards to SnapshotSealedError. The public raw
connection is available for inspection/tests and raises native sqlite3 exceptions.
Applications should use the service methods. Missing/corrupt bytes raise
ArtifactIntegrityError. Errors do not echo SQL arguments or locator values.

## Migrations

`migrations.py` discovers contiguous `NNNN_name.sql` files starting at 0001.
The narrow .gitattributes rule pins only foundation SQL migrations to LF, so Git
checkout on Windows/POSIX preserves their exact checksum bytes.
`schema_migration` records version/name/exact-byte SHA-256/UTC application time.
All historical versions/names/checksums and user_version are validated before any
upgrade; changed/missing history raises MigrationDriftError. All pending DDL,
ledger inserts and PRAGMA user_version updates share one explicit transaction,
including bootstrap on a fresh database. Failure rolls the whole pending batch
back. Reopening an up-to-date database changes no rows. No downgrade support.

SQL statements are split with sqlite3.complete_statement, including complete
trigger bodies; executescript is not used. Migration transaction control,
connection PRAGMAs, attach/detach and vacuum are rejected. These are trusted
shipped migrations, not an untrusted SQL execution interface. SQLite JSON
functions are required; missing json_valid makes migration fail closed.

## Immutability and snapshot lifecycle

Review projects, providers, revisions, raw registry rows, links, records and audit
events reject UPDATE and DELETE through SQLite triggers. Duplicate-insert guards
also prevent INSERT OR REPLACE from bypassing immutability even on external
connections with recursive_triggers OFF. Unique constraints and foreign keys
remain active. Administrative schema changes (DROP TRIGGER, replacing UDFs,
disabling constraints) are outside this trusted-local-storage threat model.

A provider snapshot is created OPEN, with no manifest hash or seal time. OPEN
metadata is fixed too: the only permitted UPDATE sets both content hash and seal
timestamp through the service's connection-scoped authorization UDF; all other
columns must remain byte-for-byte equal. Direct ordinary SQL cannot authorize a
seal. External connections without foundation UDFs fail closed. Snapshot deletion
is blocked in both states. A sealed snapshot rejects every update/delete and
new links/records. A second seal raises SnapshotSealedError; there is no reopen.

Sealing holds a SQLite write transaction, verifies every attached artifact's
hash/size/path, checks that every record references an attached artifact, validates
all snapshot/link/record JSON fields, and hashes the content manifest. The service
sets seal time and hash together. Verification requires a SEALED snapshot and
repeats artifact/manifest verification; it does not silently repair corruption.
Empty snapshots may be sealed. No scholarly completeness guarantee is implied.

Manifest format `foundation.snapshot.v1` uses UTF-8 JSON, ensure_ascii=False,
sorted keys, compact separators, allow_nan=False. It includes provider_id,
dataset, acquisition mode, upstream release, sanitized source locator, request
hash, mirror commit, parser/schema name+version, retrieval time, notes and parsed
snapshot metadata. Attached descriptors include role, ordinal, artifact hash,
sanitized locator and parsed request/response metadata, sorted by role/ordinal/hash.
Record descriptors include internal/native IDs, artifact hash, locator, ordinal,
optional record-content hash, observation time and parsed metadata, sorted by
locator/internal ID. Snapshot ID, seal time and artifact-link creation time are
excluded. Identical logical content with identical IDs/timestamps hashes equally
regardless of SQL insertion order. New UUIDs/times represent different content.
No locale formatting or semantic manifest-text normalization is involved.

ProviderRecord is an immutable *observation location*: native IDs can repeat
within and across snapshots/providers. Only snapshot+record_locator is unique.
The artifact must already be attached to that snapshot. Optional record-content
hash is caller-supplied provenance; this opaque layer cannot extract/verify a
record's byte range or JSON pointer. It does not merge publications or infer versions.
AuditEvent is append-only with SYSTEM/MANUAL/IMPORTED actors and optional existing
supersedes reference. It is not an EntityResolutionDecision and does not enforce
domain-specific entity identity. Events are explicitly appended by callers;
service operations do not automatically fabricate an event policy.

## Artifacts and separate durability domains

put_bytes/put_file hash exact binary bytes while streaming to a same-filesystem
temporary object, flush+fsync it, then atomically os.replace into the hash path.
Existing hash paths are verified, never silently repaired or overwritten on
detected mismatch. Duplicate content reuses bytes. verify reads and rehashes the
file. Directories are fsynced on POSIX where supported; portable Windows directory
fsync is unavailable. File fsync is mandatory. Filesystem/SQLite failure behavior
still depends on the actual disk and filesystem durability guarantees.

Filesystem artifact placement and SQLite transactions are separate durability
domains. A DB failure after placement may leave an unreferenced immutable object;
this is acceptable and safer than a committed DB row pointing to unplaced bytes.
There is no garbage collection or distributed/two-phase filesystem transaction.
Later acquisition should use this ordering:

1. Persist and fsync the immutable raw artifact.
2. Begin a SQLite transaction.
3. Register artifact, provider records and cursor state (cursor tables are **not**
   implemented in Stage 01).
4. Commit the database transaction.

Stage 01 does not change v4's checkpoint-before-JSONL behavior. External file
tampering/deletion after commit remains detectable by verify, not preventable by
a SQLite transaction. Keep raw bytes until later retention policy is approved.

## Security and later use

source_locator on snapshots and artifact links strips URL userinfo and replaces
case-insensitive api_key/apikey/access_token/token/key/sig/signature/password/passwd/
x-amz-signature/x-goog-signature query values with REDACTED. Non-sensitive query
values/order, path and fragment structure remain; percent encoding may normalize.
Non-URL locators use the existing generic secret redactor. Invalid URL errors are
generic and never include input. SQL CHECKs require a sanitized representation.
No original credential locator is stored alongside it. Raw bytes are deliberately
unmodified; this helper cannot scrub arbitrary secrets from provider payloads,
free-text notes, metadata JSON, paths or unknown query keys. Callers must avoid
putting credentials in those fields. This is provenance sanitization, not a
retrieval URL vault. Current fetch-oa logging is unchanged.

Later stages should append immutable observations and build their own typed
interpretations/decisions above this layer, not update provider records. Extend
schemas via a new forward migration; never edit migration 0001 after acceptance.
No OpenAlex page capture, SCImago migration, PublicationEntity, venue model,
identity ledger, graph, screening redesign, corpus release or full-text queue is
implemented or connected by this stage.
