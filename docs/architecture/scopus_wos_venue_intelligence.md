# Stage06: Scopus and Web of Science venue intelligence

SCImago ranking/presence, Scopus membership/status and WoS Core Collection
membership remain independent facts. There is no universal authoritative indexed
boolean. No membership is inferred from title matching, other providers, coverage
recency or discovery. Identity only connects already observed claims.

## Actual source schemas and provenance

Before implementation, SCOPUS/README.md, WOS/README.md and all five CSVs were read
at exact Git commit bab3b51f002e1bc8859ba207b788f02c83a18129. Scopus contains
48,888 data rows and52 columns. The core structural columns are Sourcerecord ID,
Source Title, ISSN, EISSN, Active or Inactive, Coverage, Source Type, Publisher,
All Science Journal Classification Codes (ASJC). Optional fields include
discontinuation, three-letter languages, title history/related titles, broad ASJC
column labels/top-level flags and other metadata. Unknown optional columns stay
in exact raw artifacts, are logged and do not fail the profile. Mandatory missing
or duplicate normalized headers, row-width errors and missing title/Scopus native
ID fail the entire import. No malformed structural row is silently discarded.
Empty physical CSV records are benign formatting and ignored.

All four WoS files have Journal title, ISSN, eISSN, Publisher name, Publisher
address, Languages, Web of Science Categories. Their collection codes derive only
from the corresponding SCIE/SSCI/AHCI/ESCI file. WoS provider snapshot date is not
stated, so none is invented. Scopus release label comes from the selected filename;
the default Jul_2026 reflects that export version, not a universal validity date.

Scopus status is explicit Active→ACTIVE, Inactive→INACTIVE, case/whitespace
normalized. Blank or unrecognized token→UNKNOWN with original RAW_STATUS claim.
Discontinuation and coverage are retained separately, never used to infer status.
Scopus membership is a positive source-list claim with provider-specific status.
WoS uses positive wos/PRESENT_IN_SNAPSHOT with collection_code. Missing collection
rows do not generate negative membership facts.

ASJC preserves the original string, all semicolon-separated codes, broad-category
column codes/literal values and top-level values. No codebook label is guessed and
no primary category chosen. WoS categories split only on the provider's pipe
delimiter; commas and ampersands inside names stay intact. They never become ASJC.
Languages preserve provider literals and delimiters, with no invented ISO mapping.
Coverage keeps the exact trimmed text, individually parseable year segments and
unparsed portions, preserving gaps rather than a misleading min/max envelope.
Publisher address is a literal provider attribute. Remaining provider columns are
reconstructible from the retained exact CSV/header and logical row ordinal.

## Minimal persistence extension

Existing venue descriptions, identifiers and membership claims use Stage02 tables.
Classifications, languages and discontinuous coverage cannot safely be represented
as metrics or existing scalar columns. Migration0006 adds one generic immutable
venue_provider_attribute_observation table: namespace, code, structured JSON,
field path, exact normalizer profile and ProviderRecord anchor. It has sealed-row
requirements, foreign key, canonical API payloads and UPDATE/DELETE/REPLACE guards,
including external recursive_triggers OFF. Provider API restricts code/taxonomy
combinations. No historical migration changes.

A partial unique expression index on Stage06 ProviderSnapshot import keys prevents
concurrent duplicate semantic snapshots. The key binds provider, repository,
full commit, exact filenames/SHA-256 and parser version. Same commit import reuses
the sealed snapshot and appends no observations/audit duplication. New commit
creates a new snapshot, even if file bytes match. Snapshot verification is repeated
on reuse. Database default1, ObservationStore3, VenueResolutionStore5 remain their
accepted feature targets; VenueIntelligenceStore explicitly upgrades to6.

All requested providers parse before a single semantic import transaction. CSVs
are first retained/hash-verified in Foundation CAS and parsed from those exact
bytes. Structural errors fail before snapshots. Semantic failure rolls back every
requested provider, including when caller catches an exception inside an outer
transaction (savepoint). Retry is safe; unreferenced verified artifacts may remain.
Snapshots seal before typed observations, as in accepted Stage04. Attribute writes
may append later evidence, but already frozen runs never silently incorporate it.

Duplicate Scopus rows consolidate within one native-ID logical record; different
claims for the same native ID remain separate selected claims. WoS has no native
ID, and none is fabricated. Its four collection tables undergo a deterministic
within-provider record union only for identical exact title keys and identical
ISSN/eISSN claims. Without any checksum-valid ISSN, complete raw rows must agree.
Different categories/languages remain distinct claims with all raw file/ordinal
anchors in ProviderRecord metadata. One logical record can thus carry SCIE+SSCI
or all four positive memberships, without artificial duplicate identity nodes.
This source-table union does not merge cross-provider entities or invoke fuzzy
matching. Strong conflicts/title variations still go through Stage04 resolution.

## Stage05 acquisition reuse

mirror/pinned_files.py is a finite-file plan adapter over the accepted GitHubClient
and MirrorCache, not another transport/cache implementation. It reuses ref
resolution, bounded HTTPS fetch, CAS, verified copied materialization, immutable
snapshot promotion, exclusive local lock and atomic JSON/state helpers. All files,
including both README documents, come from one resolved SHA. Default files are
the selected Scopus CSV, four WoS CSVs and two README files. No Elsevier/Clarivate
runtime acquisition occurs. Their official README links are documentation only.

Unlike SCImago, these provider directories publish no checksum manifest in the
inspected documentation. SHA-256 is therefore computed from exact-commit HTTPS
bytes and recorded in a generated outside-data sidecar, not falsely presented as
an upstream-published checksum. Same-commit/offline reuse rehashes package and CAS.
New commits fetch this finite plan again because there is no upstream filename→hash
plan for selective reuse before download. Stage05 SCImago selective behavior is
unchanged. Failures never advance state; old completed directories are untouched.
State is convenience metadata. The full SHA and retained bytes/hash are provenance.

## Resolution and frozen query

The existing Stage04 node/candidate/decision/entity model and conservative policy
remain authoritative. Provider-native grouping is provider-scoped; valid ISSN/
eISSN evidence outranks titles, disjoint valid identifiers block title-primary
merge, primary blocks above two nodes remain ambiguous. Stage06 does not reorder
the accepted ISSN/ISSNL method precedence or add aliases/fuzzy/AI rules.

Full-data validation exposed repeated full node/member scans in accepted Stage04
verification and entity record counting. Stage06 replaces those scans with one
membership lookup and one node-count lookup per operation, and uses an explicit
composite-key member query. Ordering, evidence, hashes, policy and outputs remain
identical; all accepted tests verify compatibility. No migration/history rewrite.

Venue evidence extraction conditionally includes selected provider attributes and
snapshot/file/row provenance when schema6 exists, binding them into existing run
hashes and frozen evidence. Import-wide statistics stay in the immutable snapshot
and are not duplicated into every individual input. Schema5 inputs keep their accepted representation.
VenueIntelligence.query reads only that frozen materialized run evidence, returning
separate Scopus status/ID/type/coverage/ASJC/provenance records and WoS collection/
category/provenance records. Multiple historical claims remain visible, with no
undocumented latest-source preference. No evidence means NO_SELECTED_EVIDENCE or
empty positive collection list, not a negative provider-membership assertion.

Diagnostic statistics count selected ProviderRecords: matched means their node is
in a materialized component of more than one node; otherwise active AMBIGUOUS
candidate means ambiguous; remaining singleton means unresolved. A valid singleton
is not a rejected import. Stored import statistics mark resolution NOT_RUN until
an explicit run is requested; immutable import metadata is never rewritten with
later resolution output. A run's matched/ambiguous/unresolved statistics are
separate derived reports. Live diagnostics use explicit block limit500; uniqueness
rules still require exactly two nodes, so larger blocks remain ambiguous.

## Explicit CLI and annual replacement

Existing literature_pipeline.py commands/help/config loading remain unchanged.
The separate feature entry point follows argparse/subcommand conventions and
never loads private v4 config or OpenAlex credentials:

```powershell
python -m reprovane.venue_intelligence sync --workspace C:/research/venues
python -m reprovane.venue_intelligence sync --workspace C:/research/venues --offline --ref <full-sha>
python -m reprovane.venue_intelligence import --workspace C:/research/venues --snapshot-directory <sync-result-directory>
python -m reprovane.venue_intelligence stats --workspace C:/research/venues --snapshot-id <snapshot>
python -m reprovane.venue_intelligence resolve --workspace C:/research/venues --snapshot-id <scopus-snapshot> --snapshot-id <wos-snapshot>
python -m reprovane.venue_intelligence query --workspace C:/research/venues --run-id <run> --entity-id <venue>
```

For replacement source lists: update the mirror data/README and commit GitHub,
then explicitly sync. If Scopus filename changes, pass --scopus-path consistently
to sync/import; no code change is required for a supported structural schema.
Import explicitly, choose snapshot IDs and create a new diagnostic/research run.
Old snapshots/runs stay readable and unchanged. Research execution never silently
refreshes the mirror. Stage07/OpenAlex acquisition, screening and release work are
not implemented.
