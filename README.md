# ScholarFlow

ScholarFlow — Reproducible literature discovery, screening, and evidence management.

ScholarFlow is a reproducible literature discovery, screening, evidence-management, corpus-release, and full-text acquisition platform. It is a successor product with its own version line: **1.0.0-rc1** (Python version **1.0.0rc1**), with stable **1.0.0** reserved for final acceptance.

Intended project repository: https://github.com/tvquynh/scholarflow. This bootstrap prepares that tree; it does not create or publish a repository or release.

## Capabilities

- Bounded reproducible OpenAlex acquisition, immutable provider snapshots, raw evidence and typed observations.
- Conservative publication identity resolution and venue intelligence, with separate SCImago ranking/presence, Scopus status/membership and Web of Science collection semantics.
- Citation discovery, backward/forward snowballing, finite frontier policy and search-validation audits.
- Human screening, reviewer conflict adjudication and exact evidence provenance.
- Immutable corpus releases and hash-based portable/DB verification.
- OA full-text acquisition with published/accepted version policy, structural PDF checks, content-addressed artifacts and a deterministic manual queue.
- Copy-on-write legacy migration, offline diagnostics, OS writer locks and backup-first workspace upgrades.

OpenAlex, SCImago, Scopus, Web of Science and Unpaywall are providers, not the platform's identity. ScholarFlow is not an AI paper writer. AI does not decide publication identity, screening inclusion or corpus membership. It does not promise guaranteed recall or automatic scientific correctness.

## Install and begin

Python 3.11+; runtime dependencies are requests>=2.31,<3 and pypdf>=6,<7. Install the local candidate wheel with its declared dependencies; for offline installation prepare dependency wheels first.

```powershell
python -m pip install scholarflow-1.0.0rc1-py3-none-any.whl
scholarflow --version
scholarflow --help
scholarflow doctor --offline
scholarflow workspace init --workspace "research workspace"
scholarflow doctor --offline --workspace "research workspace" --full
```

Use runtime OPENALEX_API_KEY and UNPAYWALL_EMAIL only. Help, version, offline doctor, inspect and verify need no network or credentials. Fulltext uses declared OA direct links and a frozen version policy: submittedVersion and UNKNOWN are rejected automatically by default. Full-text failure never changes corpus membership. Manual full-text ingest is not implemented; unresolved items remain in a human queue.

ScholarFlow 1.0.0 uses **database schema 13**. Product version and schema version are separate. Historical migration SQL 0001–0013 and existing workspace locators are preserved; an existing schema13 workspace needs no semantic migration merely because the Python namespace changed.

Read [Operations](OPERATIONS.md), [Migration to ScholarFlow](MIGRATION_TO_SCHOLARFLOW.md), [Architecture](ARCHITECTURE.md), [Security](SECURITY.md) and [Provenance](PROVENANCE.md). Historical predecessor material is isolated under docs/legacy/; it is not the current product manual.

## Development

```powershell
python -m pip install -e .
python -m unittest discover -s tests -q
python -m pip install build
python -m build
```

Release validation uses clean noneditable wheel and sdist installations. The bootstrap contains no Git metadata; initialize/import it only after review. No remote action, tag or publication is part of bootstrap preparation.
