"""Versioned exact evidence transforms; no fuzzy, probabilistic or identity inference."""
import re
import unicodedata

VERSIONS = {"doi": "doi_normalizer_v1", "title": "publication_title_normalizer_v1",
            "author": "author_name_normalizer_v1", "publication_type": "publication_type_classifier_v1"}


def normalize_doi(value: str) -> str | None:
    text = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text if re.fullmatch(r"10\.[0-9]{4,9}/\S+", text) else None


def _words(value: str, ampersand: bool) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    if ampersand:
        text = text.replace("&", " and ")
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def normalize_title(value: str) -> str:
    return _words(value, True)


def normalize_author(value: str) -> str:
    return _words(value, False)


def classify_publication_type(value: str | None) -> str:
    token = _words(value or "", False)
    notices = {"retraction", "retraction notice", "correction", "correction notice", "erratum",
               "expression of concern", "withdrawal", "removal"}
    return "NOTICE" if token in notices else "OTHER_OR_UNKNOWN"
