"""Small strict validators; errors never contain caller values or provider payloads."""
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..core import ValidationError
from ..core.ids import utc_now
from ..core.provenance import json_text


def text(value: Any, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Required text must be non-empty UTF-8")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValidationError("Text must be valid UTF-8") from None


def namespace(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]*", value):
        raise ValidationError("Namespace must be a lowercase normalized token")


def year(value: Any) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 9999):
        raise ValidationError("Year must be an integer in 1..9999")


def iso_date(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValidationError("Date must be a valid ISO calendar date")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError("Date must be a valid ISO calendar date") from None


def config_hash(value: Any) -> None:
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
        raise ValidationError("Normalization config hash must be lowercase SHA-256")


def object_json(value: str) -> None:
    if not isinstance(json.loads(json_text(value)), dict):
        raise ValidationError("Field provenance must be a JSON object")


def any_field(*values: Any) -> None:
    if all(value is None for value in values):
        raise ValidationError("At least one domain field must be supplied")


def authorship(author_position: int, author_display_name: str, *, author_given_name: str | None,
               author_family_name: str | None, provider_author_id_namespace: str | None,
               provider_author_id_original: str | None, provider_author_id_normalized: str | None,
               orcid_original: str | None, orcid_normalized: str | None) -> None:
    if type(author_position) is not int or not 1 <= author_position < 2**63:
        raise ValidationError("Author position must be a positive SQLite integer")
    text(author_display_name)
    text(author_given_name, optional=True)
    text(author_family_name, optional=True)
    provider_ids = (provider_author_id_namespace, provider_author_id_original, provider_author_id_normalized)
    if any(value is not None for value in provider_ids):
        if any(value is None for value in provider_ids):
            raise ValidationError("Provider author identifier fields must be supplied together")
        namespace(provider_author_id_namespace)
        text(provider_author_id_original)
        text(provider_author_id_normalized)
    if (orcid_original is None) != (orcid_normalized is None):
        raise ValidationError("ORCID original and normalized values must be paired")
    if orcid_normalized is not None:
        text(orcid_original)
        if not isinstance(orcid_normalized, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]", orcid_normalized):
            raise ValidationError("Normalized ORCID must have canonical lexical form")


def metric(value_type: str, value_text: str | None, value_integer: int | None, value_real: float | None) -> None:
    text(value_type)
    values = {"TEXT": value_text, "INTEGER": value_integer, "REAL": value_real}
    if value_type not in values or values[value_type] is None or sum(v is not None for v in values.values()) != 1:
        raise ValidationError("Metric type must select exactly one matching value")
    if value_type == "TEXT":
        text(value_text)
    elif value_type == "INTEGER" and (type(value_integer) is not int or not -(2**63) <= value_integer < 2**63):
        raise ValidationError("Metric integer must fit signed SQLite INTEGER")
    elif value_type == "REAL":
        try:
            valid = type(value_real) in (int, float) and math.isfinite(value_real)
        except OverflowError:
            valid = False
        if not valid:
            raise ValidationError("Metric real must be a finite number")


@dataclass(frozen=True)
class NormalizationProvenance:
    normalizer_name: str
    normalizer_version: str
    observation_schema_version: str
    normalization_config_sha256: str | None = None

    def fields(self) -> dict[str, Any]:
        for v in (self.normalizer_name, self.normalizer_version, self.observation_schema_version):
            text(v)
        config_hash(self.normalization_config_sha256)
        return {"normalizer_name": self.normalizer_name, "normalizer_version": self.normalizer_version,
                "observation_schema_version": self.observation_schema_version,
                "normalization_config_sha256": self.normalization_config_sha256, "normalized_at_utc": utc_now()}
