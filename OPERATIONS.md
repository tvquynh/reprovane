# ScholarFlow operations

Use Python 3.11+. Install the candidate wheel and declared requests>=2.31,<3, pypdf>=6,<7 dependencies. For offline installation, prepare those dependency wheels locally first and run `python -m pip install --no-index --find-links <wheelhouse> <candidate-wheel>`. No imports/help/version execute research requests. Standard build tooling is build/setuptools/wheel, not runtime dependencies. No editable install is required.

## Workflow and CLI routing

`scholarflow --help` lists coherent command families. Existing module CLIs are thinly routed without scientific reimplementation. Use each family --help for its exact required arguments.

1. `workspace init --workspace <workspace>` creates schema through the current migration. `project create --key <key> --display-name <name> --workspace <workspace>` and `project revision --project-id <id> --number 1 --manifest <json> --workspace <workspace>` establish explicit review context. Offline.
2. `scimago --workspace <workspace> --ref <pinned-ref> --import-snapshot` uses accepted pinned sync/import; `venue-data sync/import/resolve/query` handles the separate Scopus/WoS evidence families. Sync uses network unless --offline; import/resolution is offline. No universal indexed field is inferred.
3. `acquisition --help` routes Stage07 bounded OpenAlex specs. Acquisition requires runtime OPENALEX_API_KEY and network; inspect/verify do not. No v4 cursor crosses into Stage07.
4. `resolution create/add/generate/evaluate/materialize/seal` and `venue-resolution ...` delegate accepted Stage03/04. Supply an explicit observation selector JSON. Offline; ambiguous cases need explicit accepted API decisions, never title/fuzzy auto-merge.
5. `discovery create/register-primary/resolve-seeds/.../seal` preserves routes and membership basis. Only explicit bounded expansion commands use network. Each acquisition must be in the exact revision.
6. `frontier` and `search-validation` route Stage09 policies and audits. Planning/inspection/finalization are offline; explicit execution uses bounded accepted acquisition. Search validation does not decide screening/corpus.
7. `screening create/export-review-packet/import-decisions/submit-decision/.../finalize` requires human provenance. Offline; no automated screening. Review packets refuse conflicting bytes and symlinks.
8. `corpus create/materialize/finalize/verify/export/handoff` requires exact sealed screening. Offline immutable membership/metadata snapshots. Excluded outcomes never enter corpus. Use a new export destination for changed bytes.
9. `fulltext create/discover/acquire/finalize/verify/export` binds one sealed corpus. Discover/acquire may use network and runtime OPENALEX_API_KEY/UNPAYWALL_EMAIL; enabled Unpaywall needs its email. Defaults allow published/accepted, reject submitted/UNKNOWN, including cached OpenAlex PDFs with unknown linkage. Verification is offline. Manual queue remains inside the complete corpus; manual ingest is not implemented. `export-pdfs` creates an optional same-byte-safe convenience view.
10. `verify --workspace <workspace> --offline` verifies all sealed layers and CAS bytes; `doctor --offline --workspace <workspace> --full` diagnoses runtime/schema/FKs/dependencies/CAS and all sealed layers. Default doctor samples one sealed object per family; --full verifies all. Project/family inspect and verify use disposable DB snapshots and leave workspace bytes unchanged. No credential values are read for doctor; only process presence by name is shown, with User-env availability deliberately not inferred.

ScholarFlow exposes only the scholarflow product CLI. Historical command implementations are test-only references; legacy migration is available through scholarflow legacy audit/plan/migrate/verify. Historical execution remains a predecessor responsibility (see PROVENANCE.md).

## Locks and read-only snapshots

Modern mutating module/root CLI workflows use one OS-held lock per workspace (`.scholarflow-writer.lock`). The file contains a PID and a non-secret format/state label, not credentials. Presence alone never implies an active owner. OS handle close/process exit releases ownership after crashes; inactive metadata remains for diagnosis. A second mutating CLI process fails safely. Read-only snapshot operations refuse active managed writers. Direct Python APIs remain composable; callers must hold `workspace_lock()` around multi-step mutations. Unmanaged old processes and distributed/untrusted filesystem races are not certified; stop them before snapshot/upgrade. Copy DB+WAL bytes to a temporary directory, compare source hashes before/after, recover only the copy, read CAS from the source. Recovery/migration registration on the copy never upgrades the original. Doctor's writability check is permission-based and creates no probe files.

## Backup, upgrade and recovery

```powershell
scholarflow doctor --offline --workspace "current workspace"
scholarflow workspace upgrade --workspace "current workspace" --target "upgraded workspace" --backup "verified DB backup"
scholarflow verify --workspace "upgraded workspace" --offline
```

All three directories must be separate and non-overlapping. Current workspace remains unchanged. Check historical hashes before upgrade; SQLite backup API creates a complete DB copy (including recovered WAL state), verifies its SHA and records applied migration history. All authoritative layer manifests are in the DB. Private config/logs are never copied. Immutable CAS is omitted from the backup to avoid unnecessary duplication, but copied to the new operational target and verified there; retain the original CAS together with the DB backup for restore. No claim is made that a DB-only backup independently contains full-text bytes. A complete backup plan must separately preserve immutable CAS. Apply only additive migrations to the target, check FKs and all sealed objects, then write an upgrade manifest. Failed targets without a verified manifest are incomplete. Verified exact replays reuse target after checking source fingerprint/backup hash; changed source/backup requires a new destination. Never manually edit old migration history. Restore by copying verified backup DB to a new db/scholarflow.sqlite path with its original CAS, then doctor/verify; the original is the rollback source.

## Output and exit behavior

JSON/JSONL/Markdown use UTF-8; deterministic CSV uses UTF-8/LF. Candidate/migration semantic outputs contain relative paths and CAS locators, not absolute machine paths. CLI errors omit private exception/input values. Root CLI: 0 success, 2 validation/configuration or lock failure, 3 integrity/history mismatch, 4 root SCImago transport/operational failure, 5 migration execution failure. Existing delegated family CLIs retain their documented safe code 2 (including failed acquisition); legacy codes remain compatible. No process exit code means a scientific inclusion/exclusion decision. Network failures in delegated acquisition are diagnosed in persisted operational status, not stderr payload dumps. There is no universal remapping of old exit codes.

## Candidate reproducibility and limitations

Run tests with `python -m unittest discover -s tests -q`. Stage12's synthetic E2E uses fixed fixture-only ID/time factories to compare exact request/policy/item/manifest hashes and exports across fresh workspaces. Production operational IDs remain random and participate in accepted upstream manifests; independent production runs are different provenance contexts and are not promised identical hashes. No scientific hash implementation is changed for comparison. Windows validation covers PowerShell, spaces/Unicode/nested paths and exact LF migration/binary PDF bytes. Finite migration limits and small bounded stress fixtures are intentional, not a large-dataset benchmark. No signature/cryptographic source authenticity is implied by portable checksums. Candidate preparation creates no tag, push, merge or external publication.
