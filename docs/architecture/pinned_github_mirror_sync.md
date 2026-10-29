# Pinned GitHub mirror synchronization (Stage05)

Stage05 is an explicitly invoked acquisition service. It creates a verified local
package for the accepted Stage04 importer. It does not update a scientific run,
integrate the current v4 CLI, infer source membership or perform screening.

## API and provenance

```python
from pathlib import Path
from scholarflow.mirror import GitHubClient, sync_scimago_from_github, import_synced_scimago

with GitHubClient(connect_timeout=10, read_timeout=60,
                  max_file_bytes=256*1024*1024, max_attempts=3) as client:
    result = sync_scimago_from_github(workspace, client=client)
package = Path(result.snapshot_directory) / result.repository_path
# Stage04 validate_local_package(package) or explicit ingestion:
for imported in import_synced_scimago(database, result):
    pass  # Each snapshot is already sealed before its typed observations.

# Offline branch uses explicitly identified last successful local state.
offline = sync_scimago_from_github(workspace, offline=True)
# Or identify an existing immutable snapshot by its full commit SHA:
offline_exact = sync_scimago_from_github(
    workspace, requested_ref=result.resolved_commit_sha, offline=True)
```

Defaults: tvquynh/wosaide-journal-lists, control, SCIMAGO. A branch is mutable.
resolve_ref performs one anonymous GitHub API commit request. A full40-hex
requested ref is also verified through that endpoint and must resolve to itself;
uppercase hexadecimal becomes lowercase. A short SHA can be a requested GitHub
ref but cannot be used as an immutable raw-fetch commit or offline exact identity.
All raw requests use that same full resolved SHA. File SHA-256 is a separate
exact-byte integrity key, not a Git commit/blob SHA.

import_synced_scimago is a generator composing the unchanged Stage04 source/APC
importers. It revalidates the package/control hashes before ingestion and passes
repository, original requested ref, resolved commit and each repository filename.
ProviderSnapshot.mirror_commit_sha is full/non-null and its immutable distribution
flag true. Import is explicit; syncing alone creates no research DB rows.

## Shared semantic plan

The only Stage04 source change extracts its existing pure manifest/checksum
semantics into parse_scimago_manifest_bytes, parse_scimago_checksums_bytes and
declared_scimago_files. Local validator and remote planner both call these helpers.
No observation, venue policy, temporal semantics or migration change.

Resolve once → fetch manifest at SHA → parse → fetch its declared checksum file
at SHA → establish all expected data hashes → inspect CAS → fetch only missing
or corrupt expected objects at SHA → materialize → Stage04 validate → promote →
update convenience state. Schema1 inclusive ranges and schema2 sorted annual
years, optional independent APC snapshots and dynamic counts retain Stage04 rules.
Extra safe checksum entries remain informational, preserved exactly and never
downloaded. README/repository_state are not required. Historical base_url is never
used to construct requests. Controls get separately computed SHA-256; upstream
checksum bytes are not rewritten to add control entries.

## Cache and immutable views

Layout under workspace/mirror:

```text
objects/sha256/<first2>/<full64hash>
objects/incoming/.object-<temporary>
snapshots/github/<owner>/<repo>/<full40commit>/<sha256(repository_path)>/
  <repository_path>/manifest.json
  <repository_path>/<declared checksum file>
  <repository_path>/<declared data files>
  sync_provenance.json
state/github/<owner>/<repo>/<sha256(repository_path)>.json
state/github/<owner>/<repo>/<sha256(repository_path)>.lock
```

The full path hash namespaces independently promoted datasets in the same commit.
The result snapshot_directory is the container above repository_path; default
snapshot_directory/SCIMAGO is a directly importable package. Repository names are
canonical lowercase. CAS objects may be shared across repositories only because
their exact bytes/hash agree; snapshot/state namespaces remain separate.

Objects are streamed through temporary files, hashed, flushed/fsynced and atomically
replaced after proof of the expected hash. Every reuse rehashes existing bytes.
Only an invalid object at the expected key is repaired after incoming proof;
valid objects are never overwritten. Snapshots use copies to isolate them from
CAS mutation, rather than unsafe shared writable hardlinks. Every copy is hashed.
Symlinks/junctions in cache paths are rejected, even within the cache tree.

Snapshot packages are built in sibling temporary directories, validated through
Stage04, given an outside-package provenance sidecar, and atomically renamed to a
previously nonexistent final directory. Completed snapshots are never overwritten.
Physical files are not OS access-control immutable: external owner modifications
are detected, not prevented. An externally corrupted completed snapshot fails
closed both online/offline; automatic snapshot reconstruction is deliberately
not implemented. Restore exact verified bytes through an operator-controlled
recovery before reusing that commit. Never silently replace historical evidence.

CAS corruption: online a new plan redownloads only bad/missing objects. For a valid
completed snapshot, online may repair its data CAS objects from exact-commit
downloads and controls from that already verified snapshot, without altering the
completed directory. Data repair reports UPDATED; a healthy same-commit snapshot
reports UP_TO_DATE after one ref request, with no control/data fetch. Offline
requires package, sidecar and all its CAS objects to verify; corruption fails.
All snapshot reuse does full hash validation; there is no weak metadata-only mode.

## Atomicity and state

The mutable pointer is marked convenience_metadata_only, and contains last SHA,
requested ref, canonical computed snapshot path, UTC time and control hashes. It
is written atomically only after completed snapshot promotion. Its path is never
trusted to direct file access: the namespace/commit compute the path. An explicit
commit snapshot works with missing/stale/corrupt state. Offline branch reuse
requires valid state for that requested ref, reports OFFLINE_REUSED and the actual
stored SHA, and does not claim a new remote resolution or update state.

Failed download/hash/validation/promotion leaves prior state and snapshots intact.
Verified orphan CAS objects are reusable after failed attempts. If the state write
fails after this call's new promotion, that newly promoted unreferenced directory
is removed; existing completed snapshots are never removed by this rollback.
An abrupt process/power failure between rename and state may leave a complete
unreferenced snapshot, which can later verify/reuse; no power-loss certification.
File fsync is used, directory fsync is best effort where supported. On Windows
materialized files must be opened writable for fsync; tests cover that runtime.

An exclusive per-repository/subpath lock rejects concurrent sync writers; stale
locks after a crash require manual inspection/removal. No distributed locking,
automatic stale-lock eviction or multi-machine writer guarantee. Arbitrary hostile
concurrent filesystem owner mutation is outside this local cache boundary.

## Network and errors

requests.Session, HTTPS only, internally constructed api.github.com and
raw.githubusercontent.com endpoints; redirects disabled. Anonymous operation;
no token API, no OpenAlex credential/email read, trust_env=False disables implicit
netrc credentials/proxy settings. Explicit User-Agent and identity content encoding;
connect/read timeouts default10/60 seconds. Files default256 MiB, controls/API
1 MiB; all limits are API parameters. Actual streamed bytes are bounded even when
Content-Length is absent or encoded. Default3 attempts (configurable1..10), retry
only429/500/502/503/504 and connection/read/connect/truncated-stream failures.
Numeric Retry-After is capped at max_retry_sleep (default10 seconds); malformed,
negative/nonfinite values use bounded exponential backoff. Sleep is injectable.
TLS errors, ordinary permanent HTTP errors and redirects fail without retry.

Retries restart the destination temporary file and hash; no partial bytes are
promoted. Error classes distinguish resolution/fetch/integrity/offline/cache/state.
Errors include only safe endpoint/status/attempt descriptions, never response
bodies, request headers, environment or underlying network exception messages.
Repository/ref/path validation occurs before network access; POSIX relative paths
reject traversal, URL/query/ADS/backslash forms and Windows reserved names.

The generic client can fetch opaque WOS/SCIE.csv or SCOPUS/ext_list_Jul_2026.csv
at an exact commit and store/materialize bytes. Stage05 provides no parser or
membership semantics for either; Stage06 owns those. No Git executable/subprocess,
new dependency, ORM, GUI, browser/AI or DB migration is introduced.

## Annual operation and later stages

For year2026 the operator manually exports scimagojr-2026.csv, uploads it under
SCIMAGO, computes/adds its SHA-256, updates manifest years/latest and commits GitHub.
Older exports stay unchanged. Sync resolves the new commit, fetches controls,
reuses2010..2025 expected objects and downloads2026 only; optional Journal Value
follows its independent year/hash. Stage04 imports the selected snapshot when
explicitly requested. No code/config change. The synthetic2022/2023→2031 scenario
tests exactly this behavior. Unsupported semantic schema drift still fails closed.

Current v4 sync-scimago/run/enrich/etc remain isolated. Stage12 will expose final
CLI workflow; Stage06/07 acquisition semantics, screening and corpus releases remain
out of scope. Tests use synthetic mocked transport, never live network. Optional
live smoke was not run; no production dataset download or remote write was made.
