# Reprovane

Reproducible literature discovery, screening, and evidence management.

Reprovane is an open-source tool that helps researchers collect literature,
record review decisions, freeze a review corpus, and organize eligible
open-access full text with a traceable record of the work.

**Reprovane 2.0.0 was the first public release of Reprovane.**

Human reviewers remain responsible for screening and study inclusion decisions.

## What Reprovane helps you do

- Search and collect literature from OpenAlex with reproducible provenance.
- Expand your search through backward and forward citations.
- Track venue evidence from SCImago, Scopus, and Web of Science.
- Record human screening decisions and resolve reviewer disagreements.
- Freeze a review corpus with its selected studies and supporting metadata.
- Find eligible open-access full text and keep unresolved items in a manual queue.
- Check the integrity of your workspace and exported results.

## Who it is for

Researchers, students, librarians and review teams who want to explain how their
literature review was performed. Reprovane uses terminal commands rather than a
graphical application; some advanced steps need a team member to prepare structured plans.

## Install

Requires **Python 3.11 or newer**.

```bash
pip install reprovane
```

**New to Python or command-line tools? Start with the
[Beginner Quick Start](https://github.com/tvquynh/reprovane/blob/main/QUICKSTART.md).**
It explains how to install Python, open a terminal and use a private software
environment on Windows, macOS and Linux. No programming knowledge is assumed.

## First 5 minutes

```bash
reprovane --version
reprovane doctor --offline
reprovane workspace init --workspace "my-review"
```

These first steps work offline and do not need an API key.
Continue with the [Quick Start](https://github.com/tvquynh/reprovane/blob/main/QUICKSTART.md) to create your first project,
then use the [User Guide](https://github.com/tvquynh/reprovane/blob/main/USER_GUIDE.md) for a complete review workflow.

## Important human-review boundary

Reprovane does not decide which studies belong in your review. Venue rankings,
citation counts and full-text availability do not determine study inclusion.

## How Reprovane works

Define your project → collect literature → follow citations → review studies
→ freeze your corpus → organize full text → verify and export.

Keep your search plans and reviewer decisions alongside the results so the
review process can be inspected later. Full-text availability does not decide
whether a study belongs in your review.

## Documentation

- [Quick Start — Windows, Linux, and macOS](https://github.com/tvquynh/reprovane/blob/main/QUICKSTART.md)
- [User Guide — from search to corpus and full text](https://github.com/tvquynh/reprovane/blob/main/USER_GUIDE.md)
- [Operations — backups, recovery, and troubleshooting](https://github.com/tvquynh/reprovane/blob/main/OPERATIONS.md)
- [Security and privacy](https://github.com/tvquynh/reprovane/blob/main/SECURITY.md)
- [Third-party data and redistribution](https://github.com/tvquynh/reprovane/blob/main/THIRD_PARTY_DATA.md)
- [Changelog](https://github.com/tvquynh/reprovane/blob/main/CHANGELOG.md)

For development and architecture details, see [Architecture](https://github.com/tvquynh/reprovane/blob/main/ARCHITECTURE.md).
Source and issues: [tvquynh/reprovane](https://github.com/tvquynh/reprovane).

## License

Reprovane is available under the [MIT License](https://github.com/tvquynh/reprovane/blob/main/LICENSE).
