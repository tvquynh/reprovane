"""Canonical hash serialization and credential-safe provenance locators."""
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import ValidationError
from ..common import redact_secrets

SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "apikey", "access_token", "token", "key", "sig", "signature", "password", "passwd",
    "x-amz-signature", "x-goog-signature",
})


def canonical_json(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        text.encode("utf-8")
        return text
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValidationError("Value cannot be serialized as canonical JSON") from None


def json_text(value: str) -> str:
    """Validate JSON text without rewriting it; non-finite constants are invalid."""
    if not isinstance(value, str):
        raise ValidationError("JSON must be text")
    try:
        json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        value.encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise ValidationError("Invalid JSON text") from None
    return value


def sanitize_locator(locator: str | None) -> str | None:
    if locator is None:
        return None
    if not isinstance(locator, str):
        raise ValidationError("Provenance locator must be text")
    try:
        parts = urlsplit(locator)
        if not parts.netloc:
            return redact_secrets(locator)
        # Retain host/port spelling (including IPv6 brackets); discard ALL userinfo.
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(k, "REDACTED" if k.casefold() in SENSITIVE_QUERY_KEYS else v)
                           for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        # Fragment can carry token=... as well; apply the existing generic redactor.
        return redact_secrets(urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment)))
    except (ValueError, UnicodeError):
        raise ValidationError("Invalid provenance locator") from None
