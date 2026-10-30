# Historical predecessor overview (v4.1.0)

This is an English historical reference, not current Reprovane installation guidance.
The original language-suffixed path is retained for old references. Read the
[archived comprehensive English manual](USER_GUIDE.md) for detailed predecessor
contracts, or the current [Reprovane User Guide](../../../../USER_GUIDE.md).

OpenAlex Literature Intelligence 4.1.0 was released on 28 October 2026. It collected
OpenAlex records with cursor paging, bounded queries, resume and DOI/Work-ID
deduplication. It enriched venues from SCImago snapshots for 2010–2025, matching
ISSN/ISSN-L before titles; conflicting ISSNs prevented title-only acceptance.

Historical rankings used the publication year without interpolation. Latest source
status was ACTIVE_LATEST, HISTORICAL_ONLY, UNMATCHED or AMBIGUOUS. The default main
output used journals active in the latest snapshot, with optional quartile/category,
estimated article-processing-charge, DOAJ and CWTS filters. These were machine
filters, not modern human screening decisions.

Historical options included --search-field title_and_abstract, --exclude-retracted,
--venue-tiers and --admit-tier. OpenAlex citation metrics were retrieval-time values;
source tiers reflected user-provided lists. Preprints were written separately for
manual assessment. fetch-oa attempted eligible listed open-access locations, stopped
at anti-bot challenges and wrote unresolved items to a manual-download queue.
None of those historical options is implied to be a current Reprovane flag.

Raw/enriched/main/excluded/source-audit/preprint outputs, summary and run metadata
preserved audit context. Flattened raw outputs were not necessarily exact original
API response bytes. Hashes proved file consistency rather than authority or rights.
Mirror data could contain gaps; missing publication years were not invented. Current
source H-index values were not historical H-index measurements. Estimated charges
were not official publisher prices. A successful download did not grant permission
to redistribute a PDF. Retain original evidence and examine methodological limits.

Historical [release notes](../RELEASE_NOTES.md), [test report](../TEST_REPORT.md) and
[example notes](../examples/README.md) remain for provenance. The v4 technical details
remain in the English manual; this page does not duplicate its full command catalog.
