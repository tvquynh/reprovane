# Third-party data notes

This repository does not bundle the SCImago CSV datasets. At runtime it references the configured SCImago research mirror described by `manifest.json` and validates downloaded bytes with `SHA256SUMS.txt`.

The tool also uses OpenAlex API metadata.

Users who redistribute datasets or derived datasets should independently review the applicable terms, licenses, attribution requirements, and redistribution restrictions of the original data providers. The tool's presence or a successful download is not a grant of redistribution rights.

The default mirror (`https://scopus.cuatui.cloud/du-lieu/scimago/manifest.json`) is a community mirror maintained by the author on a best-effort basis, without an availability guarantee. Any mirror with the same `manifest.json` and `SHA256SUMS.txt` layout can be used through `scimago.manifest_url` in `config.json` or `--manifest-url`.
