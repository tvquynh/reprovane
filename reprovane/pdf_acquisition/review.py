"""Politeness for REVIEW_FULLTEXT (Stage11B) acquisition started with `reprovane pdf review acquire`.

Stage11B keeps its frozen policy, candidate plans, request ledger and sealing rules unchanged. This wrapper only adds
the operational rules of the discovery engine in front of each PDF request: robots.txt (RFC 9309), per-host pacing and
Crawl-delay, Retry-After cooldowns, bot-check detection and the workspace's persistent blocked hosts. A refused request
never reaches the network and ends as an ordinary Stage11B failed attempt with its reason (for example
ROBOTS_DISALLOWED or HOST_BLOCKED), so no corpus membership or primary selection rule changes.

robots.txt requests are recorded in pdf_review_robots_request/result because the hash-verified Stage11B ledger accepts
only PDF and METADATA requests. Each one is reserved before any network I/O and counts, together with the Stage11B
requests, against the run's frozen request and byte budget. `reprovane fulltext acquire` keeps the original Stage11B
behaviour.
"""
import json
import time

from ..core import ArtifactIntegrityError
from ..core.ids import new_id, utc_now
from ..core.provenance import canonical_json
from ..corpus_release.model import digest
from ..fulltext import USER_AGENT_TOKEN, host_of
from ..fulltext_acquisition.safety import guard
from ..fulltext_acquisition.transport import FetchError
from .engine import HostRegister
from .policy import DISCOVERY_DEFAULT_POLICY
from .politeness import Politeness, now_utc

SETTINGS_KEYS = ('per_host_delay_seconds', 'max_crawl_delay_seconds', 'host_failure_threshold', 'block_on_plain_403',
                 'max_cooldown_seconds', 'max_robots_bytes')
CHUNK = 65536  # Stage11B reserves one extra bounded chunk per request for oversize detection; robots requests too.


def politeness_settings(stage11b_policy):
    """Fixed review politeness profile: the discovery defaults plus the run's own frozen Retry-After bound."""
    return dict(schema='pdf-review-politeness/1', robots_agent_token=USER_AGENT_TOKEN,
                max_retry_delay=stage11b_policy['max_retry_delay'],
                **{k: DISCOVERY_DEFAULT_POLICY[k] for k in SETTINGS_KEYS})


def robots_usage(store, fulltext_run_id):
    """Requests and bytes of the review run's robots.txt ledger (unfinished reservations keep their full charge)."""
    row = store.db.execute(
        'SELECT count(*), coalesce(sum(coalesce(r.bytes, q.reserved_bytes)),0) FROM pdf_review_robots_request q '
        'JOIN pdf_review_politeness_session s ON s.session_id=q.session_id '
        'LEFT JOIN pdf_review_robots_result r ON r.request_id=q.request_id WHERE s.fulltext_run_id=?',
        (fulltext_run_id,)).fetchone()
    return dict(http_requests=row[0], bytes=row[1])


def combined_usage(store, full, fulltext_run_id):
    used, robots = full.budget(fulltext_run_id), robots_usage(store, fulltext_run_id)
    return dict(http_requests=used['http_requests'] + robots['http_requests'], bytes=used['bytes'] + robots['bytes'],
                robots_requests=robots['http_requests'], robots_bytes=robots['bytes'])


def verify_review_politeness(store, full, fulltext_run_id):
    """Offline check of the auxiliary ledger: row hashes, reservation bounds and the combined frozen budget."""
    checks = dict(sessions=0, robots_requests=0, robots_results=0)
    for s in store._all('SELECT * FROM pdf_review_politeness_session WHERE fulltext_run_id=?', (fulltext_run_id,)):
        if digest(json.loads(s['settings_json'])) != s['settings_sha256']:
            raise ArtifactIntegrityError('Review politeness session hash mismatch')
        checks['sessions'] += 1
    for q in store._all('SELECT q.* FROM pdf_review_robots_request q JOIN pdf_review_politeness_session s '
                        'ON s.session_id=q.session_id WHERE s.fulltext_run_id=?', (fulltext_run_id,)):
        body = json.loads(q['request_json'])
        if digest(body) != q['request_sha256'] or body.get('reserved_bytes') != q['reserved_bytes']:
            raise ArtifactIntegrityError('Review robots request ledger mismatch')
        checks['robots_requests'] += 1
        result = store._one('SELECT * FROM pdf_review_robots_result WHERE request_id=?', (q['request_id'],))
        if result is not None:
            declared = json.loads(result['result_json']).get('bytes')
            if digest(json.loads(result['result_json'])) != result['result_sha256'] or declared != result['bytes'] or \
                    type(declared) is not int or not 0 <= declared <= q['reserved_bytes']:
                raise ArtifactIntegrityError('Review robots result ledger mismatch')
            checks['robots_results'] += 1
    cap, used = full.policy(fulltext_run_id)['global_download_budget'], combined_usage(store, full, fulltext_run_id)
    if used['http_requests'] > cap['http_requests'] or used['bytes'] > cap['bytes']:
        raise ArtifactIntegrityError('Review run exceeds its frozen budget including robots.txt requests')
    return dict(verified=True, checks=checks, usage=used)


class PoliteReviewTransport:
    """Stage11B transport with the discovery politeness gate in front of PDF requests (metadata lookups unchanged)."""

    def __init__(self, store, fulltext_run_id, transport, *, clock=time.monotonic, sleep=time.sleep, now=now_utc):
        from ..fulltext_acquisition import FullTextStore
        self.store, self.t, self.rid = store, transport, fulltext_run_id
        self.full = FullTextStore(store.db)
        self.p, self.key, self.email = transport.p, transport.key, transport.email
        self.cap = self.p['global_download_budget']
        self.settings = politeness_settings(transport.p)
        self.session_id = new_id('prp')
        with store.write():
            store._insert('pdf_review_politeness_session', dict(
                session_id=self.session_id, fulltext_run_id=fulltext_run_id, settings_json=canonical_json(self.settings),
                settings_sha256=digest(self.settings), started_at_utc=utc_now()))
        self.private = tuple(v for v in (transport.key, transport.email) if v)
        hosts = HostRegister(store, fulltext_run_id, now, private=self.private)
        self.polite = Politeness(self.settings, hosts, fetch_robots=self._robots, clock=clock, sleep=sleep, now=now)
        self.robots_requests = 0

    def _room(self, requested):
        used = combined_usage(self.store, self.full, self.rid)
        if used['http_requests'] >= self.cap['http_requests'] or self.cap['bytes'] - used['bytes'] < requested + CHUNK:
            raise FetchError('GLOBAL_BUDGET_EXHAUSTED')

    def _robots(self, url, gate, inspect):
        host = host_of(url)

        def reserve(safe, purpose, requested):
            with self.store.write():
                self._room(requested)
                request = dict(url=safe, purpose='ROBOTS', reserved_bytes=requested + CHUNK)
                guard(request, self.private)
                tid = new_id('prr')
                self.store._insert('pdf_review_robots_request', dict(
                    request_id=tid, session_id=self.session_id, host=host, reserved_bytes=requested + CHUNK,
                    request_json=canonical_json(request), request_sha256=digest(request), created_at_utc=utc_now()))
            self.robots_requests += 1
            return tid, requested

        def complete(ticket, result):
            result = dict(result)
            guard(result, self.private)
            with self.store.write():
                self.store._insert('pdf_review_robots_result', dict(
                    request_id=ticket, bytes=result['bytes'], result_json=canonical_json(result),
                    result_sha256=digest(result), finished_at_utc=utc_now()))
        body, _ = self.t.fetch(url, purpose='ROBOTS', max_bytes=self.settings['max_robots_bytes'], reserve=reserve,
                               complete=complete, gate=gate, inspect=inspect)
        return body

    def fetch(self, url, *, purpose, **kw):
        if purpose == 'PDF':
            self.polite.begin()
            original = kw['reserve']

            def reserve(safe, reserve_purpose, requested):
                # Stage11B checks its own ledger; robots.txt requests of this run count against the same caps.
                self._room(requested)
                return original(safe, reserve_purpose, requested)
            kw.update(gate=self.polite.gate, inspect=self.polite.inspect_pdf, reserve=reserve)
        return self.t.fetch(url, purpose=purpose, **kw)

    def summary(self):
        return dict(session_id=self.session_id, settings_sha256=digest(self.settings), robots_requests=self.robots_requests,
                    hosts_paused_this_invocation=dict(sorted(self.polite.paused.items())),
                    ledger=verify_review_politeness(self.store, self.full, self.rid))
