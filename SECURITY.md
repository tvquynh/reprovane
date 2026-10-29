# ScholarFlow security boundaries

Use only runtime OPENALEX_API_KEY and UNPAYWALL_EMAIL (Windows User environment or session environment elsewhere). Never put values in source, plans, workspaces, reports or URLs you share. Doctor reports configuration presence by name only. No browser profile, cookie, SSO or institutional-login automation is part of the modern acquisition workflow.

Stage11B pins resolved public IPs with original-host TLS verification, checks redirect targets, bounds requests/bytes/retries/redirects and structurally validates PDF bytes. No unknown/submitted version bypass, landing-page scraping, anti-bot/paywall bypass, AI or OCR fallback. Candidate URLs and metadata are untrusted. Raw responses containing known secrets or signed URL secrets fail closed. Privacy heuristics are conservative and cannot detect every unknown secret; known runtime values are also checked exactly.

Legacy migration is directory-only, bounded, copy-on-write and symlink-refusing. No archive extraction, pickle/eval/exec payload or shell interpolation is used for migration. Legacy secrets/config/logs remain hash-inventoried, not copied. All SQL values use bound parameters; dynamic table/column names come only from internal allowlists. OS locks serialize modern CLI writers; direct API users must participate. Immutable export writers refuse symlinks/conflicting bytes. File systems controlled by an adversarial concurrent process and PDF parser CPU/memory isolation are not certified. Backups intentionally exclude private config and require separate immutable CAS retention.

No historical product CLI is installed by ScholarFlow. Legacy execution reference code exists only for regression coverage; the predecessor remains responsible for historical execution. Modern acquisition keeps its accepted pinned-DNS/version/PDF safeguards.

Candidate security audit and exact known-secret scans are in the Stage12 delivery. Checksum validation proves integrity relative to trusted hashes, not publisher authenticity, copyright permission or scientific correctness. No package publication/tag/push is performed by this milestone.
