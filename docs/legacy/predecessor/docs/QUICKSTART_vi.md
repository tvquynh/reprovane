# Historical predecessor Quick Start (v4.1.0)

This English historical summary retains an old documentation path. It is not the
current installation tutorial. Use the [Reprovane Quick Start](../../../../QUICKSTART.md)
for current work and the [archived English manual](USER_GUIDE.md) for v4 details.

The predecessor used Python, a private environment and a separate local config.json.
Command arguments took precedence over configuration and then code defaults.
OpenAlex run required its own key; users were expected to keep configuration private.
SCImago synchronization validated checksums before enrichment. A bounded test query
preceded larger runs. Historical journal/latest-snapshot filters, quartile policies,
venue tiers and preprint exports differed from current human screening semantics.

Historical fetch-oa wrote unresolved work to a manual-download queue and did not
retry anti-bot challenges. Its manual-PDF handling is not a current Reprovane ingest
feature. Reprovane manual ingest remains unimplemented. Do not copy old flags into
current commands or change endpoint security to activate an old environment.
