# Third-party data and redistribution

OpenAlex supplies literature metadata. SCImago supplies ranking/presence evidence;
Scopus supplies membership/status evidence; Web of Science supplies collection
evidence. These meanings stay separate, with their own snapshots and scope.
Unpaywall may supply open-access locations when enabled with a contact email.
Reprovane does not package real research PDFs or provider venue datasets in its
wheel or source distribution.

Record provider, snapshot, reference/commit, checksum and retrieval time. A valid
checksum shows consistency with an expected file; it does not make a mirror
authoritative or justify extrapolating ranking/membership beyond the data.
The [pinned-mirror contract](docs/architecture/pinned_github_mirror_sync.md) describes
the existing GitHub source pinning model. [User Guide](USER_GUIDE.md) explains how
provider evidence differs from human decisions and full-text artifacts.

Before sharing metadata, datasets, a corpus or PDFs, the review lead must check
each source's applicable terms, license, attribution and redistribution limits.
A successful download is not a license to redistribute. When rights are unclear,
share only permitted reports/manifests and seek review from the responsible person.
Do not publish a whole workspace, private key, contact value or private URL.

Historical documentation in [docs/legacy](docs/legacy/README.md) describes predecessor
mirrors/configuration/commands. Those historical flags do not automatically apply
to the current program. Do not use missing full text to exclude a study from the corpus.
