# Reprovane provenance

Lineage: **OpenAlex Literature Intelligence 4.1.0 → ScholarFlow 1.0.0 → Reprovane 2.0.0**.

Reprovane 2.0.0 is the breaking distribution/import/CLI rebrand of the accepted ScholarFlow 1.0.0 source at commit `95120023dc738021963b8b1f78f81a7cfd6b2d4b`, tree `74d34d63588dfb05385c950f96c6e0d37decd3b7`. Scientific semantics, schema13, migration bytes, database/CAS paths and the actual `.litintel-writer.lock` locator remain unchanged. No `.reprovane-writer.lock` is introduced. Old documentation's `.scholarflow-writer.lock` wording did not match the shipped v1 implementation and is corrected here.

This PR remains in `tvquynh/scholarflow`; the future in-place repository name is `tvquynh/reprovane`, only after separate acceptance/merge. No repository rename, v2 tag/release or registry publication is performed by this preparation. Historical v1 stable/RC tags, release IDs and assets remain intact. The predecessor remains archived and unchanged.

The ScholarFlow repository began as a separate bootstrap, not imported predecessor Git ancestry. Reprovane continues that same repository's history. The following historical bootstrap record is preserved as a dated account of that transition; statements about unpublished bootstrap activity describe that earlier milestone, not the present remote state.

## Historical ScholarFlow bootstrap record

# ScholarFlow provenance

ScholarFlow is the successor to `tvquynh/openalex-literature-intelligence`, not a v5 rename-in-place. The predecessor's legacy/tool baseline is v4.1.0; architecture development continued through the technically accepted Stage12 implementation. ScholarFlow starts its own product version line at 1.0.0, beginning with 1.0.0rc1.

- Legacy baseline: `f70796d1ef6003e24ce3afa1328088cc515dc0a0`.
- Accepted Stage11B baseline: `87da7dff3e2ddc6dcbac0ec5fe6e8dd8241f88b5`, tree `2df6fd9a777422b5c686f42e6f3864d6d797a62a`.
- Technically accepted Stage12 implementation: `6ad3dde25d2a7a6e21591e8e6f131f3885b62ce7`, tree `0223eb31cc71d7dc18c449157d9ea20a1c9b2818`.

The predecessor Stage12 technical candidate was 5.0.0rc1; that artifact is not a ScholarFlow release. No scientific identity, screening, corpus, provider or full-text policy is redesigned here. All thirteen SQL migration filenames/order/bytes are retained. The schema version remains 13 while the product line resets to 1.

Technical workspace compatibility allowlist: `db/litintel.sqlite` and `.litintel-writer.lock` are established on-disk format/locking locators, not product/package/CLI names. Keeping them permits schema13 workspaces and writer exclusion to interoperate without an artificial data migration. Only those exact filename literals remain in the canonical namespace; no runtime import requires the predecessor package.

All accepted semantic tests are ported to scholarflow. Historical CLI/config regression references are retained under tests/legacy_reference solely to preserve scientific coverage; they are excluded from runtime wheel/sdist and are not installed console commands. Historical manuals/notices are isolated in docs/legacy/. Copyright/license notices retain their original attribution.

Intended successor repository: https://github.com/tvquynh/scholarflow. No repository has been created or pushed by this bootstrap. The release manifest records deterministic bootstrap tree/hash and artifact hashes externally to avoid self-referential source hashes. Final acceptance and any later stable release remain human/review actions.
