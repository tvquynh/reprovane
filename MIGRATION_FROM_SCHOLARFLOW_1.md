# Moving from ScholarFlow 1.x to Reprovane

This guide is for existing ScholarFlow users. New users should start with
[Quick Start](QUICKSTART.md), then [User Guide](USER_GUIDE.md).

The distribution `scholarflow`, command `scholarflow` and `import scholarflow` were
replaced by `reprovane`, `reprovane` and `import reprovane` in 2.0.0. No compatibility
package or old-command alias is installed. Reprovane 2.0.1 keeps that identity.
Schema remains 13; no migration 0014 is needed for the rename or documentation update.

## Before switching

1. Stop all workspace writers, including notebooks/scripts. Never run old and new
   writers concurrently against the same workspace.
2. Retain your old environment and package for reproducibility. Historical v1 stable
   and release-candidate tags, releases and assets remain unchanged.
3. Back up the entire stopped workspace, database and content-addressed storage (CAS)
   together. A database alone does not preserve all evidence/PDF bytes.
4. Check the old workspace fully, save results and trusted hashes, and investigate
   any existing integrity problem before switching.

## Test a separate copy

Install Reprovane in a new environment following Quick Start. The following
PowerShell example assumes its software folder is current; `$ws` is your verified
workspace copy, not your only original:

```powershell
$rp = ".\.venv-reprovane\Scripts\reprovane.exe"
$py = ".\.venv-reprovane\Scripts\python.exe"
$ws = "$HOME\Documents\existing-review-copy"
& $rp --version
& $rp doctor --offline --workspace "$ws" --full
& $rp verify --offline --workspace "$ws"
```

Expect the installed Reprovane version (2.0.1 in this guide), schema 13 and no
pending migrations/foreign-key violations. On a launcher-restricted enterprise
host, use `& $py -m reprovane` instead of `& $rp`, as explained in Quick Start.
Checks use a temporary database snapshot, not a scientific-data conversion.
No decisions are resealed, corpus memberships changed or PDFs reranked.

Do not run legacy migration or workspace upgrade just for this name change.
The legacy adapter handles predecessor v4 evidence, not ScholarFlow 1 workspaces.

## Retain compatibility paths

Keep `db/litintel.sqlite`, `.litintel-writer.lock`, CAS paths, revision IDs, sealed
manifests and old hashes. Earlier documentation incorrectly named the actual lock
`.scholarflow-writer.lock`; the public implementation uses `.litintel-writer.lock`.
Do not rename it or add `.reprovane-writer.lock`. Do not replace product names inside
sealed database/JSON bytes. Removing a lock file is not a safe unlocking procedure.

Update your own imports/command calls to `reprovane`, preserving all supported
arguments. Do not change `OPENALEX_API_KEY`, `UNPAYWALL_EMAIL` or provider identifiers.
Do not install an unrelated same-named registry package as an old-namespace shim.

Adopt the new environment for real data only after copy checks succeed. To return
to an old environment, stop the new writer and use a verified compatible backup;
do not edit migration history. The canonical repository is
[tvquynh/reprovane](https://github.com/tvquynh/reprovane). GitHub's old repository URL
redirect does not redirect distribution/import/CLI names. Reprovane 2.0.0 is already
published. Reprovane 2.0.1 preserves the workspace format and compatibility paths
described in this guide.
