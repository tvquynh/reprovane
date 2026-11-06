"""Structural PDF check shared by downloads, manual import and verification."""
import logging

from ..fulltext_acquisition.safety import validate_pdf as _stage11b_validate_pdf

_PYPDF = logging.getLogger('pypdf')


def validate_pdf(body, policy):
    """Run the Stage11B structural validation without printing pypdf repair warnings.

    pypdf logs recoverable repairs (for example a non-zero-indexed xref table) as warnings. They do not
    change the outcome of the check, so they are kept out of the operator's terminal.
    """
    previous = _PYPDF.level
    _PYPDF.setLevel(logging.ERROR)
    try:
        return _stage11b_validate_pdf(body, policy)
    finally:
        _PYPDF.setLevel(previous)
