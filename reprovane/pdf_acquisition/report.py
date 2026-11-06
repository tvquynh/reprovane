"""Semantic manifest, legacy-compatible reports and DB-free export verification.

Exports contain exactly: pdf_manifest.json, pdf_items.jsonl, downloaded.csv, manual_download.csv,
manual_download.html, manual_download.ris, reconciliation.csv, fetch_summary.json and SHA256SUMS.txt.
Every file is rendered deterministically from pdf_manifest.json, so verify-export rebuilds exact bytes.
No timestamps, absolute paths or credentials are written.
"""
import csv
import html
import io
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..core import ArtifactIntegrityError, ValidationError
from ..core.provenance import canonical_json
from ..corpus_release.model import digest, sha
from ..fulltext import host_of
from ..fulltext_acquisition.portable import write_exact
from .manual import reconciliation

FILES = ('SHA256SUMS.txt', 'downloaded.csv', 'fetch_summary.json', 'manual_download.csv', 'manual_download.html',
         'manual_download.ris', 'pdf_items.jsonl', 'pdf_manifest.json', 'reconciliation.csv')
DOWNLOADED_FIELDS = ['paper', 'work_id', 'status', 'source', 'basis', 'sha256', 'bytes', 'artifact_locator', 'openalex_id', 'doi',
                     'title', 'year', 'version_claim', 'verification', 'source_url']
MANUAL_FIELDS = ['paper', 'file_name', 'work_id', 'reason', 'detail', 'host', 'openalex_id', 'doi', 'title', 'year', 'pdf_url',
                 'landing_page']
RECONCILIATION_FIELDS = ['item_id', 'kind', 'state', 'source_name', 'sha256', 'candidates', 'decision', 'target_id']
HAVE_PDF = ('DOWNLOADED', 'MANUAL_IMPORTED', 'LEGACY_V4_DOWNLOADED')


def build_manifest(store, rid, watermark, state=None):
    run, p = store.get(rid), store.policy(rid)
    run = dict(run, state=state or run['state'])
    statuses = store.statuses(rid, watermark=watermark)
    records = store.records(rid)
    rejected = Counter(r['reason'] for r in records if r['status'] == 'REJECTED')
    candidates = store.candidates(rid)
    attempts = store.attempts(rid)
    cmap = {c['candidate_id']: c for c in candidates}
    by_source, by_host = defaultdict(Counter), defaultdict(Counter)
    for c in candidates:
        for provider in sorted({r['evidence_provider'] for r in c['routes']}):
            by_source[provider]['candidates'] += 1
            by_source[provider]['eligible_candidates'] += int(c['eligible'])
    for a in attempts:
        c = cmap[a['candidate_id']]
        host = ((a['result'] or {}).get('chain') or [host_of(c['url'])])[-1]
        outcome = 'acquired' if a['outcome'] == 'ACQUIRED' else 'failed' if a['outcome'] else 'interrupted'
        by_host[host]['attempts'] += 1
        by_host[host][outcome] += 1
        if a['outcome'] == 'ACQUIRED':
            for provider in sorted({r['evidence_provider'] for r in c['routes']}):
                by_source[provider]['acquired'] += 1
    def describe(sha256):
        row = store._one('SELECT byte_size, validation_json FROM pdf_artifact WHERE artifact_sha256=?', (sha256,))
        return dict(artifact_sha256=sha256, byte_size=row['byte_size'], pages=json.loads(row['validation_json'])['pages'],
                    locator='raw/sha256/' + sha256[:2] + '/' + sha256)
    artifacts = {a['artifact_sha256']: describe(a['artifact_sha256']) for s in statuses for a in s['artifacts']}
    # Manual PDFs registered by this run but not linked to a work (reconciliation, conflicts) are still evidence.
    held = {r['artifact_sha256'] for r in store._all(
        "SELECT DISTINCT m.artifact_sha256 FROM pdf_manual_import m JOIN pdf_import_session s ON s.session_id=m.session_id "
        "WHERE s.context_kind='DISCOVERY_RUN' AND s.context_id=? AND m.artifact_sha256 IS NOT NULL", (rid,))} - set(artifacts)
    queue = [s for s in statuses if s['status'] not in HAVE_PDF]
    sessions = store._all("SELECT s.session_id, s.kind, s.session_sha256, count(m.import_id) AS files, "
                          "sum(m.outcome='LINKED') AS linked, sum(m.outcome='REJECTED') AS rejected, "
                          "sum(m.outcome='RECONCILIATION_REQUIRED') AS reconciliation FROM pdf_import_session s "
                          "LEFT JOIN pdf_manual_import m ON m.session_id=s.session_id WHERE s.context_kind='DISCOVERY_RUN' "
                          "AND s.context_id=? GROUP BY s.session_id ORDER BY s.created_at_utc, s.session_id", (rid,))
    hosts = sorted({h for s in statuses for h in s['hosts']} | set(by_host))
    host_states = [store.host_state(h, watermark) for h in hosts]
    identity = json.loads(run['identity_json']) if run['identity_json'] else None
    status_counts = Counter(s['status'] for s in statuses)
    summary = dict(
        input_records=len(records), accepted_records=len(records) - sum(rejected.values()), rejected_records=sum(rejected.values()),
        rejected_reasons=dict(sorted(rejected.items())), unique_works=len(statuses),
        duplicate_records=(identity or {}).get('duplicate_records', 0), identity_issues=len((identity or {}).get('issues', [])),
        candidate_urls=len({c['url_identity_sha256'] for c in candidates}),
        eligible_candidates=sum(c['eligible'] for c in candidates),
        works_with_eligible_candidate=sum(s['eligible_candidate_count'] > 0 for s in statuses),
        downloaded=status_counts['DOWNLOADED'], manually_imported=status_counts['MANUAL_IMPORTED'],
        legacy_v4_downloaded=status_counts['LEGACY_V4_DOWNLOADED'], without_pdf=len(queue),
        status_counts=dict(sorted(status_counts.items())), artifacts=len(artifacts),
        reconciliation_open=sum(r['state'] == 'OPEN' for r in reconciliation(store, run_id=rid)))
    return dict(schema='pdf-acquisition-manifest/1', run_id=rid, label=run['label'], profile=run['profile'],
                run_state_at_manifest=run['state'], policy=p, policy_sha256=run['policy_sha256'],
                identity=identity, host_event_watermark=watermark, summary=summary,
                by_evidence_source={k: dict(sorted(v.items())) for k, v in sorted(by_source.items())},
                by_host={k: dict(sorted(v.items())) for k, v in sorted(by_host.items())},
                host_states=host_states, items=statuses, manual_queue=[s['work_id'] for s in queue],
                artifacts=[artifacts[k] for k in sorted(artifacts)],
                held_manual_artifacts=[describe(k) for k in sorted(held)], import_sessions=sessions,
                reconciliation=reconciliation(store, run_id=rid), budget=store.budget(rid),
                interpretation='Acquisition evidence only. Availability of a PDF never decides study inclusion; '
                               'version claims are provider statements, not verified versions.')


def _csv(fields, rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: canonical_json(v) if isinstance(v, (list, dict)) else ('' if v is None else v) for k, v in row.items()})
    return out.getvalue().encode('utf-8')


def _link(url, label):
    if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
        return ''
    return f"<a href='{html.escape(url)}' rel='noopener noreferrer'>{html.escape(label)}</a>"


def _clean(value):
    return ' '.join(str(value or '').split())


def _bibliographic(s):
    """Input fields first; provider display fields only fill what the input did not supply."""
    shown = s.get('provider_display') or {}
    titles = s['titles'] or shown.get('titles') or ['']
    dois = s['dois'] or shown.get('dois') or []
    years = s['publication_years'] or shown.get('publication_years') or []
    return dict(doi='; '.join(dois), title=_clean(titles[0]), year='; '.join(str(y) for y in years))


def downloaded_rows(m):
    artifacts = {a['artifact_sha256']: a for a in m['artifacts']}
    rows = []
    for s in m['items']:
        if s['status'] not in HAVE_PDF:
            continue
        a = s['artifacts'][0]
        url = next((c['url'] for c in s['candidates'] if c['outcome'] == 'ACQUIRED'), '') if a['source'] == 'AUTO' else ''
        rows.append(dict(paper=s['paper'], work_id=s['work_id'], status=s['status'], source=a['source'], basis=a['basis'],
                         sha256=a['artifact_sha256'], bytes=artifacts[a['artifact_sha256']]['byte_size'],
                         artifact_locator=artifacts[a['artifact_sha256']]['locator'], openalex_id='; '.join(s['openalex_ids']),
                         **_bibliographic(s), version_claim=a['version_claim'],
                         verification=a['verification'], source_url=url))
    return rows


def manual_rows(m):
    rows = []
    for s in m['items']:
        if s['status'] in HAVE_PDF:
            continue
        detail = ' | '.join([f"{c['outcome']}: {c['host']}" for c in s['candidates']] +
                            [f"4.1.0 {h['status']}" + (f": {h['host']}" if h.get('host') else '')
                             for h in s.get('legacy_v4_history', ())])
        rows.append(dict(paper=s['paper'], file_name=s['paper'] + '.pdf', work_id=s['work_id'], reason=s['status'], detail=detail,
                         host=(s['hosts'] or [''])[0], openalex_id='; '.join(s['openalex_ids']), **_bibliographic(s),
                         pdf_url=(s['candidates'][0]['url'] if s['candidates'] else ''), landing_page=(s['landing_pages'] or [''])[0]))
    return rows


def manual_html(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['reason']].append(row)
    parts = ["<!doctype html><html><head><meta charset='utf-8'><title>Manual download list</title>",
             "<style>body{font-family:sans-serif;margin:2em;max-width:70em}td,th{border-bottom:1px solid #ddd;padding:4px 8px;"
             "vertical-align:top;text-align:left}table{border-collapse:collapse}code{background:#f3f3f3}</style></head><body>",
             f'<h1>Manual download list ({len(rows)} works)</h1>',
             '<p>Save each PDF under the given file name in one folder, then run <code>reprovane pdf manual-import</code> '
             'with that folder. Files are linked only by exact identifiers; anything else waits for your reconciliation decision.</p>']
    for reason in sorted(groups):
        group = sorted(groups[reason], key=lambda r: (r['host'], r['title'], r['work_id']))
        parts.append(f"<h2>{html.escape(reason)} ({len(group)})</h2><table><tr><th>File name</th><th>Paper</th><th>Links</th>"
                     "<th>Detail</th></tr>")
        for row in group:
            dois = [d for d in row['doi'].split('; ') if d]
            links = [x for x in (_link('https://doi.org/' + dois[0], 'DOI') if dois else '', _link(row['landing_page'], 'landing page'),
                                 _link(row['pdf_url'], 'PDF')) if x]
            parts.append(f"<tr><td><code>{html.escape(row['file_name'])}</code></td><td>{html.escape(row['title'])}"
                         f"<br><small>{html.escape(row['year'])}</small></td><td>{' &middot; '.join(links)}</td>"
                         f"<td><small>{html.escape(row['detail'])}</small></td></tr>")
        parts.append('</table>')
    parts.append('</body></html>')
    return ('\n'.join(parts) + '\n').encode('utf-8')


def manual_ris(rows):
    lines = []
    for row in rows:
        lines += ['TY  - JOUR', 'TI  - ' + row['title']]
        for tag, value in (('PY', row['year']), ('DO', row['doi'].split('; ')[0] if row['doi'] else '')):
            if value:
                lines.append(f'{tag}  - {value}')
        url = row['landing_page'] or ('https://doi.org/' + row['doi'].split('; ')[0] if row['doi'] else row['pdf_url'])
        if url:
            lines.append('UR  - ' + url)
        lines += [f"N1  - Save as {row['file_name']} ({row['reason']})", 'ER  - ', '']
    return '\n'.join(lines).encode('utf-8')


def render_files(m):
    manual = manual_rows(m)
    summary = dict(schema='pdf-fetch-summary/1', run_id=m['run_id'], profile=m['profile'], run_state=m['run_state_at_manifest'],
                   authoritative=m['run_state_at_manifest'] == 'CLOSED', policy_sha256=m['policy_sha256'],
                   manifest_sha256=digest(m), **m['summary'],
                   blocked_hosts=sorted(h['host'] for h in m['host_states'] if h['blocked']), budget=m['budget'],
                   by_evidence_source=m['by_evidence_source'], by_host=m['by_host'])
    files = {'pdf_manifest.json': (canonical_json(m) + '\n').encode('utf-8'),
             'pdf_items.jsonl': b''.join((canonical_json(s) + '\n').encode('utf-8') for s in m['items']),
             'downloaded.csv': _csv(DOWNLOADED_FIELDS, downloaded_rows(m)),
             'manual_download.csv': _csv(MANUAL_FIELDS, manual),
             'manual_download.html': manual_html(manual),
             'manual_download.ris': manual_ris(manual),
             'reconciliation.csv': _csv(RECONCILIATION_FIELDS, m['reconciliation']),
             'fetch_summary.json': (json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')}
    files['SHA256SUMS.txt'] = ''.join(sha(data) + '  ' + name + '\n' for name, data in sorted(files.items())).encode('utf-8')
    return files


def export(store, rid, directory):
    run = store.get(rid)
    if run['state'] == 'OPEN':
        raise ValidationError('Run discover before exporting')
    store.verify(rid)
    manifest = json.loads(run['manifest_json']) if run['state'] == 'CLOSED' else store.manifest(rid, store.host_watermark())
    write_exact(Path(directory), render_files(manifest))
    return verify_export(directory, expected_manifest_sha256=digest(manifest))


def verify_export(directory, *, expected_manifest_sha256=None):
    root = Path(directory)
    try:
        if root.is_symlink() or sorted(p.name for p in root.iterdir()) != sorted(FILES) or any(p.is_symlink() or not p.is_file() for p in root.iterdir()):
            raise ValueError()
        m = json.loads((root / 'pdf_manifest.json').read_bytes())
        if m.get('schema') != 'pdf-acquisition-manifest/1' or digest(m['policy']) != m['policy_sha256']:
            raise ValueError()
        for s in m['items']:
            if digest({k: v for k, v in s.items() if k != 'status_sha256'}) != s['status_sha256']:
                raise ValueError()
        if [s['work_id'] for s in m['items'] if s['status'] not in HAVE_PDF] != m['manual_queue']:
            raise ValueError()
        if {a['artifact_sha256'] for s in m['items'] for a in s['artifacts']} != {a['artifact_sha256'] for a in m['artifacts']}:
            raise ValueError()
        if expected_manifest_sha256 and digest(m) != expected_manifest_sha256:
            raise ValueError()
        if any((root / name).read_bytes() != body for name, body in render_files(m).items()):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ArtifactIntegrityError('Portable PDF acquisition export verification failed') from None
    return dict(verified=True, mode='PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES', manifest_sha256=digest(m),
                authoritative=m['run_state_at_manifest'] == 'CLOSED', works=len(m['items']), manual_queue=len(m['manual_queue']))


def export_pdfs(store, rid, directory):
    """Convenience copies named like v4.1.0 (<paper>.pdf); CAS stays authoritative."""
    store.verify(rid)
    statuses = store.statuses(rid)
    names = Counter(s['paper'] for s in statuses if s['primary_artifact_sha256'])
    files = {}
    for s in statuses:
        if s['primary_artifact_sha256']:
            name = (s['paper'] if names[s['paper']] == 1 else s['work_id']) + '.pdf'
            files[name] = store.cas.path_for_hash(s['primary_artifact_sha256']).read_bytes()
    write_exact(Path(directory), files)
    return dict(files=len(files), verified=True, naming='LEGACY_PAPER_KEY_OR_WORK_ID')


def _review_politeness(store, ft, fulltext_run_id):
    from .review import verify_review_politeness
    return verify_review_politeness(store, ft, fulltext_run_id)


def review_status(store, fulltext_run_id):
    """Unified operational status for a REVIEW_FULLTEXT (Stage11B) run plus supplementary manual PDFs."""
    from ..fulltext_acquisition import FullTextStore
    ft = FullTextStore(store.db)
    run = ft.get(fulltext_run_id)
    inspect = ft.inspect(fulltext_run_id)
    if run['state'] in ('MATERIALIZED', 'SEALED'):
        items = json.loads(run['manifest_json'])['items']
    elif run['state'] == 'ACQUIRING':
        _, p, inputs_ = ft._context(fulltext_run_id)
        items = ft._statuses(fulltext_run_id, inputs_, p)
    else:
        items = []
    supplements = defaultdict(list)
    for row in store._all('SELECT * FROM pdf_review_supplement WHERE fulltext_run_id=? ORDER BY rowid', (fulltext_run_id,)):
        supplements[row['corpus_item_id']].append(dict(artifact_sha256=row['artifact_sha256'], basis=row['basis'],
                                                       verification=row['verification']))
    rows = []
    for s in items:
        extra = supplements.get(s['corpus_item_id'], [])
        display = s['status'] if s['status'] == 'ACQUIRED_AUTO' else ('MANUAL_SUPPLIED_UNVERIFIED' if extra else s['status'])
        rows.append(dict(corpus_item_id=s['corpus_item_id'], stage11b_status=s['status'], reasons=s['reasons'],
                         selected_artifact_sha256=s['selected_artifact_sha256'], supplementary_manual=extra,
                         operational_status=display))
    counts = Counter(r['operational_status'] for r in rows)
    return dict(profile='REVIEW_FULLTEXT', fulltext_run_id=fulltext_run_id, state=run['state'], run=inspect,
                counts=dict(sorted(counts.items())), items=rows,
                politeness=_review_politeness(store, ft, fulltext_run_id),
                reconciliation=reconciliation(store, fulltext_run_id=fulltext_run_id),
                interpretation='Stage11B statuses and primaries are unchanged; manual PDFs are supplementary and unverified.')
