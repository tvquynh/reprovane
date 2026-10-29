"""Extract only selected typed observations. Never read raw bytes or provider metadata JSON."""
from collections import Counter
from ..core import ValidationError
from .policy import ObservationSelector
from .normalization import normalize_doi, normalize_title, normalize_author, classify_publication_type, VERSIONS


def extract_evidence(db, record_id: str, selector: ObservationSelector) -> dict:
    anchor = db.execute("SELECT r.provider_record_id,r.provider_native_id,p.semantic_namespace,s.sealed_at_utc "
        "FROM provider_record r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id "
        "JOIN provider p ON p.provider_id=s.provider_id WHERE r.provider_record_id=?", (record_id,)).fetchone()
    if anchor is None or anchor["sealed_at_utc"] is None:
        raise ValidationError("Resolution input must reference sealed provider evidence")
    def selected(table):
        return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE provider_record_id=? ORDER BY observation_id", (record_id,))
                if (r["normalizer_name"], r["normalizer_version"], r["observation_schema_version"]) in selector.profiles]
    metadata = selected("publication_metadata_observation")
    identifiers = selected("publication_identifier_observation")
    authors = selected("publication_authorship_observation")
    venues = selected("venue_identifier_observation")
    warnings = []
    if len(metadata) > 1:
        warnings.append("AMBIGUOUS_SELECTED_METADATA")
    if any(n > 1 for n in Counter(r["author_position"] for r in authors).values()):
        # Conservative across all selected profiles; do not arbitrarily choose a preferred profile.
        warnings.append("AMBIGUOUS_AUTHORSHIP_POSITION")
    dois, invalid = set(), []
    for row in identifiers:
        if row["identifier_namespace"] == "doi":
            doi = normalize_doi(row["identifier_value_normalized"])
            if doi:
                dois.add(doi)
            else:
                invalid.append(row["observation_id"])
    titles = sorted({(normalize_title(r["title"] or ""), r["publication_year"]) for r in metadata
                     if r["title"] and r["publication_year"] is not None})
    authors.sort(key=lambda r: (r["author_position"], r["normalized_at_utc"], r["observation_id"]))
    sequence = [dict(observation_id=r["observation_id"], author_position=r["author_position"],
                     display_normalized=normalize_author(r["author_display_name"]),
                     family_normalized=normalize_author(r["author_family_name"] or "")) for r in authors]
    first = (sequence[0]["family_normalized"] or sequence[0]["display_normalized"]) if sequence else ""
    last = (sequence[-1]["family_normalized"] or sequence[-1]["display_normalized"]) if sequence else ""
    only = metadata[0] if len(metadata) == 1 else {}
    native = anchor["provider_native_id"]
    native = native if isinstance(native, str) and native.strip() else None
    return dict(record_id=record_id, provider_namespace=anchor["semantic_namespace"], provider_native_id=native,
        valid_dois=sorted(dois), invalid_or_unusable_doi_observation_ids=sorted(invalid),
        title=only.get("title"), title_normalized=normalize_title(only.get("title") or ""),
        publication_year=only.get("publication_year"), publication_date=only.get("publication_date"),
        publication_type=only.get("publication_type"), publication_type_class=classify_publication_type(only.get("publication_type")),
        title_year_keys=[list(x) for x in titles], authorship=sequence, first_author_normalized=first, last_author_normalized=last,
        venue_identifier_claims=sorted([dict(namespace=r["identifier_namespace"], value=r["identifier_value_normalized"],
            observation_id=r["observation_id"]) for r in venues], key=lambda r: (r["namespace"], r["value"], r["observation_id"])),
        observation_ids_used=sorted(r["observation_id"] for rows in (metadata, identifiers, authors, venues) for r in rows),
        evidence_warnings=sorted(warnings), normalization_versions=VERSIONS)
