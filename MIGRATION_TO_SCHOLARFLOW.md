# Migrate to ScholarFlow

Predecessor legacy v4.1.0 -> ScholarFlow 1.0.0 (candidate 1.0.0rc1).

Legacy baseline: f70796d1ef6003e24ce3afa1328088cc515dc0a0. Candidate product version is reported by `scholarflow --version` (1.0.0rc1 for this candidate).

V4 wrote flattened OpenAlex `_raw.jsonl` and `_raw.csv`, enriched CSV/JSONL, main/excluded/source-audit/preprint CSVs, checkpoint/summary/run-metadata JSON and fetch-oa state/log/manual/downloaded outputs and PDFs. Its raw filename does not mean exact original API bytes. A legacy main or excluded group is a historical machine filtering result, never a modern human screening decision. No legacy classification becomes ScreeningDecision, ScreeningOutcome, CorpusReleaseItem or a primary FullTextArtifact.

Keep the original directory unchanged and backed up. Migration never writes there, never resumes a legacy cursor, never extracts an archive and never fuzzy-matches a title or PDF filename. Use a separate non-overlapping target. Audit inventories all ordinary files, with documented exclusions for .git, .venv, venv and __pycache__. Symlinks fail closed. Finite scope defaults: 1000 files, 64 MiB/file, 256 MiB total, 100000 rows/file; oversized sources need explicit future adapter work, not silent partial import.

```powershell
scholarflow legacy audit --source "legacy results"
scholarflow legacy migrate --dry-run --source "legacy results"
# Write UTF-8 without PowerShell's historical UTF-16 redirect behavior:
scholarflow legacy plan --source "legacy results" --project-key imported-review | Set-Content -Encoding utf8 migration-plan.json
scholarflow legacy migrate --source "legacy results" --target "ScholarFlow workspace" --plan migration-plan.json
scholarflow legacy verify --workspace "ScholarFlow workspace" --migration-id <migration-id> --source "legacy results"
scholarflow verify --workspace "ScholarFlow workspace" --offline
```

Dry-run emits JSON only, creates no target, and includes source version if detectable, exact relative inventory/size/SHA, type, row and identifier coverage, duplicates, planned objects and unsupported/manual warnings. Version is read from VERSION or the actual run-metadata tool_version field. Missing evidence reports unknown; contradictory versions are reported without guessing. A detected 4.1.0 is not a cryptographic proof of source software origin. The plan binds inventory hash, explicit adapter versions, target project/revision, all import/secret/duplicate/fulltext policies and the separate-target destination policy. The actual absolute target path is a runtime argument excluded from semantic hashes. Execution recomputes the plan/source before writing and rechecks before completion. Record order and duplicate occurrences are preserved, not guessed away.

Safe `_raw`/`_enriched` rows with a valid Work ID become MANUAL_IMPORT ProviderRecords in a LEGACY_V4_IMPORT snapshot. Original file bytes, row ordinals and canonical row hashes are preserved. Complete attached Work objects, if present within those families, use the accepted OpenAlex typed normalizer. Actual v4 flat rows use the separately named legacy-v4-flat profile for explicitly present publication identifiers, metadata and venue descriptions. Flattened author strings and OA/venue ranking/status fields remain exact legacy evidence; no reconstructed authorship positions, collection membership or quartile authority is manufactured. Original OpenAlex request SHA stays null, explicitly LEGACY_INCOMPLETE_REQUEST_PROVENANCE; the hashed plan is the migration import spec, not a claimed original request. Query/cursor metadata stays historical. Stage03 generates/evaluates conservative identity candidates; ambiguous/conflicting rows retain Stage03 behavior. A later live acquisition uses a new Stage07 spec and fresh cursor identity.

LegacyMigrationRun/Artifact/RecordMap/Warning tables bind exact source fingerprint and plan to project/revision, snapshot and Stage03 resolution. Files containing recognized secrets/private paths are hash-inventoried but not copied; config/logs and unsupported artifacts are not copied. Detection is conservative (whole-file refusal can include false positives) and cannot recognize every unknown secret. Known runtime credential values are checked without printing. Filenames with secret-like content are anonymized and not copied. Reconfigure OPENALEX_API_KEY and UNPAYWALL_EMAIL in runtime/User environment; never paste values into the plan.

Main/excluded CSVs and venue/source statuses are historical-only artifacts. Downloaded PDFs are SHA-inventoried/copied only as LEGACY_FULLTEXT_UNBOUND evidence, without primary selection or matching. Modern full text requires an exact sealed corpus handoff and frozen Stage11B policy; manual ingest remains unimplemented. Unknown files are listed as UNSUPPORTED_ARTIFACT, not hidden. ZIP/archive inputs remain unsupported attachments; migrate an ordinary already-prepared directory.

A migration first commits an OPEN ledger entry, then transactionally imports the logical phase, verifies source/CAS/maps/observations/snapshot/Stage03/warnings and manifest, and commits COMPLETE. A failure rolls back semantic import and leaves OPEN (or an earlier pre-creation failure), never success; orphan CAS objects can remain from pre-registration placement. Same source+same plan into its owned target reuses verified completed state or resumes the rolled-back OPEN state. A different plan requires another target. Verification can check the target without the source, but source-drift verification requires `--source`.

Rollback: keep using the unchanged v4 source. Preserve an incomplete target for diagnosis; use a new target for changed inputs/policy. Never delete the original. For existing modern DB upgrades, use `scholarflow workspace upgrade` with separate source/target/backup directories (see operations). Backups do not include private configuration or duplicate immutable CAS; the upgrade target gets a verified CAS copy. Do not manually rewrite old migration hashes or promote old classifications into new decisions.
