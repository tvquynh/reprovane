# Operating Reprovane safely

Use [Quick Start](QUICKSTART.md) for installation and [User Guide](USER_GUIDE.md) for
review work. Reprovane 2.0.0 is published; this branch prepares 2.0.1 without publishing
it. Installation version and data-format version are different concepts.

## Find the right workspace

The software environment contains the program; the workspace holds research data.
Record the full workspace address, software version, project/revision/run identifiers
and manifest fingerprints in your team notebook. Keep quotes around addresses with
spaces. The following terminal examples assume the installed launcher is available;
beginners should use the direct paths from Quick Start instead.

```bash
reprovane --version
reprovane doctor --offline --workspace "research workspace" --full
reprovane verify --offline --workspace "research workspace"
```

Doctor reports schema 13, pending migrations, foreign-key violations and sealed-object
checks. `--full` checks all sealed objects; without it doctor samples one per family.
Zero sealed objects is normal for an empty workspace. `database: ABSENT` means check
the address first; it is not a reason to delete or initialize over real work.
Read-only inspect/doctor/verify use a temporary database snapshot without changing
source bytes. Configuration status reports only process-variable presence, not keys.

## Network and interruption

Workspace/project, resolution, screening, corpus verification and exports are offline.
OpenAlex acquire/resume, citation expansion, provider sync and full-text discovery/
acquisition may use the network. Search validation operates on existing evidence.
Save each successful ID before the next operation. After interruption, inspect the
saved run and use its supported resume/state transitions. Never assume a failed
response means no object was created. See User Guide for acquisition's revision boundary.

## One writer at a time

The database remains `db/litintel.sqlite`; the actual writer lock is
`.litintel-writer.lock`. These compatibility names are intentional. Do not rename
them or introduce `.reprovane-writer.lock`. Historical references to
`.scholarflow-writer.lock` did not match the shipped implementation.

The operating system holds the lock while a writer runs. A remaining lock file
alone does not prove a writer is active; a crash releases the operating-system lock.
If a writer is reported active, find that program and allow a normal shutdown.
Do not delete the lock file or terminate unrelated processes to force access.
Direct Python API writers must use `reprovane.operations.lock.workspace_lock()`.
Do not run old and new writers concurrently. Network filesystems and writers that
ignore this lock are not certified safe.

## Daily backup

1. Stop every command, notebook or script writing the workspace; wait for completion.
2. Copy the **entire workspace** to a separate dated folder using your file manager
   or approved backup system. Keep database and all content-addressed storage (CAS)
   together. CAS is the evidence store addressed by content fingerprints.
3. Check the copy with full doctor and verify using its own quoted workspace address.
   Save results and keep the original. Do not experiment on your only backup.
4. Keep another protected copy on independent storage according to your team's policy.

Do not copy just an actively changing SQLite database. A full backup may contain
private metadata and full-text files, so restrict access and check redistribution rights.

## Before an upgrade and when restoring

Back up first. Install the new program in a separate environment, read the changelog
and check a workspace copy before adopting it. The name change from ScholarFlow 1
does not itself require a schema upgrade. Never promise automatic migration merely
because pip installation succeeded.

Where a real data upgrade is required, supported copy-on-write upgrade uses three
separate, non-overlapping source/target/backup directories. Terminal syntax:

```bash
reprovane workspace upgrade --workspace "current workspace" --target "upgraded workspace" --backup "verified DB backup"
reprovane verify --offline --workspace "upgraded workspace"
```

This leaves the source intact. The database backup uses SQLite's backup API and
validates hashes/history. The backup directory does **not** duplicate CAS; retain the
original CAS with it. The operational target receives verified CAS. Private config
and logs are not transferred. A target without a verified upgrade manifest is incomplete.
Replay reuses a target only if fingerprints and all checks match; changed inputs need
a new target. This is not a merge or generic folder-repair command.

The simplest restore is a verified full-workspace copy at a new location, with all
writers stopped. If an administrator restores a database-only backup, the corresponding
original CAS must also be restored at its expected paths. Check full doctor and verify
before writing. Do not edit migration history, rename the database or reseal old objects.

## Integrity failure and safe reporting

Stop writes, retain the original and all evidence, and ask the technical lead to
investigate a copy. Share version, operating system, sanitized command/error and exit
code. Never post keys, Authorization headers, cookies, private paths, real databases
or research PDFs. [Security](SECURITY.md) explains the reporting boundary.

Root command exit codes: 0 success; 2 configuration/validation/locking; 3 integrity/
history failure; 4 operational/transport failure for root scimago; 5 migration
execution failure. Routed command families retain their own safe error handling,
usually code 2. An exit code does not indicate scientific include/exclude status.

## Reproducibility limits

Production identifiers are random and enter manifests; separate runs are not promised
identical hashes. Fixed clocks/IDs belong to synthetic tests only. Checksums compare
content against a trusted value, not scientific validity, publisher identity or rights.
Exports reject symlinks and conflicting bytes; choose a new destination for different
content. Use UTF-8 for JSON, JSONL and Markdown. Do not change sealed bytes for display.
