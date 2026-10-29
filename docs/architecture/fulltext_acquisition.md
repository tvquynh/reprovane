# Stage11B full-text handoff and OA acquisition

Input is exclusively `CorpusReleaseStore.handoff()` from one verified SEALED Stage11 release. No screening, identity, discovery or frontier operation is executed. Full-text failures leave all corpus members intact. The old `scholarflow.fulltext` module and legacy CLI remain unchanged.

## Policy and operations

Use Python 3.10+ and `pip install -r requirements-fulltext.txt`. The explicit example policy is `tests/fixtures/fulltext_acquisition/policy.json`. Defaults accept publishedVersion and acceptedVersion, reject submittedVersion and UNKNOWN. Every operational bound, provider set, version/host rank, MIME rule, export mode and privacy mode is in canonical hashed policy. Change policy to obtain a separate run. Manual ingest is optional and is deliberately not implemented; enabling it fails validation. Manual queue files support external human acquisition without any membership change.

```powershell
python -m scholarflow.fulltext_acquisition create --workspace <workspace> --release-id <sealed-release-id> --policy <policy.json>
python -m scholarflow.fulltext_acquisition discover --workspace <workspace> --run-id <run-id>
python -m scholarflow.fulltext_acquisition acquire --workspace <workspace> --run-id <run-id> --expected-policy-sha256 <sha256>
python -m scholarflow.fulltext_acquisition finalize --workspace <workspace> --run-id <run-id>
python -m scholarflow.fulltext_acquisition verify --workspace <workspace> --run-id <run-id>
python -m scholarflow.fulltext_acquisition export --workspace <workspace> --run-id <run-id> --directory <metadata-dir>
python -m scholarflow.fulltext_acquisition verify-export --directory <metadata-dir> --expected-manifest-sha256 <sha256>
python -m scholarflow.fulltext_acquisition export-pdfs --workspace <workspace> --run-id <run-id> --directory <pdf-view-dir>
```

Only discover/acquire resolve runtime credentials. OPENALEX_API_KEY and UNPAYWALL_EMAIL use process environment, then the named Windows User environment values, reusing Stage07's OpenAlex registry helper. No config file/profile/session is enumerated. No key in a URL: OpenAlex uses Bearer headers on the original OpenAlex origin only. Unpaywall email is added only to the runtime DOI URL, never to stored request identities. Unpaywall enabled without runtime email fails before discovery. An offline frozen-only policy needs neither credential. `verify`, exports and inspection never resolve credentials or perform HTTP.

## Evidence, eligibility and plan freeze

Frozen OA locations, single-Work OpenAlex refresh (all locations; locations_count retained), and Unpaywall v2 DOI responses are separate evidence routes. Unique provider+exact handoff identifier is looked up once per run, serially. Exact raw response bytes use the accepted content-addressed store. Responses echoing known runtime secrets or containing sensitive signed query values fail closed and are not archived; the failure is recorded. This intentional raw-evidence privacy exception sacrifices unsafe lookup results instead of storing secret bytes. No retired Unpaywall search endpoint exists.

Candidates group only one item's normalized exact URL identity (path/query unchanged; scheme/host/default port normalized, fragment removed). Every provider/source-record route remains. Unknown and submitted versions cannot bypass policy via best_oa_location or cached content. Cached OpenAlex content always remains UNKNOWN in schema 1 because there is no deterministic cache-to-location version linkage. No version inference from URL/title/host. Multiple non-UNKNOWN version claims for the same URL fail closed. One route must itself support all OA/version/direct-file requirements; unrelated claims are never combined to fabricate eligibility.

Rank is version, host, direct evidence, OA evidence, provider name, stable candidate ID. Explicit version/host permutations are policy fields; provider is only the last semantic tie-break. Plans persist before any PDF request and cannot be changed. Landing pages are retained for humans, never scraped. A plan can contain all provider locations; only the first finite per_item_candidate_limit eligible candidates are attempted. Candidates beyond the limit remain evidence. Successful bytes from several URLs share one artifact; different hashes remain separate. A primary is selected only during materialization, as first successful candidate in frozen rank. There is no provisional primary to silently replace during acquisition retries. No metadata refresh or manual primary override after plan freeze; create a new policy/run context for new evidence. Verification of a subsequently altered frozen route/version fails closed.

## Transport and bounds

One Stage11B transport serves metadata and PDFs. It uses urllib3 from the existing requests dependency, reuses Stage07 runtime/private-value helpers, and has an independent network implementation because generic Stage07 JSON transport cannot pin DNS, stream PDFs or validate redirects. It does not modify Stage07. urllib3 connects to the validated public IP; HTTPS preserves original Host, SNI and certificate hostname verification. Every DNS answer and redirect target must be public; literal private/loopback/link-local/multicast/reserved targets, localhost, non-HTTP schemes, user-info URLs and nonstandard ports fail closed. No ambient proxies, netrc, cookies, browser state or login machinery is used. Only the original OpenAlex origin receives Bearer credentials; no forwarding to other hosts. HTTPS downgrade is forbidden by default.

Finite HTTP retries handle 429, 5xx and transport errors; other 4xx fail permanently. Retry-After/backoff are bounded. Redirect count is shared across retries for one fetch. Connection/read timeout plus an elapsed streaming deadline prevent unlimited waits; one blocking read can last up to the read timeout after the elapsed deadline. Bodies are streamed into a bounded buffer, not an unbounded whole-response request. Content-Encoding other than identity is refused to avoid decompression bombs. This conservative choice can reject otherwise usable providers. Concurrent downloads are not enabled.

Append-only request reservations commit before network I/O. Counts include metadata, redirects, failed requests and retries. A reservation charges max response bytes plus one 64-KiB oversize-detection chunk; completed requests charge actual bytes. Interrupted reservations retain the conservative full charge. Global caps cover items attempted, requests, bytes, distinct artifacts and unique provider lookups. Both HTTP retry and later candidate resume use retry_count, so the maximum is (retry_count+1)^2 HTTP attempts per candidate before redirects, further bounded by the global budget. Permanent failures and successes never redownload. Interrupted attempts consume an attempt ordinal and may resume within that bound. Exhausted budgets produce explicit manual statuses rather than shrinking corpus. Operators must also respect Unpaywall's published daily guidance across separate runs; there is no cross-workspace rate service.

## PDF artifacts and privacy

A success requires HTTP 200, byte bounds, a leading PDF header, trailing EOF, and strict pypdf structural opening with at least one page; encrypted PDFs fail closed. No text extraction, OCR, article metadata extraction or semantic analysis occurs. MIME is advisory: valid PDF bytes may override an incorrect MIME. HTML/challenge/login/JSON/error content cannot become an acquired artifact. Provider version is not independently inferred from PDF content; a structurally valid mismatched paper cannot be detected by this stage.

CAS reuses `raw/sha256/<prefix>/<hash>`. Artifact rows bind exact size, format, media type, structural validation and relative storage locator. SHA is authoritative; remote filenames are ignored. Optional convenience files are exactly corpus_item_id.pdf, with same-byte reuse and conflicting-byte refusal. The directory contains only actually acquired files, no placeholder PDFs.

URL persistence redacts credential/transient query values (including key, token, signature, sig, email and signing families). Retrieval retains exact query semantics in memory. Frozen signed candidates retain a digest of the original runtime URL and sanitized display URL. The programmatic acquire runtime_urls mapping can supply an exact matching URL; after restart without it the candidate fails closed with RUNTIME_URL_REQUIRED. Raw provider responses containing such query secrets are conservatively refused, so this stage does not promise automatic acquisition of every signed-link provider. No raw secrets are stored to make resume work. No legal redistribution claim is made by successful acquisition.

## Persistence, lifecycle and verification

Migration 0012 is Stage11B acquisition persistence, not Stage12 hardening. Historical migrations 0001-0011 are unchanged. Normalized run/input/evidence/plan/candidate/request/request-result/attempt/attempt-result/artifact/status tables use foreign keys, unique identities and immutable guards. OPEN -> ACQUIRING -> MATERIALIZED -> SEALED. Plans, results and statuses are append-only. Run updates permit only these lifecycle transitions. Only SEALED exports are authoritative; all members must have a final operational status, including unresolved manual statuses.

DB verification re-verifies Stage11 and exact handoff, policy, raw evidence hashes/projections, plans/candidate table, attempts/HTTP/artifact linkage, budgets, CAS bytes/PDF structure, per-item status hashes, manual queue and manifest. It works offline. The semantic manifest excludes timestamps but includes stable persisted identities, evidence inventories, all ordered item statuses, selected candidate/artifact, budget and manual queue. For an unchanged run its hash is reproducible; different run IDs/time-specific evidence contexts are intentionally different identities.

Portable metadata export contains exactly fulltext_manifest.json, fulltext_items.jsonl, manual_queue.csv, manual_queue.jsonl and SHA256SUMS.txt. Checksums cover the other four files and exclude the checksum file itself. Portable verification rebuilds exact bytes and checks policy/item/status/queue consistency, optionally against a trusted expected semantic manifest hash. It does not authenticate DB provenance, inspect artifact bytes, certify legal OA or grant copyright. PDF verification requires the DB plus CAS. No real PDF belongs in an implementation delivery ZIP.

Export writes are not a multi-file filesystem transaction: matching partial exports can resume; conflicting/extra/symlink entries fail before overwrite. Integrity must pass before reporting export success. CAS placement precedes database registration, so an interruption can leave an unreferenced object; it never becomes an acquired item. A single writer is recommended; distributed/multi-process racing acquisition and untrusted shared filesystem hardening are not certified. Current buffers/parser are bounded by byte policy, but pypdf has no separate CPU sandbox; use trusted operation limits and a separate process for hostile-file isolation in deployment. No Stage12 work is included.

## Official documentation checked 2026-10-29

- https://help.openalex.org/data/locations/
- https://help.openalex.org/data/works/open-access/
- https://help.openalex.org/access/fulltext/
- https://help.openalex.org/api/authentication/
- https://unpaywall.org/api
- https://unpaywall.org/data-format

The Unpaywall SPA initially exposed only a JavaScript shell; indexed official API/data-format text was checked as well. v2 DOI lookup remains supported, search retired 2026-09-18, daily guidance 100,000 calls. OpenAlex location lists may truncate at 100; singleton returns all. Cached PDFs retain original copyright. OpenAlex accepts Bearer authentication; API content downloads are metered. Schema 1 does not download XML or perform full-text analysis.
