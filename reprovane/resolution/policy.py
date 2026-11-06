"""Canonical versioned policy and exact observation-profile selector."""
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from ..core import ValidationError
from ..core.provenance import canonical_json, json_text
from ..observations.validation import text

POLICY_NAME = "conservative_publication_v1"
POLICY_VERSION = "1"
RESOLVER_NAME, RESOLVER_VERSION = "conservative_publication", "1"


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def object_value(value: str | dict) -> dict:
    if isinstance(value, str):
        value = json.loads(json_text(value))
    if not isinstance(value, dict):
        raise ValidationError("Configuration must be a JSON object")
    canonical_json(value)
    return value


@dataclass(frozen=True)
class ResolverPolicy:
    max_candidate_block_size: int = 25

    def as_dict(self) -> dict:
        if type(self.max_candidate_block_size) is not int or self.max_candidate_block_size < 2:
            raise ValidationError("Candidate block limit must be an integer >=2")
        return {"auto_same": ["SAME_PROVIDER_NATIVE_ID", "SINGLE_SHARED_VALID_DOI"], "auto_distinct": [],
                "candidate_methods": ["SAME_PROVIDER_NATIVE_ID", "SHARED_VALID_DOI", "EXACT_TITLE_YEAR", "EXACT_TITLE_FIRST_AUTHOR_YEAR"],
                "hard_conflicts": ["DISTINCT_VALID_DOI", "MULTIPLE_VALID_DOI_CLAIMS", "NOTICE_TYPE_CONFLICT",
                                   "AMBIGUOUS_SELECTED_METADATA", "AMBIGUOUS_AUTHORSHIP_POSITION"],
                "max_candidate_block_size": self.max_candidate_block_size, "allow_fuzzy_auto_merge": False}

    @classmethod
    def from_value(cls, value: str | dict | None):
        if value is None:
            return cls()
        obj = object_value(value)
        result = cls(obj.get("max_candidate_block_size", 25))
        if obj != result.as_dict():
            raise ValidationError("Only the versioned conservative publication policy is supported")
        return result


@dataclass(frozen=True)
class ObservationSelector:
    profiles: tuple[tuple[str, str, str], ...]

    @classmethod
    def from_value(cls, value: str | dict):
        obj = object_value(value)
        if set(obj) != {"profiles"} or not isinstance(obj["profiles"], list) or not obj["profiles"]:
            raise ValidationError("Selector requires explicit observation profiles")
        profiles = []
        for profile in obj["profiles"]:
            if not isinstance(profile, dict) or set(profile) != {"normalizer_name", "normalizer_version", "observation_schema_version"}:
                raise ValidationError("Invalid selector profile")
            row = tuple(profile[k] for k in ("normalizer_name", "normalizer_version", "observation_schema_version"))
            for v in row:
                text(v)
            profiles.append(row)
        return cls(tuple(sorted(set(profiles))))

    def as_dict(self) -> dict:
        return {"profiles": [dict(zip(("normalizer_name", "normalizer_version", "observation_schema_version"), row)) for row in self.profiles]}
