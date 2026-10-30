# Reprovane User Guide

Start with the [Beginner Quick Start](QUICKSTART.md). This guide is for researchers
who can copy terminal commands but are not programmers. Reprovane has a command-line
interface (CLI), not a graphical review application. Some advanced tasks need a
technical lead to prepare plans or call the supported application programming
interface (API). A help command explains syntax; it does not run an acquisition.

**Humans decide screening and inclusion.** Venue rankings and citation counts do
not automatically include a study. Missing full text does not remove a study from
a frozen corpus. Reprovane stores evidence and decisions; it does not certify
scientific validity or complete search coverage.

## Reading these examples

Executable examples below use **Windows PowerShell** with `$rp`, `$py`, `$ws` and
the software folder set as in Quick Start. Reopen that setup in each new terminal.
If your launcher is blocked, replace `& $rp` with `& $py -m reprovane`; keep security
policy unchanged. On macOS/Linux, use the direct `"$RP"` launcher and `"$WS"` workspace
variable from Quick Start with the same CLI options; do not paste PowerShell syntax.

JSON means JavaScript Object Notation, named fields stored as text. JSONL means
one JSON object per line. Do not rename a spreadsheet to `.jsonl`. A successful
command has exit code zero; in PowerShell inspect `$LASTEXITCODE` immediately after
the command. If nonzero, stop dependent steps and inspect existing state before
repeating a create operation. IDs are saved identifiers; placeholders below must
be replaced with IDs from the matching successful run, not guessed.

## 1. Workspace and project

**Goal:** keep a review topic and its evidence in a known location.
**Before you start:** complete Quick Start using practice data, then choose a separate
workspace for a real review. Agree the question, date range, languages, study types,
reviewers, criteria and search limits as a team.

**Command — PowerShell:**

```powershell
& $rp workspace init --workspace "$ws"
& $rp project create --workspace "$ws" --key my-review --display-name "My Review"
```

**What it does:** initializes storage and creates a named project. **What you should
see:** JSON containing a `project_id`. **Record/save:** workspace path, project ID
and approved protocol. **Common mistakes:** repeating create with the same key,
using the practice workspace for real data, or putting data inside the software
environment. **Next step:** save a revision.

## 2. Revisions and your review notebook

**Goal:** preserve each version of the question. **Before you start:** set `$projectId`
to the returned project ID and prepare `review-project.json` like the Quick Start
manifest, using the team's actual description.

**Command — PowerShell:**

```powershell
& $rp project revision --workspace "$ws" --project-id $projectId --number 1 --manifest review-project.json
& $rp project inspect --workspace "$ws" --project-id $projectId
```

**What it does:** stores a revision without overwriting previous descriptions.
**What you should see:** `revision_id` and the project's revision list. **Record/save:**
project and revision IDs, protocol, query text and reasons for limits. Use a new
revision number when the description changes. **Common mistakes:** confusing a title
with an ID or assuming all CLI families share the same revision automatically.
**Next step:** configure online access and understand acquisition's revision boundary.

Keep a notebook with workspace path, software version, project/revision IDs,
acquisition run and snapshot IDs, resolution/discovery run IDs, screening plan and
decision IDs, corpus release ID, full-text run ID, and important manifests. A
manifest lists saved objects and their hashes. SHA256 (Secure Hash Algorithm, 256-bit)
is a content fingerprint: compare it with a trusted saved value to detect changed
bytes. An ID and a fingerprint serve different purposes; retain both.

## 3. Online credentials and bounded OpenAlex acquisition

**Goal:** collect a finite set of metadata records. **Before you start:** obtain your
own OpenAlex API key through its official service, agree a query/spec and limit
costs and results. `OPENALEX_API_KEY` identifies requests; keep its value private.
`UNPAYWALL_EMAIL` is a contact address used only when Unpaywall is enabled for full
text. It is personal data, even though it is not a password.

To avoid printing or saving a key in PowerShell history, enter it at a masked prompt.
The example placeholder describes what to enter; do not use it as a real credential.

**Command — PowerShell:**

```powershell
$secret = Read-Host "OpenAlex key (YOUR_KEY_HERE means your own private key)" -AsSecureString
$env:OPENALEX_API_KEY = [System.Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret
& $rp acquisition acquire --help
```

The variable applies to this process and its children. Closing the terminal removes
this process copy. For persistent configuration use your organization's approved
environment-variable method; do not put values in source, plans, URLs or reports.
On macOS/Linux, a team administrator can help set the same environment variable
without displaying it. Doctor reports presence only, not the stored key.

Save a small acquisition spec using PowerShell:

```powershell
@'
{
  "search": "reproducible literature review",
  "filters": {"from_publication_date": "2020-01-01"},
  "max_records": 20,
  "page_size": 20,
  "max_pages": 1
}
'@ | Set-Content -Encoding utf8 acquisition-spec.json
```

This is an illustration, not a complete review strategy. After the team approves
the spec, the next PowerShell block **uses the network**:

```powershell
$acquisition = (& $rp acquisition acquire --workspace "$ws" --spec acquisition-spec.json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw "Acquisition incomplete. Inspect saved state before continuing." }
$acquisitionId = $acquisition.acquisition_id
$revisionId = $acquisition.project_revision_id
$snapshotId = $acquisition.snapshot_id
$acquisition | Select-Object acquisition_id, project_revision_id, snapshot_id, state
```

**What it does:** stores provider evidence and a bounded acquisition. **What you
should see:** IDs and `state: COMPLETE` before using it downstream. **Record/save:**
exact spec, limits, IDs and state. **Common mistakes:** the acquisition CLI currently
creates/reuses its own spec-based revision in the internal `openalex-acquisition`
project; it has no flag selecting your manually created review revision. Use its
returned `project_revision_id` for this CLI demonstration. Do not substitute the
practice revision. A real multi-search review needing one agreed revision requires
a technical lead to use `OpenAlexAcquisitionStore.acquire(..., project_revision_id=...)`
under the workspace lock. Do not modify the database or invent a CLI flag.
**Next step:** inspect the snapshot and resolve publication identity.

## 4. Snapshots and provider records

**Goal:** distinguish original evidence from interpreted results. **Before you start:**
have a successful acquisition ID from this workspace.

**Command — PowerShell:**

```powershell
& $rp acquisition inspect --workspace "$ws" --acquisition-id $acquisitionId
```

**What it does:** reads acquisition state and references. **What you should see:**
the same acquisition/revision/snapshot references. **Record/save:** snapshot ID and
provider record IDs supplied by the supported store API. **Common mistakes:** an
OpenAlex Work ID is not a local `provider_record_id`. There is no general one-click
CLI for browsing every raw provider record; a technical lead can use the store API
and [acquisition contract](docs/architecture/openalex_acquisition_migration.md).
**Next step:** choose typed-observation profiles and conservative identity resolution.

## 5. Publication identity resolution

**Goal:** decide conservatively which records describe the same publication.
**Before you start:** a technical lead prepares `selector.json`, valid provider
record IDs and the correct revision. A selector for standard OpenAlex work
observations contains `openalex-work`, normalizer version `1` and observation
schema version `1`; use the exact structure in the
[identity contract](docs/architecture/publication_identity_resolution.md).

**Command — PowerShell (syntax reference):**

```powershell
& $rp resolution create --help
& $rp resolution add --help
& $rp resolution verify --help
```

**What it does:** shows required inputs before creating a resolution run. The
supported sequence is create, add, generate, evaluate, materialize, seal, verify.
**What you should see:** help listing revision, selector, run and record options.
**Record/save:** selector bytes/profile versions, input record IDs, run ID and
verification result. **Common mistakes:** fuzzy titles cannot safely merge an
ambiguous publication; conflicts require expert review through supported APIs.
**Next step:** assess venue evidence separately. This guide does not invent a
single command to resolve every conflict.

## 6. Venue evidence and venue resolution

**Goal:** retain distinct venue evidence. **Before you start:** select approved,
pinned provider data and its provenance, not an arbitrary mirror.

**Command — PowerShell (help only):**

```powershell
& $rp venue-data --help
& $rp scimago --help
& $rp venue-resolution create --help
```

**What it does:** displays provider import/query/sync and venue resolution options.
Sync can use the network; help does not. **What you should see:** the supported
parameters. **Record/save:** provider, snapshot, reference/commit, checksum, time and
resolution context. **Common mistakes:** SCImago ranking/presence, Scopus membership/
status and Web of Science collection evidence are different. Do not turn a venue
title into a claim about one paper's quartile or all collections. **Next step:**
create discovery provenance using matching acquisition/revision evidence. See
[venue resolution](docs/architecture/venue_resolution_scimago.md) and
[provider intelligence](docs/architecture/scopus_wos_venue_intelligence.md).

## 7. Citation discovery

**Goal:** record how publications were found. **Before you start:** use a COMPLETE
acquisition and its returned revision ID, or a lead-managed common revision.

**Command — PowerShell:**

```powershell
& $rp discovery create --workspace "$ws" --project-revision $revisionId
```

Save the returned `discovery_run_id` as `$discoveryId` before continuing in PowerShell:

```powershell
& $rp discovery register-primary --workspace "$ws" --run-id $discoveryId --acquisition-id $acquisitionId
& $rp discovery resolve-seeds --workspace "$ws" --run-id $discoveryId
& $rp discovery expand-backward --help
& $rp discovery expand-forward --help
```

**What it does:** binds the primary acquisition and resolves seeds. Expansion is a
separate network action needing a valid seed file and finite limits. Backward
citations are references cited by the seed; forward citations are works citing the
seed. **What you should see:** a run ID and step state, not an inclusion decision.
**Record/save:** route/seed/acquisition IDs and expansion limits. **Common mistakes:**
mixing directions or revision contexts. **Next step:** materialize and seal the
discovery after approved expansion; review the
[discovery contract](docs/architecture/discovery_provenance_citation_expansion.md).

## 8. Frontier and search validation

**Goal:** bound repeated expansion and compare strategies. **Before you start:** a
lead prepares a finite policy for rounds, depth, seeds and budgets, and sentinel
publications that the review expects a strategy to find.

**Command — PowerShell (help only):**

```powershell
& $rp frontier --help
& $rp search-validation --help
```

**What it does:** lists policy-driven execution and strategy comparison operations.
**What you should see:** create/execute or planning/finalization options as appropriate.
**Record/save:** policy, plan/run IDs, stop reason and comparisons. **Common mistakes:**
an unbounded loop is not a search strategy; a sentinel match is not study inclusion.
**Next step:** freeze the discovery/frontier inputs for screening. Plan schemas and
state requirements are in the [frontier contract](docs/architecture/frontier_policy_search_validation.md).

## 9. Human title/abstract screening

**Goal:** collect review decisions. **Before you start:** a lead creates a run from
sealed discovery and an approved plan with HUMAN reviewers, assigned labels,
criteria, exclusion reasons and required counts. Store the run ID as `$screeningId`.
Example labels below must actually exist in that plan.

**Command — PowerShell:**

```powershell
& $rp screening export-review-packet --workspace "$ws" --run-id $screeningId --reviewer reviewer_1 --directory "review packet 1"
& $rp screening import-decisions --workspace "$ws" --run-id $screeningId --reviewer reviewer_1 --input decisions.jsonl
& $rp screening resolve --workspace "$ws" --run-id $screeningId
```

**What it does:** exports a packet, then imports decisions you prepared after
reviewing it; do not run the import until `decisions.jsonl` is valid and complete.
`SCREEN_INCLUDE` passes this stage; `SCREEN_EXCLUDE` needs a plan reason;
`SCREEN_UNSURE` records uncertainty. **What you should see:** recorded decisions
and unresolved conflicts if any. **Record/save:** reviewer assignment, plan,
decision IDs and packet context hashes. **Common mistakes:** editing evidence/IDs,
pretending an automated agent is a human, or using test criteria for real research.
**Next step:** human adjudication. See [screening contract](docs/architecture/screening_separation.md).

## 10. Adjudication

**Goal:** resolve disagreements explicitly. **Before you start:** the adjudicator
must be named in the plan and review the exact conflicting input decision IDs.

**Command — PowerShell:**

```powershell
& $rp screening export-conflicts --workspace "$ws" --run-id $screeningId --adjudicator adjudicator_1
& $rp screening submit-adjudication --help
& $rp screening finalize --help
```

**What it does:** exports conflicts and documents how to submit a resolution and
finalize. **What you should see:** conflict references; finalization refuses missing
decisions or unresolved disagreements. **Record/save:** adjudication IDs, reasoning
and supersession references. **Common mistakes:** deleting old decisions rather
than superseding them or forcing finalization. **Next step:** verify the sealed
screening and prepare a corpus release through its handoff.

## 11. Immutable corpus release

**Goal:** freeze the selected studies and their metadata. **Before you start:** a
lead supplies a release plan and matching revision/sealed screening. Use its create,
materialize and finalize operations; save the returned release ID as `$releaseId`.

**Command — PowerShell:**

```powershell
& $rp corpus create --help
& $rp corpus inspect --workspace "$ws" --release-id $releaseId
& $rp corpus verify --workspace "$ws" --release-id $releaseId
& $rp corpus export --workspace "$ws" --release-id $releaseId --directory "corpus export 1"
& $rp corpus verify-export --directory "corpus export 1"
```

**What it does:** verifies and exports an already prepared release. **What you
should see:** sealed membership and a verified manifest. **Record/save:** release
ID, plan and trusted manifest fingerprint. **Common mistakes:** trying to edit a
sealed corpus or include excluded studies through export. A changed review needs
a new context/run/release. **Next step:** full-text handoff. Read the
[corpus contract](docs/architecture/immutable_corpus_release.md).

## 12. Open-access full-text acquisition

**Goal:** obtain eligible full text for a sealed corpus. **Before you start:** a lead
prepares `fulltext-policy.json`, including approved network bounds and versions.
For Unpaywall, set an approved contact email in PowerShell using this placeholder:

```powershell
$env:UNPAYWALL_EMAIL = "YOUR_CONTACT_EMAIL_HERE"
```

Replace the placeholder locally before enabling that provider; do not publish your
personal value. The following PowerShell operations can use the network:

```powershell
& $rp fulltext create --workspace "$ws" --release-id $releaseId --policy fulltext-policy.json
```

Save returned `run_id` as `$fulltextId`, then run in PowerShell:

```powershell
& $rp fulltext discover --workspace "$ws" --run-id $fulltextId
& $rp fulltext inspect --workspace "$ws" --run-id $fulltextId
```

Inspect the saved policy, ranking and actual policy hash; set that hash as `$policySha`
only after review. Continue in PowerShell:

```powershell
& $rp fulltext acquire --workspace "$ws" --run-id $fulltextId --expected-policy-sha256 $policySha
& $rp fulltext finalize --workspace "$ws" --run-id $fulltextId
& $rp fulltext verify --workspace "$ws" --run-id $fulltextId
```

**What it does:** discovers candidates, acquires within policy, freezes and verifies.
**What you should see:** verified artifacts and unresolved entries where appropriate.
**Record/save:** full-text run ID, policy hash, manifest and outcomes. **Common mistakes:**
supplying a guessed hash, assuming every version is eligible, or using institutional
login/cookies. Default policy rejects submitted/unknown versions; PDF (Portable
Document Format) files must pass structural checks. **Next step:** review the manual
queue. See the [full-text contract](docs/architecture/fulltext_acquisition.md).

## 13. Manual queue

**Goal:** identify unresolved full text without changing inclusion. **Before you
start:** have a finalized/inspected full-text run.

**Command — PowerShell:**

```powershell
& $rp fulltext inspect --workspace "$ws" --run-id $fulltextId
```

**What it does:** reports run state; review the exported manifest's `manual_queue`.
**What you should see:** unresolved entries for missing links, ineligible versions,
rejected files or failed requests. **Record/save:** outcome and lawful follow-up.
**Common mistakes:** forcing a rejected PDF, bypassing a paywall, CAPTCHA or anti-bot
challenge, or treating a missing PDF as exclusion. Manual PDF ingest is not implemented;
`export-pdfs` copies already validated artifacts, not newly supplied PDFs.
**Next step:** verify and export permitted results.

## 14. Verification and export

**Goal:** check consistency before sharing. **Before you start:** confirm data rights
and use a new export directory if content differs.

**Command — PowerShell:**

```powershell
& $rp fulltext export --workspace "$ws" --run-id $fulltextId --directory "fulltext export 1"
& $rp fulltext verify-export --directory "fulltext export 1"
& $rp doctor --offline --workspace "$ws" --full
& $rp verify --offline --workspace "$ws"
```

**What it does:** verifies stored objects and export consistency. **What you should
see:** successful verification, or a safe integrity error requiring investigation.
**Record/save:** plans, IDs, manifests and trusted hashes independently of exports.
**Common mistakes:** assuming a self-consistent manifest authenticates its origin
or grants redistribution rights. **Next step:** back up and review sharing permissions
in [Third-party data](THIRD_PARTY_DATA.md).

## 15. Resume interrupted work

**Goal:** continue a saved operation safely. **Before you start:** retain the exact
spec and acquisition ID; do not reuse an old predecessor cursor.

**Command — PowerShell:**

```powershell
& $rp acquisition inspect --workspace "$ws" --acquisition-id $acquisitionId
& $rp acquisition resume --workspace "$ws" --acquisition-id $acquisitionId --spec acquisition-spec.json
```

**What it does:** resumes the matching bounded acquisition, potentially using the
network. **What you should see:** updated state; inspect again and use saved IDs
only after COMPLETE. **Record/save:** stop reason and completed state. **Common
mistakes:** recreating a successful project/run or guessing an ID after an error.
Other workflows have distinct state rules; inspect first and ask the lead before
repeating writes. **Next step:** verify and back up.

## 16. Backups and recovery

**Goal:** preserve a recoverable review. **Before you start:** stop all writers and
copy the entire workspace, including database and content-addressed storage (CAS),
the folders holding evidence under its content fingerprints.

**Command — PowerShell:**

```powershell
& $rp doctor --offline --workspace "$ws" --full
& $rp verify --offline --workspace "$ws"
```

**What it does:** checks the selected workspace; it does not make a backup for you.
**What you should see:** successful integrity checks before and after a stopped-writer
copy. **Record/save:** backup location, software version, IDs and check results.
**Common mistakes:** copying only the database, deleting lock files or attempting
manual database repair. **Next step:** follow [Operations](OPERATIONS.md) for copying,
restoring and integrity failures, and [Security](SECURITY.md) before reporting issues.
