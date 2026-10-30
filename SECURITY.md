# Security and privacy

## Protect credentials and review data

Install published Reprovane from PyPI or a separately reviewed candidate supplied
through the project's accepted process. Keep `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL`
in approved runtime/environment configuration. Never put actual values in source,
plans, workspaces, reports or shared URLs. Doctor reports presence, not key values.
An environment variable is configuration, not encrypted storage; restrict access
to the account running the program. See [Quick Start](QUICKSTART.md) and
[User Guide](USER_GUIDE.md) for safe examples using placeholders.

For a public issue, share version, operating system, sanitized command, exit code
and sanitized error. Do not upload passwords, API keys, cookies, Authorization
headers, browser profiles, private signed URLs, databases or restricted PDFs.
For a sensitive defect, contact the maintainer through an agreed private channel
before disclosing details. Do not assume a dedicated security mailbox exists.

Stop writers before backing up and retain the entire database and evidence store.
Control who can read workspace copies; they may contain private review data.
[Operations](OPERATIONS.md) describes backup/restore and writer locks. Do not manually
edit databases/manifests or remove lock files to get past a failed check.

## Full-text and legal boundary

Only eligible open-access files are acquired within the frozen policy. Do not
bypass paywalls, CAPTCHA or anti-bot checks, or automate institutional login,
single sign-on or cookies. Leave unresolved items in the manual queue. Manual
PDF ingest is not implemented; never force a rejected PDF into a sealed run.
Successful access is not redistribution permission; see [Third-party data](THIRD_PARTY_DATA.md).

URLs, metadata and files from providers are untrusted inputs. Full-text transport
pins resolved public IP addresses, validates Transport Layer Security (TLS) against
the original hostname, checks redirects and bounds requests/bytes/retries/redirects.
PDF files must pass structural checks. Submitted/unknown versions are rejected by
default; no landing-page scraping, artificial-intelligence or optical-character-
recognition fallback is used. Recognized secret-bearing responses and signed URLs
are refused. Heuristics cannot recognize every previously unknown secret.

## Filesystem and API boundary

Legacy migration accepts bounded ordinary directories, refuses symlinks and uses
copy-on-write. It does not extract archives or execute pickle/eval/payloads. Legacy
secrets/config/logs are inventoried by hash instead of being copied. Database values
use bound parameters; dynamic table/column names come from internal allowlists.
CLI writers use the operating-system lock; direct API writers must participate in
the same lock. Immutable exports refuse symlinks and conflicting bytes.

Adversarial concurrent filesystem mutation and PDF-parser CPU/memory isolation are
not certified. A database-only backup needs corresponding CAS evidence separately.
Old execution packages/CLI shims are not installed. Historical reference code exists
only in tests and is excluded from distributions. A checksum alone does not
authenticate a publisher; verify package provenance and trusted origin separately.
