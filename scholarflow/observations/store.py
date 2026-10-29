"""Typed table APIs; a claim has one ProviderRecord anchor, never a canonical entity."""
from contextlib import nullcontext
from typing import Any

from ..core import ValidationError
from ..core.db import Database
from ..core.ids import new_id
from ..core.migrations import apply_migrations
from . import validation as v
from .validation import NormalizationProvenance

Row = dict[str, Any]
TABLES = {
    "pmo": "publication_metadata_observation", "pio": "publication_identifier_observation",
    "vo": "venue_observation", "vio": "venue_identifier_observation", "vmo": "venue_membership_observation",
    "vmeo": "venue_metric_observation", "ceo": "citation_edge_observation", "ieo": "integrity_event_observation",
    "pao": "publication_authorship_observation",
}
EVENT_TYPES = frozenset({"RETRACTION", "CORRECTION", "EXPRESSION_OF_CONCERN", "WITHDRAWAL", "REMOVAL", "OTHER"})


class ObservationStore:
    def __init__(self, database: Database):
        self.db = database
        # Explicit feature opt-in: bare Database retains the accepted v1 default.
        # The migrator validates all already-applied hashes and never downgrades.
        apply_migrations(database.connection, target_version=3)

    def _add(self, prefix: str, provider_record_id: str, provenance: NormalizationProvenance, fields: Row) -> Row:
        v.text(provider_record_id)
        if not isinstance(provenance, NormalizationProvenance):
            raise ValidationError("Normalization provenance is required")
        common = provenance.fields()
        row = dict(observation_id=new_id(prefix), provider_record_id=provider_record_id, **fields, **common)
        with (nullcontext() if self.db.connection.in_transaction else self.db.transaction()):
            self.db.execute(f"INSERT INTO {TABLES[prefix]} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                            tuple(row.values()))
        return row.copy()

    def add_publication_metadata_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            title: str | None = None, subtitle: str | None = None, abstract_text: str | None = None,
            publication_type: str | None = None, publication_date: str | None = None, publication_year: int | None = None,
            language_code: str | None = None, volume: str | None = None, issue: str | None = None,
            first_page: str | None = None, last_page: str | None = None, article_number: str | None = None,
            publisher_name: str | None = None, field_provenance_json: str = "{}") -> Row:
        fields = dict(title=title, subtitle=subtitle, abstract_text=abstract_text, publication_type=publication_type,
            publication_date=publication_date, publication_year=publication_year, language_code=language_code,
            volume=volume, issue=issue, first_page=first_page, last_page=last_page, article_number=article_number,
            publisher_name=publisher_name)
        v.any_field(*fields.values()); v.year(publication_year); v.iso_date(publication_date)
        for key, value in fields.items():
            if key != "publication_year":
                v.text(value, optional=True)
        v.object_json(field_provenance_json)
        return self._add("pmo", provider_record_id, provenance, dict(**fields, field_provenance_json=field_provenance_json))

    def add_publication_identifier_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            identifier_namespace: str, identifier_value_original: str, identifier_value_normalized: str,
            source_field_path: str | None = None) -> Row:
        return self._identifier("pio", provider_record_id, provenance, identifier_namespace,
                                identifier_value_original, identifier_value_normalized, source_field_path)

    def _identifier(self, prefix: str, provider_record_id: str, provenance: NormalizationProvenance,
                    namespace: str, original: str, normalized: str, path: str | None) -> Row:
        v.namespace(namespace); v.text(original); v.text(normalized); v.text(path, optional=True)
        return self._add(prefix, provider_record_id, provenance, dict(identifier_namespace=namespace,
            identifier_value_original=original, identifier_value_normalized=normalized, source_field_path=path))

    def add_venue_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            title: str | None = None, publisher_name: str | None = None, source_type: str | None = None,
            country_code: str | None = None, field_provenance_json: str = "{}") -> Row:
        fields = dict(title=title, publisher_name=publisher_name, source_type=source_type, country_code=country_code)
        v.any_field(*fields.values())
        for value in fields.values():
            v.text(value, optional=True)
        v.object_json(field_provenance_json)
        return self._add("vo", provider_record_id, provenance, dict(**fields, field_provenance_json=field_provenance_json))

    def add_venue_identifier_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            identifier_namespace: str, identifier_value_original: str, identifier_value_normalized: str,
            source_field_path: str | None = None) -> Row:
        return self._identifier("vio", provider_record_id, provenance, identifier_namespace,
                                identifier_value_original, identifier_value_normalized, source_field_path)

    def add_venue_membership_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            membership_namespace: str, collection_code: str | None = None, status_code: str | None = None,
            coverage_start_year: int | None = None, coverage_end_year: int | None = None,
            valid_from_date: str | None = None, valid_to_date: str | None = None,
            source_field_path: str | None = None) -> Row:
        v.namespace(membership_namespace); v.any_field(collection_code, status_code)
        v.year(coverage_start_year); v.year(coverage_end_year)
        if coverage_start_year is not None and coverage_end_year is not None and coverage_start_year > coverage_end_year:
            raise ValidationError("Coverage years must be ordered")
        for value in (valid_from_date, valid_to_date):
            v.iso_date(value)
        for value in (collection_code, status_code, source_field_path):
            v.text(value, optional=True)
        return self._add("vmo", provider_record_id, provenance, dict(membership_namespace=membership_namespace,
            collection_code=collection_code, status_code=status_code, coverage_start_year=coverage_start_year,
            coverage_end_year=coverage_end_year, valid_from_date=valid_from_date, valid_to_date=valid_to_date,
            source_field_path=source_field_path))

    def add_venue_metric_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            metric_namespace: str, metric_code: str, value_type: str, value_text: str | None = None,
            value_integer: int | None = None, value_real: float | None = None, period_year: int | None = None,
            category_name: str | None = None, unit: str | None = None, source_field_path: str | None = None) -> Row:
        v.namespace(metric_namespace); v.text(metric_code); v.year(period_year)
        v.metric(value_type, value_text, value_integer, value_real)
        if value_type == "REAL":
            value_real = float(value_real)
        for value in (category_name, unit, source_field_path):
            v.text(value, optional=True)
        return self._add("vmeo", provider_record_id, provenance, dict(metric_namespace=metric_namespace,
            metric_code=metric_code, period_year=period_year, category_name=category_name, value_type=value_type,
            value_text=value_text, value_integer=value_integer, value_real=value_real, unit=unit, source_field_path=source_field_path))

    def add_citation_edge_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            source_identifier_namespace: str, source_identifier_value: str, target_identifier_namespace: str,
            target_identifier_value: str, relation_type: str = "CITES", source_field_path: str | None = None) -> Row:
        v.namespace(source_identifier_namespace); v.namespace(target_identifier_namespace)
        v.text(source_identifier_value); v.text(target_identifier_value); v.text(source_field_path, optional=True)
        if relation_type != "CITES":
            raise ValidationError("Unsupported scholarly citation relation")
        return self._add("ceo", provider_record_id, provenance, dict(source_identifier_namespace=source_identifier_namespace,
            source_identifier_value=source_identifier_value, target_identifier_namespace=target_identifier_namespace,
            target_identifier_value=target_identifier_value, relation_type=relation_type, source_field_path=source_field_path))

    def add_integrity_event_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            event_type: str, subject_identifier_namespace: str, subject_identifier_value: str,
            related_notice_identifier_namespace: str | None = None, related_notice_identifier_value: str | None = None,
            effective_date: str | None = None, status_code: str | None = None, source_field_path: str | None = None) -> Row:
        v.text(event_type)
        if event_type not in EVENT_TYPES:
            raise ValidationError("Unsupported integrity event type")
        v.namespace(subject_identifier_namespace); v.text(subject_identifier_value); v.iso_date(effective_date)
        if (related_notice_identifier_namespace is None) != (related_notice_identifier_value is None):
            raise ValidationError("Related notice namespace and value must be paired")
        if related_notice_identifier_namespace is not None:
            v.namespace(related_notice_identifier_namespace); v.text(related_notice_identifier_value)
        v.text(status_code, optional=True); v.text(source_field_path, optional=True)
        return self._add("ieo", provider_record_id, provenance, dict(event_type=event_type,
            subject_identifier_namespace=subject_identifier_namespace, subject_identifier_value=subject_identifier_value,
            related_notice_identifier_namespace=related_notice_identifier_namespace,
            related_notice_identifier_value=related_notice_identifier_value, effective_date=effective_date,
            status_code=status_code, source_field_path=source_field_path))

    def get_observation(self, observation_id: str) -> Row:
        v.text(observation_id)
        prefix = observation_id.split("_", 1)[0]
        if prefix not in TABLES:
            raise ValidationError("Unknown observation ID prefix")
        row = self.db.execute(f"SELECT * FROM {TABLES[prefix]} WHERE observation_id=?", (observation_id,)).fetchone()
        if row is None:
            raise ValidationError("Observation does not exist")
        return dict(row)

    def add_publication_authorship_observation(self, provider_record_id: str, provenance: NormalizationProvenance, *,
            author_position: int, author_display_name: str, author_given_name: str | None = None,
            author_family_name: str | None = None, provider_author_id_namespace: str | None = None,
            provider_author_id_original: str | None = None, provider_author_id_normalized: str | None = None,
            orcid_original: str | None = None, orcid_normalized: str | None = None,
            source_field_path: str | None = None, field_provenance_json: str = "{}") -> Row:
        v.authorship(author_position, author_display_name, author_given_name=author_given_name,
            author_family_name=author_family_name, provider_author_id_namespace=provider_author_id_namespace,
            provider_author_id_original=provider_author_id_original,
            provider_author_id_normalized=provider_author_id_normalized,
            orcid_original=orcid_original, orcid_normalized=orcid_normalized)
        v.text(source_field_path, optional=True)
        v.object_json(field_provenance_json)
        return self._add("pao", provider_record_id, provenance, dict(author_position=author_position,
            author_display_name=author_display_name, author_given_name=author_given_name,
            author_family_name=author_family_name, provider_author_id_namespace=provider_author_id_namespace,
            provider_author_id_original=provider_author_id_original,
            provider_author_id_normalized=provider_author_id_normalized,
            orcid_original=orcid_original, orcid_normalized=orcid_normalized, source_field_path=source_field_path,
            field_provenance_json=field_provenance_json))

    def list_publication_authorship_observations(self, provider_record_id: str | None = None) -> list[Row]:
        if provider_record_id is not None:
            v.text(provider_record_id)
        return [dict(r) for r in self.db.execute("SELECT * FROM publication_authorship_observation "
            "WHERE (? IS NULL OR provider_record_id=?) ORDER BY author_position, normalized_at_utc, observation_id",
            (provider_record_id, provider_record_id))]

    def _list(self, prefix: str, provider_record_id: str | None) -> list[Row]:
        return [dict(r) for r in self.db.execute(f"SELECT * FROM {TABLES[prefix]} "
            "WHERE (? IS NULL OR provider_record_id=?) ORDER BY normalized_at_utc, observation_id",
            (provider_record_id, provider_record_id))]

    def list_observations_for_provider_record(self, provider_record_id: str) -> list[Row]:
        v.text(provider_record_id)
        return sorted((r for prefix in TABLES for r in self._list(prefix, provider_record_id)),
                      key=lambda r: (r["normalized_at_utc"], r["observation_id"]))

    def list_publication_metadata_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("pmo", provider_record_id)

    def list_publication_identifier_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("pio", provider_record_id)

    def list_venue_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("vo", provider_record_id)

    def list_venue_identifier_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("vio", provider_record_id)

    def list_venue_membership_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("vmo", provider_record_id)

    def list_venue_metric_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("vmeo", provider_record_id)

    def list_citation_edge_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("ceo", provider_record_id)

    def list_integrity_event_observations(self, provider_record_id: str | None = None) -> list[Row]:
        return self._list("ieo", provider_record_id)
