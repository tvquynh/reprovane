"""Input adapters: legacy v4.1.0 / Reprovane CSV-JSONL files, identifier lists, saved OpenAlex works and
sealed corpus handoffs. Every adapter is a pure function of its exact source so verification can re-derive
the stored records. Records are acquisition evidence only; they never become screening decisions."""
import csv
import io
import json
import re
from pathlib import Path

from ..core import ValidationError
from ..corpus_release.model import digest, sha
from ..fulltext_acquisition.candidates import location_routes
from ..fulltext_acquisition.safety import normalize_url, sanitize_url
from ..legacy_migration.inventory import family as legacy_family
from . import identifiers as ids

MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_ROWS = 2_000_000
FORMATS = ('auto', 'legacy-v4', 'openalex-works', 'reprovane-corpus', 'reprovane-pdf-export', 'generic', 'identifiers')
FORMAT_LABELS = {'legacy-v4': 'LEGACY_V4_1_0_FLAT', 'openalex-works': 'OPENALEX_WORK_JSON',
                 'reprovane-corpus': 'REPROVANE_CORPUS_EXPORT', 'reprovane-pdf-export': 'REPROVANE_PDF_EXPORT',
                 'generic': 'GENERIC_TABLE', 'identifiers': 'IDENTIFIER_LIST'}
# Canonical field -> accepted source column names, in priority order.
ALIASES = {
    'openalex_id': ('openalex_id', 'openalex_ids', 'openalex_work_id', 'openalex_work_ids', 'openalex', 'work_id', 'id'),
    'doi': ('doi', 'dois', 'normalized_doi', 'doi_url'),
    'title': ('title', 'titles', 'display_name'),
    'publication_year': ('publication_year', 'year'),
    'best_oa_pdf_url': ('best_oa_pdf_url', 'pdf_url', 'url_for_pdf'),
    'oa_url': ('oa_url',),
    'best_oa_landing_page': ('best_oa_landing_page', 'landing_page_url', 'landing_page'),
    'is_open_access': ('is_open_access', 'is_oa'),
    'oa_status': ('oa_status',),
}
URL_FIELDS = (('best_oa_pdf_url', 'DIRECT_PDF'), ('oa_url', 'OA_URL'), ('best_oa_landing_page', 'LANDING_PAGE'))
HISTORICAL_PREFIXES = ('main_', 'scimago_', 'venue_', 'admission', 'admitted', 'tier')
HISTORICAL_NAMES = ('source_status', 'main_corpus', 'in_main_corpus')
CREDENTIAL = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|authorization|password|passwd|client[_-]?secret|cookie)[\"']?\s*[=:]\s*\S"
                        r'|\bbearer\s+[A-Za-z0-9._~+/-]{8,}')


def header_key(name):
    return re.sub(r'[\s\-]+', '_', str(name).replace('﻿', '').strip().lower())


def _text(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'True' if value else 'False'
    return str(value).strip()


def _values(value):
    """A cell may hold one value, a JSON array (Reprovane corpus CSV) or a list (JSONL)."""
    if isinstance(value, list):
        return [_text(v) for v in value if _text(v)]
    text = _text(value)
    if text.startswith('[') and text.endswith(']'):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [_text(v) for v in parsed if _text(v)]
        except ValueError:
            pass
    return [text] if text else []


def _oa_flag(present, value):
    if not present:
        return 'ABSENT'
    text = _text(value).lower()
    if text in ('true', '1', 'yes'):
        return 'TRUE'
    if text in ('false', '0', 'no'):
        return 'FALSE'
    return 'ABSENT'


def _year(value):
    text = _text(value)
    if re.fullmatch(r'-?\d{1,4}', text):
        return int(text)
    if re.fullmatch(r'\d{4}\.0', text):
        return int(text[:4])
    return None


def url_route(evidence_id, field, url_type, url, oa_flag):
    """Route for a URL taken from an input row (provider version is unknown for flat rows)."""
    normalized = normalize_url(url)
    safe = sanitize_url(url)
    return dict(evidence_provider='INPUT_ROW', evidence_id=evidence_id, source_record=field, linkage=evidence_id,
                url=safe, url_identity_sha256=digest(normalized), url_type=url_type,
                runtime_url_required=safe != normalized, host_type='unknown', version='UNKNOWN',
                is_oa=oa_flag == 'TRUE', oa_evidence=oa_flag, license=None, display_name=None,
                is_best=field == 'best_oa_pdf_url')


def _relabel(routes, provider):
    out = []
    for route in routes:
        route = dict(route)
        if route['evidence_provider'] == 'OPENALEX':
            route['evidence_provider'] = provider
        out.append(route)
    return out


def detect_format(name, headers, rows):
    keys = set(headers)
    if any(isinstance(r.get('id'), str) and 'openalex.org/W' in r.get('id', '') and
           any(k in r for k in ('locations', 'best_oa_location', 'primary_location', 'open_access')) for r in rows[:5]):
        return 'openalex-works'
    if {'corpus_item_id', 'publication_entity_id'} <= keys:
        return 'reprovane-corpus'
    if {'work_id', 'paper', 'status'} <= keys or {'work_id', 'paper', 'reason'} <= keys:
        return 'reprovane-pdf-export'
    if 'openalex_id' in keys and ({'best_oa_pdf_url', 'oa_url'} & keys):
        return 'legacy-v4'
    return 'generic'


def field_mapping(headers):
    mapping = {}
    for canonical, names in ALIASES.items():
        for name in names:
            if name in headers:
                mapping[canonical] = name
                break
    return mapping


def read_table(name, body):
    """Parse CSV/TSV/JSONL/JSON bytes into dict rows with normalized header names."""
    if len(body) > MAX_INPUT_BYTES:
        raise ValidationError('Input file exceeds the finite byte limit')
    text = body.decode('utf-8-sig')
    suffix = Path(name).suffix.lower()
    rows = []
    if suffix in ('.jsonl', '.ndjson'):
        for line in text.splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValidationError('JSONL rows must be objects')
                rows.append(value)
    elif suffix == '.json':
        value = json.loads(text)
        value = value.get('results', value.get('items')) if isinstance(value, dict) else value
        if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
            raise ValidationError('JSON input must be a list of objects or an object with results/items')
        rows = value
    elif suffix in ('.csv', '.tsv'):
        reader = csv.DictReader(io.StringIO(text, newline=''), delimiter='\t' if suffix == '.tsv' else ',')
        rows = [dict(r) for r in reader]
    else:
        raise ValidationError('Unsupported input file type; use .csv, .tsv, .jsonl or .json')
    if len(rows) > MAX_ROWS:
        raise ValidationError('Input row count exceeds the finite limit')
    normalized = []
    for row in rows:
        out = {}
        for k, v in row.items():
            if k is None:
                continue
            out.setdefault(header_key(k), v)
        normalized.append(out)
    headers = []
    for row in normalized:
        for k in row:
            if k not in headers:
                headers.append(k)
    return headers, normalized


def secret_like(body, private=()):
    """Raw input bytes are not archived when they contain credential-like values or signed URLs."""
    text = body.decode('utf-8', errors='replace')
    if CREDENTIAL.search(text) or any(v and v in text for v in private):
        return True
    for url in re.findall(r"https?://[^\s\"'<>,]+", text):
        try:
            if sanitize_url(url) != normalize_url(url):
                return True
        except ValidationError:
            continue
    return False


def flat_record(row, mapping, fmt, file_family):
    """Normalize one tabular row; returns (status, reason, record)."""
    def raw(field):
        column = mapping.get(field)
        return row.get(column) if column else None
    warnings = []
    openalex_values, doi_values = _values(raw('openalex_id')), _values(raw('doi'))
    works = sorted({w for w in (ids.openalex_work(v) for v in openalex_values) if w})
    dois = sorted({d for d in (ids.doi(v) for v in doi_values) if d})
    if openalex_values and len(works) != len(openalex_values) and mapping.get('openalex_id') != 'id':
        warnings.append('INVALID_OPENALEX_ID_IGNORED')
    if doi_values and len(dois) != len(set(doi_values)):
        warnings.append('INVALID_DOI_IGNORED')
    titles = _values(raw('title'))
    year = _year(raw('publication_year'))
    flag = _oa_flag('is_open_access' in mapping, raw('is_open_access'))
    record_ref = 'row'
    routes, original_urls = [], {}
    for field, url_type in URL_FIELDS:
        value = _text(raw(field))
        if not value:
            continue
        try:
            routes.append(url_route(record_ref, field, url_type, value, flag))
            original_urls[field] = sanitize_url(value)
        except ValidationError:
            warnings.append('UNSAFE_OR_INVALID_URL:' + field)
            original_urls[field] = 'UNSAFE_URL'
    legacy_original = {
        'openalex_id': _text(raw('openalex_id')) if mapping.get('openalex_id') == 'openalex_id' else (works[0] if works else ''),
        'doi': _text(raw('doi')) if mapping.get('doi') == 'doi' and len(doi_values) <= 1 else (dois[0] if dois else ''),
        'title': titles[0] if titles else '', 'publication_year': _text(raw('publication_year')),
        'best_oa_pdf_url': _text(raw('best_oa_pdf_url')), 'oa_url': _text(raw('oa_url'))}
    key = ids.legacy_key(legacy_original)
    # Trusted for automatic file matching only when the same key follows from the validated identifiers alone
    # (or, without any identifier, from the content fields 4.1.0 hashed). A key copied from an invalid identifier
    # value, whatever its shape, is kept for display and legacy replay but never trusted.
    trusted = bool(key) and key == ids.legacy_key(dict(legacy_original, openalex_id=works[0] if works else '',
                                                        doi=dois[0] if dois else ''))
    historical = {k: _text(v) for k, v in sorted(row.items())
                  if k.startswith(HISTORICAL_PREFIXES) or k in HISTORICAL_NAMES}
    record = dict(schema='pdf-input-record/1', format=FORMAT_LABELS[fmt], original_row_sha256=digest(row),
                  openalex_ids=works, dois=dois, titles=titles[:5], publication_year=year, oa_flag=flag,
                  oa_status=_text(raw('oa_status')) or None, legacy_key=key or None, legacy_key_trusted=trusted,
                  urls=original_urls,
                  routes=routes, warnings=sorted(set(warnings)), work_json_present=False,
                  historical=dict(file_family=file_family, screening_authority='NONE',
                                  machine_classification=historical) if (historical or file_family.startswith('LEGACY_')) else None)
    if not works and not dois and not (titles or original_urls):
        # A malformed DOI alone is not an identity; such a row cannot be acquired or matched safely.
        return 'REJECTED', 'NO_IDENTIFIER_OR_CONTENT', record
    return 'ACCEPTED', None, record


# A Reprovane PDF export row (pdf_items.jsonl, downloaded.csv, manual_download.csv) re-imported as input.
# The opaque work_id of the earlier run is never read as an OpenAlex ID.
EXPORT_MAPPING = dict(openalex_id='openalex_ids|openalex_id', doi='dois|doi', title='titles|title',
                      publication_year='publication_years|year', best_oa_pdf_url='source_url|pdf_url|candidates[0].url',
                      oa_url='candidates[1:].url', best_oa_landing_page='landing_page|landing_pages[0]')


def export_record(row):
    def first(*names):
        for name in names:
            value = row.get(name)
            if value not in (None, '', []):
                return value
        return ''
    def many(value):
        # JSONL keeps lists; the CSV exports join several values with '; '.
        if isinstance(value, list):
            return [_text(v) for v in value if _text(v)]
        return [v.strip() for v in _text(value).split(';') if v.strip()]
    years = many(first('publication_years', 'year'))
    # pdf_items.jsonl carries every planned URL with its identity hash, type, runtime requirement and OA flag;
    # the CSV views carry one URL each and no OA flag.
    planned = [c for c in (row.get('candidate_urls') if isinstance(row.get('candidate_urls'), list) else [])
               if isinstance(c, dict) and isinstance(c.get('url'), str) and c['url']]
    if not planned and isinstance(row.get('candidates'), list):
        # Earlier pdf_items.jsonl rows list only the attempted candidates, without identity or OA metadata.
        planned = [dict(url=c['url'], url_type='OA_URL') for c in row['candidates']
                   if isinstance(c, dict) and isinstance(c.get('url'), str) and c['url']]
    landing = first('landing_page') or next(iter(row.get('landing_pages') or []), '')
    flat = dict(openalex_ids=many(first('openalex_ids', 'openalex_id')), dois=many(first('dois', 'doi')),
                title=first('titles', 'title'), publication_year=years[0] if years else '',
                best_oa_pdf_url='' if planned else first('source_url', 'pdf_url'),
                best_oa_landing_page=landing if isinstance(landing, str) else '')
    mapping = dict(openalex_id='openalex_ids', doi='dois', title='title', publication_year='publication_year',
                   best_oa_pdf_url='best_oa_pdf_url', best_oa_landing_page='best_oa_landing_page')
    status, reason, record = flat_record(flat, mapping, 'reprovane-pdf-export', 'NOT_LEGACY')
    # The planner groups routes of one URL into one candidate; here each (URL, role) pair is kept.
    known = {(r['url_identity_sha256'], r['url_type']) for r in record['routes']}
    for index, c in enumerate(planned):
        kind = c.get('url_type') if c.get('url_type') in ('DIRECT_PDF', 'OA_URL', 'LANDING_PAGE') else 'OA_URL'
        flag = c.get('oa_evidence') if c.get('oa_evidence') in ('TRUE', 'FALSE', 'ABSENT') else 'ABSENT'
        try:
            route = url_route('row', f'candidate_urls/{index}', kind, c['url'], flag)
        except ValidationError:
            record['warnings'] = sorted(set(record['warnings']) | {'UNSAFE_OR_INVALID_URL:candidate_urls'})
            continue
        exported = c.get('url_identity_sha256')
        if c.get('runtime_url_required') is True and isinstance(exported, str) and re.fullmatch(r'[0-9a-f]{64}', exported):
            # Keep the identity of the original (signed) URL so --runtime-urls-from can supply it again.
            route.update(url_identity_sha256=exported, runtime_url_required=True)
        if (route['url_identity_sha256'], route['url_type']) not in known:
            known.add((route['url_identity_sha256'], route['url_type']))
            record['routes'].append(route)
    for route in record['routes']:
        if 'REDACTED' in route['url'].partition('?')[2]:
            # A sanitized placeholder is never a runnable URL: the exact original must be supplied at run time.
            route['runtime_url_required'] = True
    if status == 'REJECTED' and reason == 'NO_IDENTIFIER_OR_CONTENT' and record['routes']:
        # The URLs were restored after the flat classification; a row with a URL was acceptable input.
        status, reason = 'ACCEPTED', None
    return status, reason, record


def work_record(work, fmt_label, provider, anchor):
    """Normalize one OpenAlex Work object (saved acquisition page or JSONL work)."""
    oid = ids.openalex_work(work.get('id') or '')
    doi = ids.doi(work.get('doi') or '')
    title = _text(work.get('title') or work.get('display_name'))
    best = work.get('best_oa_location') or {}
    open_access = work.get('open_access') or {}
    flag = 'TRUE' if open_access.get('is_oa') is True else 'FALSE' if open_access.get('is_oa') is False else 'ABSENT'
    routes = []
    if oid:
        routes = _relabel(location_routes(work, 'OPENALEX', 'https://openalex.org/' + oid, 'record'), provider)
        routes = [dict(r, oa_evidence='TRUE' if r['is_oa'] else 'FALSE', evidence_id='record') for r in routes]
    oa_url = _text(open_access.get('oa_url'))
    if oa_url:
        try:
            route = dict(url_route('record', 'oa_url', 'OA_URL', oa_url, flag), evidence_provider=provider)
            # A Work's oa_url that OpenAlex also reports as a landing page is never treated as a download.
            if route['url_identity_sha256'] not in {r['url_identity_sha256'] for r in routes}:
                routes.append(route)
        except ValidationError:
            pass
    legacy_original = dict(openalex_id=oid or '', doi=doi or '', title=title,
                           publication_year=_text(work.get('publication_year')),
                           best_oa_pdf_url=_text(best.get('pdf_url')), oa_url=oa_url)
    record = dict(schema='pdf-input-record/1', format=fmt_label, original_row_sha256=digest(work), anchor=anchor,
                  openalex_ids=[oid] if oid else [], dois=[doi] if doi else [], titles=[title] if title else [],
                  publication_year=_year(work.get('publication_year')), oa_flag=flag,
                  oa_status=_text(open_access.get('oa_status')) or None,
                  legacy_key=ids.legacy_key(legacy_original) or None, legacy_key_trusted=True, urls={}, routes=routes,
                  warnings=[],
                  work_json_present=True, historical=None)
    if not oid:
        return 'REJECTED', 'INVALID_OPENALEX_WORK', record
    return 'ACCEPTED', None, record


def parse_file(path, fmt='auto'):
    """Read one input file. Returns the batch description and normalized (status, reason, record) rows."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValidationError('Input must be an ordinary file')
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValidationError('Input file exceeds the finite byte limit')
    body = path.read_bytes()
    return parse_bytes(path.name, body, fmt)


def parse_bytes(name, body, fmt='auto'):
    if fmt not in FORMATS:
        raise ValidationError('Unknown input format')
    if fmt == 'identifiers' or (fmt == 'auto' and Path(name).suffix.lower() in ('.txt', '.lst')):
        values = [line.strip() for line in body.decode('utf-8-sig').splitlines()]
        batch, rows = parse_identifiers([v for v in values if v and not v.startswith('#')])
        batch.update(source_kind='FILE', source_label=name, source_sha256=sha(body))
        return batch, rows, body
    try:
        headers, table = read_table(name, body)
    except (ValueError, UnicodeError, csv.Error):
        raise ValidationError('Input file could not be parsed as UTF-8 CSV/TSV/JSON/JSONL') from None
    chosen = detect_format(name, headers, table) if fmt == 'auto' else fmt
    file_family = legacy_family(name) if chosen == 'legacy-v4' or name.endswith(('_main.csv', '_excluded.csv')) else 'NOT_LEGACY'
    rows = []
    if chosen == 'openalex-works':
        mapping = {'openalex_id': 'id', 'doi': 'doi', 'title': 'title', 'locations': 'locations'}
        for ordinal, row in enumerate(table):
            rows.append(work_record(row, FORMAT_LABELS[chosen], 'OPENALEX_RECORD', dict(row=ordinal)))
    elif chosen == 'reprovane-pdf-export':
        mapping = dict(EXPORT_MAPPING, adapter='pdf-export-row/1')
        for row in table:
            rows.append(export_record(row))
    else:
        mapping = field_mapping(headers)
        for row in table:
            rows.append(flat_record(row, mapping, chosen, file_family))
    batch = dict(source_kind='FILE', source_format=FORMAT_LABELS[chosen], source_label=name, source_sha256=sha(body),
                 detail=dict(field_mapping=mapping, headers=headers[:200], requested_format=fmt, file_family=file_family,
                             screening_authority='NONE',
                             interpretation='Acquisition evidence only; historical classifications are not screening decisions.'))
    return batch, rows, body


def parse_identifiers(values):
    """DOI / OpenAlex Work ID list (one per line or repeated CLI values)."""
    rows = []
    for value in values:
        work, doi = ids.openalex_work(value), ids.doi(value)
        legacy = ids.legacy_key(dict(openalex_id=work or '', doi=doi or ''))
        record = dict(schema='pdf-input-record/1', format='IDENTIFIER_LIST', original_row_sha256=digest(value),
                      openalex_ids=[work] if work else [], dois=[doi] if doi else [], titles=[],
                      publication_year=None, oa_flag='ABSENT', oa_status=None, legacy_key=legacy or None,
                      legacy_key_trusted=True, urls={},
                      routes=[], warnings=[], work_json_present=False, historical=None)
        rows.append(('ACCEPTED', None, record) if (work or doi) else ('REJECTED', 'INVALID_IDENTIFIER', record))
    canonical = [str(v) for v in values]
    batch = dict(source_kind='IDENTIFIERS', source_format='IDENTIFIER_LIST', source_label='identifier-list',
                 source_sha256=digest(canonical), detail=dict(identifiers=canonical, screening_authority='NONE'))
    return batch, rows


def handoff_records(handoff):
    """Sealed corpus members (read-only Stage11 handoff). Their frozen OA locations stay FROZEN evidence."""
    rows = []
    for item in handoff['items']:
        works = sorted({w for w in (ids.openalex_work(v) for v in item['openalex_work_ids']) if w})
        dois = sorted({d for d in (ids.doi(v) for v in item['dois']) if d})
        routes = []
        for record in (item.get('known_oa_locations') or {}).get('records', []):
            for r in location_routes(record['fields'], 'FROZEN', record['provider_record_id'], 'record'):
                routes.append(dict(r, oa_evidence='TRUE' if r['is_oa'] else 'FALSE'))
        legacy = ids.legacy_key(dict(openalex_id=works[0] if works else '', doi=dois[0] if dois else ''))
        record = dict(schema='pdf-input-record/1', format='SEALED_CORPUS_HANDOFF', original_row_sha256=item['item_sha256'],
                      corpus_item_id=item['corpus_item_id'], openalex_ids=works, dois=dois, titles=item['titles'][:5],
                      publication_year=None, oa_flag='ABSENT', oa_status=None, legacy_key=legacy or None,
                      legacy_key_trusted=True, urls={},
                      routes=routes, warnings=[], work_json_present=False, historical=None)
        rows.append(('ACCEPTED', None, record) if (works or dois) else ('REJECTED', 'NO_IDENTIFIER_OR_CONTENT', record))
    batch = dict(source_kind='CORPUS_RELEASE', source_format='SEALED_CORPUS_HANDOFF', source_label=handoff['release_id'],
                 source_sha256=handoff['release_manifest_sha256'],
                 detail=dict(release_id=handoff['release_id'], handoff_sha256=digest(handoff),
                             interpretation='Corpus membership is unchanged by any acquisition outcome.'))
    return batch, rows


def acquisition_records(acquisition, works, label='OPENALEX_ACQUISITION'):
    """Works replayed from a COMPLETE saved OpenAlex acquisition, in deterministic ID order."""
    rows = []
    for oid in sorted(works):
        value = works[oid]
        anchor = dict(acquisition_id=acquisition['acquisition_id'], **value['anchors'][0])
        rows.append(work_record(value['work'], 'OPENALEX_ACQUISITION_WORK', 'OPENALEX_RECORD', anchor))
    batch = dict(source_kind=label, source_format='OPENALEX_ACQUISITION_WORK', source_label=acquisition['acquisition_id'],
                 source_sha256=acquisition['request_spec_sha256'],
                 detail=dict(acquisition_id=acquisition['acquisition_id'], snapshot_id=acquisition['snapshot_id'],
                             work_count=len(works)))
    return batch, rows
