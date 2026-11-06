"""Synthetic, keyless fixtures for unified PDF acquisition. No socket is ever opened."""
import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fulltext_acquisition_fixtures import PDF, PDF2, Response, public_dns
from reprovane.core.db import Database
from reprovane.pdf_acquisition import PdfAcquisitionStore, default_policy
from reprovane.pdf_acquisition.engine import Downloader, make_transport

ROBOTS_OK = (200, {'Content-Type': 'text/plain'}, b'User-agent: *\nAllow: /\n')
NOT_FOUND = (404, {'Content-Type': 'text/plain'}, b'not found')
PDF_OK = (200, {'Content-Type': 'application/pdf'}, PDF)
PDF2_OK = (200, {'Content-Type': 'application/pdf'}, PDF2)
CHALLENGE = (200, {'Content-Type': 'text/html'}, b'<html><head><title>Just a moment...</title></head><body>wait</body></html>')


class FakeWeb:
    """URL -> responses (status, headers, body) or exceptions; the last entry repeats. Records every request."""

    def __init__(self, routes=None, default_robots=True):
        self.routes, self.calls, self.default_robots = {}, [], default_robots
        for url, value in (routes or {}).items():
            self.add(url, *(value if isinstance(value, list) else [value]))

    def add(self, url, *responses):
        self.routes[url] = list(responses)

    def __call__(self, url, address, headers, timeout):
        self.calls.append(dict(url=url, address=address, headers=dict(headers)))
        entries = self.routes.get(url)
        if entries is None:
            entries = [ROBOTS_OK if url.endswith('/robots.txt') and self.default_robots else NOT_FOUND]
        entry = entries.pop(0) if len(entries) > 1 else entries[0]
        if isinstance(entry, BaseException):
            raise entry
        status, given, body = entry
        headers = dict(given)
        headers.setdefault('Content-Length', str(len(body)))
        if headers['Content-Length'] == 'omit':
            del headers['Content-Length']
        return Response(body, status, headers)

    def requested(self, contains=''):
        return [c['url'] for c in self.calls if not c['url'].endswith('/robots.txt') and contains in c['url']]


def row(pid, pdf='', oa='', is_oa='True', doi=None, title=None, landing=''):
    return dict(openalex_id=pid, doi=('10.9999/' + pid.lower()) if doi is None else doi, title=title or 'Paper ' + pid,
                publication_year='2024', authors='Doe, J.', is_open_access=is_oa, oa_status='gold' if is_oa == 'True' else 'closed',
                best_oa_pdf_url=pdf, oa_url=oa, best_oa_landing_page=landing)


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def policy(**changes):
    value = default_policy('discovery')
    value.update(changes)
    return value


class PdfCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / 'Không gian nghiên cứu' / 'review space'
        self.base.mkdir(parents=True)
        self.ws = self.base / 'workspace'
        self.db = Database(self.ws)
        self.store = PdfAcquisitionStore(self.db)
        self.web = FakeWeb()
        self.sleeps, self.clock = [], [1000.0]
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.resolver = public_dns

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock[0] += seconds

    def make_run(self, rows, p=None, name='x_main.csv', discover=True):
        rid = self.store.create_run(p, label='synthetic')['run_id']
        self.store.import_file(rid, write_csv(self.base / 'inputs' / name, rows))
        if discover:
            self.store.discover(rid)
        return rid

    def transport(self, rid, credentials=None):
        return make_transport(self.store.policy(rid), credentials=credentials, resolver=self.resolver, opener=self.web,
                              sleeper=self.sleep)

    def downloader(self, rid, **kw):
        return Downloader(self.store, rid, self.transport(rid, kw.pop('credentials', None)), clock=lambda: self.clock[0],
                          sleep=self.sleep, now=lambda: self.now, **kw)

    def download(self, rid, **kw):
        return self.downloader(rid).run(**kw)

    def status(self, rid):
        return {s['paper']: s for s in self.store.statuses(rid)}

    def pdf_dir(self, files, name='manual'):
        folder = self.base / name
        folder.mkdir(parents=True, exist_ok=True)
        for rel, body in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(body)
        return folder

    def table_count(self, table):
        return self.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]


def tree_hashes(root):
    import hashlib
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(root).rglob('*') if p.is_file()}


__all__ = ['PDF', 'PDF2', 'ROBOTS_OK', 'NOT_FOUND', 'PDF_OK', 'PDF2_OK', 'CHALLENGE', 'FakeWeb', 'row', 'write_csv', 'policy',
           'PdfCase', 'tree_hashes', 'json']
