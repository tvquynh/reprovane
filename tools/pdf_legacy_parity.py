"""Offline functional-parity benchmark: v4.1.0 `fetch-oa` versus unified PDF acquisition.

Both implementations receive the same CSV and the same in-memory web (no socket is opened). The legacy
code is the accepted v4.1.0 algorithm: by default the in-repository copy `reprovane.fulltext` (identical
to litintel/fulltext.py except the User-Agent string), or the real predecessor checkout with --legacy-root.

Every per-paper difference must appear in INTENTIONAL with its policy reason; anything else is reported
as a regression. Results describe synthetic fixtures only and say nothing about real-world success rates.

    python tools/pdf_legacy_parity.py [--legacy-root PATH] [--json OUT.json]
"""
import argparse
import contextlib
import csv
import hashlib
import importlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import requests  # noqa: E402
from requests.structures import CaseInsensitiveDict  # noqa: E402

RealSession = requests.Session

from fulltext_acquisition_fixtures import PDF, PDF2, Response  # noqa: E402

A, B, C, D = 'https://a.example.org', 'https://b.example.org', 'https://c.example.org', 'https://d.example.org'
PRIVATE = 'https://internal.example.org'
JUNK_PDF = b'\r\n' + PDF
ROUTES = {
    A + '/p1.pdf': (200, {'Content-Type': 'application/pdf'}, PDF),
    A + '/p5.pdf': (200, {'Content-Type': 'application/octet-stream'}, PDF2),
    A + '/landing.html': (200, {'Content-Type': 'text/html'}, b'<html><title>Article</title>' + b' ' * 30000 + b'</html>'),
    A + '/to-b': (302, {'Location': B + '/b2.pdf'}, b''),
    A + '/closed.pdf': (200, {}, PDF),
    A + '/junk.pdf': (200, {'Content-Type': 'application/pdf'}, JUNK_PDF),
    A + '/cut.pdf': (200, {'Content-Type': 'application/pdf', 'Content-Length': str(len(PDF) + 99)}, PDF),
    A + '/huge.pdf': (200, {'Content-Type': 'application/pdf', 'Content-Length': str(300 * 1024 * 1024)}, b'%PDF-1.4\n'),
    A + '/to-private': (302, {'Location': PRIVATE + '/x.pdf'}, b''),
    PRIVATE + '/x.pdf': (200, {'Content-Type': 'application/pdf'}, PDF),
    B + '/b1.pdf': (200, {'Content-Type': 'text/html'}, b'<html><head><title>Just a moment...</title></head></html>'),
    B + '/b2.pdf': (200, {}, PDF),
    B + '/b3.pdf': (200, {}, PDF),
    C + '/robots.txt': (200, {'Content-Type': 'text/plain'}, b'User-agent: *\nAllow: /\nDisallow: /private/\n'),
    C + '/private/c1.pdf': (200, {}, PDF),
    C + '/c2.pdf': (429, {'Retry-After': '3600'}, b'slow down'),
    D + '/robots.txt': (503, {}, b'unavailable'),
    D + '/d1.pdf': (200, {}, PDF),
}


def _blank_pdf():
    from pypdf import PdfWriter
    writer, out = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({'/Title': 'Synthetic manual copy'})
    writer.write(out)
    return out.getvalue()


MANUAL = {'W8': _blank_pdf()}   # a closed-access paper the reviewer downloaded by hand into pdf_manual/


def corpus():
    def r(pid, pdf='', oa='', is_oa=True):
        return dict(openalex_id=pid, doi='10.9/' + pid.lower(), title='Paper ' + pid, publication_year=2024, source='Alpha',
                    authors='Doe, J.', is_open_access=is_oa, oa_status='gold' if is_oa else 'closed', best_oa_pdf_url=pdf,
                    oa_url=oa, best_oa_landing_page='')
    return [r('W1', A + '/p1.pdf'), r('W2', B + '/b1.pdf'), r('W3', B + '/b3.pdf'), r('W4', A + '/to-b'),
            r('W5', A + '/landing.html', A + '/p5.pdf'), r('W6', C + '/private/c1.pdf'), r('W7', C + '/c2.pdf'),
            r('W8', is_oa=False), r('W9', A + '/gone.pdf'), r('W10', A + '/closed.pdf', is_oa=False), r('W1', A + '/p1.pdf'),
            r('W11', A + '/junk.pdf'), r('W12', A + '/cut.pdf'), r('W13', A + '/huge.pdf'), r('W14', A + '/to-private'),
            r('W15', D + '/d1.pdf'), r('W16', 'https://e.example.org:8443/x.pdf'),
            dict(openalex_id='', doi='', title='', best_oa_pdf_url='', oa_url='')]


# Equivalent status names (legacy -> unified) and every accepted policy difference.
EQUIVALENT = {'DOWNLOADED': 'DOWNLOADED', 'MANUAL_DOWNLOADED': 'MANUAL_IMPORTED'}
INTENTIONAL = {
    'W11': ('DOWNLOADED', 'PDF_VALIDATION_FAILED', 'Strict structural validation: the PDF header must start the file and '
                                                   'pypdf must open at least one page (v4.1.0 accepted junk before %PDF-).'),
    'W14': ('DOWNLOADED', 'UNSAFE_URL', 'SSRF guard: redirects to private, loopback, link-local or metadata addresses are refused.'),
    'W16': ('ROBOTS_UNREACHABLE', 'UNSAFE_OR_INVALID_URL', 'URLs with a non-default port are refused at import (shared '
                                                           'Stage11B URL rule) instead of being contacted.'),
}


class FakeAdapter(requests.adapters.BaseAdapter):
    def __init__(self, routes, calls):
        super().__init__()
        self.routes, self.calls = routes, calls

    def send(self, request, **kwargs):
        self.calls.append(request.url)
        status, headers, body = self.routes.get(request.url, (404, {'Content-Type': 'text/plain'}, b'not found'))
        if request.url.endswith('/robots.txt') and request.url not in self.routes:
            status, headers, body = 200, {'Content-Type': 'text/plain'}, b'User-agent: *\nAllow: /\n'
        if 'e.example.org:8443' in request.url:
            raise requests.ConnectionError('synthetic refused port')
        response = requests.Response()
        response.status_code, response.url, response.request = status, request.url, request
        response.headers = CaseInsensitiveDict(dict(headers, **({} if 'Content-Length' in headers else {'Content-Length': str(len(body))})))
        response.raw = io.BytesIO(body)
        return response

    def close(self):
        pass


def legacy_module(legacy_root=None):
    if legacy_root:
        sys.path.insert(0, str(Path(legacy_root).resolve()))
        module = importlib.import_module('litintel.fulltext')
        sys.path.pop(0)
        return module, 'predecessor checkout ' + getattr(importlib.import_module('litintel'), '__version__', '?')
    return importlib.import_module('reprovane.fulltext'), 'reprovane.fulltext (v4.1.0 algorithm, Reprovane User-Agent)'


def run_legacy(module, csv_path, out_dir, limit=None):
    calls = []

    def session():
        s = RealSession()
        adapter = FakeAdapter(ROUTES, calls)
        s.mount('http://', adapter)
        s.mount('https://', adapter)
        return s
    with patch.object(module.requests, 'Session', session), patch.object(module.time, 'sleep', lambda *_: None), \
            contextlib.redirect_stdout(io.StringIO()):
        summary = module.fetch_open_access(csv_path, out_dir, delay=0, limit=limit, verbose=False)
    state = json.loads((out_dir / 'fetch_state.json').read_text(encoding='utf-8'))['papers']
    return summary, state, calls


def run_unified(csv_path, workspace, manual_dir, limit_first=None):
    from fulltext_acquisition_fixtures import Response as FakeResponse
    from reprovane.core.db import Database
    from reprovane.pdf_acquisition import PdfAcquisitionStore
    from reprovane.pdf_acquisition.engine import Downloader, make_transport
    from reprovane.pdf_acquisition.manual import manual_import
    calls = []

    def opener(url, address, headers, timeout):
        calls.append(url)
        status, given, body = ROUTES.get(url, (404, {'Content-Type': 'text/plain'}, b'not found'))
        if url.endswith('/robots.txt') and url not in ROUTES:
            status, given, body = 200, {}, b'User-agent: *\nAllow: /\n'
        return FakeResponse(body, status, dict(given, **({} if 'Content-Length' in given else {'Content-Length': str(len(body))})))

    def dns(host, *args, **kwargs):
        return [(2, 1, 6, '', ('10.0.0.7' if host == 'internal.example.org' else '93.184.216.34', 443))]
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds
    with Database(workspace) as db:
        store = PdfAcquisitionStore(db)
        rid = store.create_run(label='parity')['run_id']
        batch = store.import_file(rid, csv_path)
        store.discover(rid)
        transport = make_transport(store.policy(rid), resolver=dns, opener=opener, sleeper=sleep)
        engine = lambda: Downloader(store, rid, transport, clock=lambda: clock[0], sleep=sleep)  # noqa: E731
        if limit_first:
            engine().run(limit=limit_first)
        engine().run()
        manual = manual_import(store, run_id=rid, path=manual_dir) if manual_dir else None
        statuses = {s['paper']: s for s in store.statuses(rid)}
        verified = store.verify(rid)['verified']
        identity = store.inspect(rid)['identity']
        candidates = store.candidates(rid)
        artifacts = {s['paper']: s['primary_artifact_sha256'] for s in statuses.values() if s['primary_artifact_sha256']}
        return dict(run_id=rid, batch=batch, statuses=statuses, verified=verified, identity=identity, candidates=candidates,
                    artifacts=artifacts, manual=manual, calls=calls)


def legacy_import_check(legacy_out, csv_path, workspace):
    from reprovane.core.db import Database
    from reprovane.pdf_acquisition import PdfAcquisitionStore
    from reprovane.pdf_acquisition.legacy import import_legacy_fulltext
    with Database(workspace) as db:
        store = PdfAcquisitionStore(db)
        rid = store.create_run(label='legacy import')['run_id']
        store.import_file(rid, csv_path)
        store.discover(rid)
        before = {p.relative_to(legacy_out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy_out.rglob('*') if p.is_file()}
        result = import_legacy_fulltext(store, rid, legacy_out)
        after = {p.relative_to(legacy_out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy_out.rglob('*') if p.is_file()}
        statuses = {s['paper']: s['status'] for s in store.statuses(rid)}
        return dict(counts=result['counts'], blocked_hosts_imported=result['blocked_hosts_imported'], statuses=statuses,
                    legacy_folder_unchanged=before == after, verified=store.verify(rid)['verified'])


def run_parity(legacy_root=None):
    module, legacy_label = legacy_module(legacy_root)
    with tempfile.TemporaryDirectory(prefix='reprovane-parity-') as folder:
        base = Path(folder) / 'parity có dấu'
        base.mkdir()
        csv_path = base / 'x_main.csv'
        rows = corpus()
        with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        legacy_out = base / 'legacy_fulltext'
        (legacy_out / 'pdf_manual').mkdir(parents=True)
        for key, body in MANUAL.items():
            (legacy_out / 'pdf_manual' / (key + '.pdf')).write_bytes(body)
        manual_dir = base / 'manual_for_unified'
        manual_dir.mkdir()
        for key, body in MANUAL.items():
            (manual_dir / (key + '.pdf')).write_bytes(body)
        summary, state, legacy_calls = run_legacy(module, csv_path, legacy_out)
        unified = run_unified(csv_path, base / 'ws single', manual_dir)
        # Resume correctness: an interrupted first pass (limit) followed by a resume ends in the same states.
        resumed = run_unified(csv_path, base / 'ws resumed', manual_dir, limit_first=3)
        legacy_resume_out = base / 'legacy_resume'
        (legacy_resume_out / 'pdf_manual').mkdir(parents=True)
        for key, body in MANUAL.items():
            (legacy_resume_out / 'pdf_manual' / (key + '.pdf')).write_bytes(body)
        run_legacy(module, csv_path, legacy_resume_out, limit=3)
        _, legacy_resumed_state, _ = run_legacy(module, csv_path, legacy_resume_out)
        imported = legacy_import_check(legacy_out, csv_path, base / 'ws legacy import')

        papers = sorted(state, key=lambda k: int(k[1:]) if k[1:].isdigit() else 10 ** 6)
        per_paper, regressions, intentional = [], [], []
        for key in papers:
            old = state[key]['status']
            new = unified['statuses'][key]['status'] if key in unified['statuses'] else 'MISSING'
            same = EQUIVALENT.get(old, old) == new
            entry = dict(paper=key, legacy=old, unified=new, equivalent=same)
            if not same:
                expected = INTENTIONAL.get(key)
                if expected and expected[:2] == (old, new):
                    entry['classification'] = 'INTENTIONAL_POLICY'
                    entry['reason'] = expected[2]
                    intentional.append(entry)
                else:
                    entry['classification'] = 'REGRESSION'
                    regressions.append(entry)
            per_paper.append(entry)
        for key, (old, new, _) in INTENTIONAL.items():
            if not any(e['paper'] == key and e.get('classification') == 'INTENTIONAL_POLICY' for e in per_paper):
                regressions.append(dict(paper=key, legacy=state.get(key, {}).get('status'),
                                        unified=unified['statuses'].get(key, {}).get('status'), classification='STALE_EXPECTATION'))

        legacy_files = {k: v['sha256'] for k, v in state.items() if v.get('sha256')}
        common = sorted(k for k in legacy_files if k in unified['artifacts'])
        integrity = dict(common_successes=len(common), identical_bytes=all(legacy_files[k] == unified['artifacts'][k] for k in common),
                         unified_db_and_cas_verified=unified['verified'] and resumed['verified'])
        legacy_candidates = sum(len({u for u in (str(r.get('best_oa_pdf_url') or ''), str(r.get('oa_url') or '')) if u})
                                for r in {r['openalex_id']: r for r in rows if r['openalex_id']}.values())
        legacy_eligible = sum(1 for r in {r['openalex_id']: r for r in rows if r['openalex_id']}.values()
                              if r.get('is_open_access') and (r.get('best_oa_pdf_url') or r.get('oa_url')))
        resume_same = {k: s['status'] for k, s in resumed['statuses'].items()} == {k: s['status'] for k, s in unified['statuses'].items()}
        legacy_resume_same = {k: v['status'] for k, v in legacy_resumed_state.items()} == {k: v['status'] for k, v in state.items()}
        metrics = dict(
            accepted_input_records=dict(legacy_papers=summary['papers'], legacy_rows_without_identifier=summary['rows_without_identifier'],
                                        unified_accepted=unified['batch']['counts']['accepted'],
                                        unified_rejected=unified['batch']['counts']['rejected'],
                                        unified_unique_works=unified['identity']['unique_works']),
            candidate_url_coverage=dict(legacy_candidate_urls=legacy_candidates,
                                        unified_candidate_urls=len({c['url_identity_sha256'] for c in unified['candidates']}),
                                        unified_eligible_candidates=sum(c['eligible'] for c in unified['candidates'])),
            auto_download_eligibility=dict(legacy_works_with_attemptable_url=legacy_eligible,
                                           unified_works_with_eligible_candidate=sum(s['eligible_candidate_count'] > 0 for s in unified['statuses'].values())),
            successful_acquisition=dict(legacy_downloaded=summary['downloaded'],
                                        unified_downloaded=sum(s['status'] == 'DOWNLOADED' for s in unified['statuses'].values())),
            manual_ingestion=dict(legacy_manual=summary['manually_downloaded'],
                                  unified_manual=sum(s['status'] == 'MANUAL_IMPORTED' for s in unified['statuses'].values())),
            resume_correctness=dict(unified_limit_then_resume_equals_single_pass=resume_same,
                                    legacy_limit_then_resume_equals_single_pass=legacy_resume_same),
            failure_classification=dict(papers=len(per_paper), equivalent=sum(e['equivalent'] for e in per_paper),
                                        intentional_policy_differences=len(intentional), regressions=len(regressions)),
            artifact_integrity=integrity,
            requests=dict(legacy_http_requests=len(legacy_calls), unified_http_requests=len(unified['calls'])),
            legacy_v4_import=imported)
        return dict(schema='pdf-legacy-parity/1', legacy_implementation=legacy_label, fixture='synthetic in-memory web; no network',
                    metrics=metrics, per_paper=per_paper, intentional=intentional, regressions=regressions,
                    interpretation='Synthetic fixtures only; not an estimate of real-world download success rates.')


def markdown(report):
    lines = ['| Paper | v4.1.0 | Unified | Classification |', '|---|---|---|---|']
    for e in report['per_paper']:
        lines.append(f"| {e['paper']} | {e['legacy']} | {e['unified']} | {e.get('classification', 'EQUIVALENT')} |")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--legacy-root', type=Path)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    report = run_parity(args.legacy_root)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report['metrics'], ensure_ascii=False, indent=2, sort_keys=True))
    print(markdown(report))
    print('REGRESSIONS:', len(report['regressions']))
    return 1 if report['regressions'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
