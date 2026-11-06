"""Exact identifier normalization and legacy file-name keys. No fuzzy or title-based identity."""
import re

from ..fulltext import paper_key as legacy_paper_key, host_of
from ..resolution.normalization import normalize_doi as _normalize_doi

_OPENALEX = re.compile(r'(?:https?://(?:www\.)?openalex\.org/(?:works/)?)?(W[0-9]+)', re.I)

__all__ = ['doi', 'openalex_work', 'legacy_key', 'legacy_doi_key', 'host_of']


def doi(value):
    """Return a normalized DOI, or None when the value is absent or malformed."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _normalize_doi(value)


def openalex_work(value):
    """Return the short OpenAlex Work ID (W123), or None when the value is not a Work ID."""
    if not isinstance(value, str):
        return None
    match = _OPENALEX.fullmatch(value.strip())
    return match[1].upper() if match else None


def legacy_doi_key(normalized_doi):
    """The file-name stem v4.1.0 fetch-oa used for a paper identified only by DOI."""
    return 'doi_' + re.sub(r'[^a-z0-9._-]', '_', normalized_doi.lower())


def legacy_key(original):
    """Exact v4.1.0 paper_key for the original field values (openalex_id, doi, title, year, URLs)."""
    return legacy_paper_key(original)
