"""Read-only import of a v4.1.0 `fetch-oa` output folder into a discovery run.

The legacy folder is never modified. Downloaded PDFs whose bytes still match fetch_state.json become
LEGACY_V4 artifacts; files in pdf_manual/ become manual imports; blocked_hosts.json entries become host
blocks marked LEGACY_V4_IMPORT. Historical statuses stay evidence only.
"""
import json
import re
from pathlib import Path

from ..core import ValidationError
from ..corpus_release.model import sha
from ..legacy_migration.inventory import runtime_private, sensitive
from ..fulltext import host_of
from ..fulltext_acquisition.safety import sanitize_url
from .manual import DiscoveryContext, collect, ingest

MAX_STATE_BYTES = 256 * 1024 * 1024
_STATUS = re.compile(r'[A-Z][A-Z0-9_]{0,63}')


REDACTED = 'REDACTED_PRIVATE_LOOKING_TEXT'


def _text(value, private, limit=300):
    """A copied 4.1.0 string, or REDACTED when it carries a runtime secret or private-looking text."""
    if not isinstance(value, str) or not value:
        return None
    value = ' '.join(value.split())[:limit]
    return REDACTED if sensitive(value.encode('utf-8'), private) else value


def _url(value, private):
    if not isinstance(value, str) or not value:
        return None
    try:
        return _text(sanitize_url(value), private, 2000)
    except ValidationError:
        return 'UNSAFE_URL'


def _history(papers, private):
    """Every 4.1.0 per-paper outcome, kept as sanitized historical evidence (never a new attempt or a decision).

    Every copied string is checked against the runtime secrets and private-looking patterns; URLs are sanitized.
    """
    rows = []
    for key, entry in sorted(papers.items()):
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        status = entry.get('status') if isinstance(entry.get('status'), str) else ''
        if not _STATUS.fullmatch(status) or sensitive(status.encode('utf-8'), private):
            status = 'UNRECOGNIZED'
        rows.append(dict(legacy_key=_text(key, private), status=status,
                         detail=_text(entry.get('detail'), private), url=_url(entry.get('url'), private),
                         host=_text(entry.get('host'), private, 255)))
    return rows


def _json(path):
    if not path.is_file() or path.is_symlink():
        return None, None
    body = path.read_bytes()
    if len(body) > MAX_STATE_BYTES:
        raise ValidationError('Legacy state file exceeds the finite byte limit')
    return json.loads(body.decode('utf-8-sig')), sha(body)


def _inside(root, relative):
    target = (root / relative)
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        return None
    return target


def import_legacy_fulltext(store, rid, source, *, import_blocked_hosts=True):
    root = Path(source)
    if root.is_symlink() or not root.is_dir():
        raise ValidationError('Legacy fetch-oa output must be an ordinary directory')
    state, state_sha = _json(root / 'fetch_state.json')
    blocked, blocked_sha = _json(root / 'blocked_hosts.json')
    if state is None and not (root / 'pdf').is_dir() and not (root / 'pdf_manual').is_dir():
        raise ValidationError('No fetch_state.json, pdf/ or pdf_manual/ found in the legacy folder')
    papers = (state or {}).get('papers', {}) if isinstance(state, dict) else {}
    ctx = DiscoveryContext(store, rid)
    private = tuple(v for v in runtime_private() if v)
    specs, covered = [], set()
    for key, entry in sorted(papers.items()):
        if not isinstance(entry, dict) or entry.get('status') not in ('DOWNLOADED', 'MANUAL_DOWNLOADED') or not entry.get('file'):
            continue
        target = _inside(root, entry['file'])
        if target is None or not target.is_file():
            continue
        rel = target.relative_to(root).as_posix()
        covered.add(rel)
        url = _url(entry.get('url'), private)
        manual = entry['status'] == 'MANUAL_DOWNLOADED'
        # Only a recorded, well-formed SHA-256 that matches the file gives fetch-state authority (any historical key).
        recorded = entry.get('sha256').lower() if isinstance(entry.get('sha256'), str) else ''
        verified = bool(re.fullmatch(r'[0-9a-f]{64}', recorded))
        specs.append(dict(rel=rel, path=target, forced_key=key, expected_sha256=recorded if verified else None,
                          basis='LEGACY_V4_MANUAL_KEY' if manual else
                          'LEGACY_V4_FETCH_STATE' if verified else 'LEGACY_V4_FETCH_STATE_NO_SHA',
                          link_source='MANUAL' if manual else 'LEGACY_V4',
                          detail=dict(legacy_status=entry['status'], legacy_key=_text(key, private), legacy_source_url=url,
                                      legacy_host=_text(entry.get('host'), private, 255) or
                                      (host_of(url) if url and url not in ('UNSAFE_URL', REDACTED) else None))))
    for folder, source_kind, basis in (('pdf_manual', 'MANUAL', 'EXACT_FILENAME_KEY'), ('pdf', 'LEGACY_V4', 'LEGACY_V4_FILENAME_KEY')):
        if (root / folder).is_dir() and not (root / folder).is_symlink():
            _, files, _ = collect(root / folder, recursive=False)
            for rel, path in files:
                rel = folder + '/' + rel
                if rel not in covered:
                    specs.append(dict(rel=rel, path=path, basis=basis, link_source=source_kind,
                                      detail=dict(legacy_folder=folder, legacy_status=None)))
    detail = dict(legacy_tool_version=_text((state or {}).get('tool_version'), private, 40) if isinstance(state, dict) else None,
                  fetch_state_sha256=state_sha, blocked_hosts_sha256=blocked_sha,
                  interpretation='Historical v4.1.0 fetch-oa evidence; statuses are not re-interpreted as screening.',
                  history=_history(papers, private))
    result = ingest(ctx, specs, session_kind='LEGACY_V4_FULLTEXT', source_label='legacy-fetch-oa-output', session_detail=detail,
                    always_session=True, private=private)
    imported_hosts = []
    if import_blocked_hosts and isinstance(blocked, dict):
        for host, info in sorted(blocked.items()):
            info = info if isinstance(info, dict) else {}
            if not isinstance(host, str) or not host or store.host_state(host)['blocked']:
                continue
            status = info.get('http_status') if isinstance(info.get('http_status'), int) else None
            if _text(host, private, 255) != host:
                continue  # a host name carrying a private value is not recorded
            url = _url(info.get('url'), private)
            store.host_event(host, 'BLOCK', 'legacy v4.1.0: ' + str(info.get('reason') or 'blocked'), http_status=status,
                             private=private, url=url if url not in ('UNSAFE_URL', REDACTED) else None, run_id=rid,
                             origin='LEGACY_V4_IMPORT')
            imported_hosts.append(host)
    result.update(legacy={k: v for k, v in detail.items() if k != 'history'}, history_entries=len(detail['history']),
                  blocked_hosts_imported=imported_hosts)
    return result
