"""Sequential, resumable discovery downloader over the shared SafeTransport.

Each candidate attempt is reserved durably before any network I/O. A crash or Ctrl+C leaves an attempt
without a result, which resume reports as INTERRUPTED and retries within the frozen per-candidate bound.
Successful bytes are validated, placed in the content-addressed store and registered in one transaction
together with the attempt result and the work link, so success is never reported before verification.
"""
import time
from datetime import datetime, timedelta

from ..core import ValidationError
from ..core.ids import new_id, utc_now
from ..core.provenance import canonical_json
from ..corpus_release.model import digest
from ..fulltext import USER_AGENT, host_of
from ..fulltext_acquisition.safety import normalize_url
from ..fulltext_acquisition.transport import FetchError, SafeTransport
from .pdfcheck import validate_pdf
from .policy import transport_policy
from .politeness import Politeness, normalize_reason, now_utc, outcome_class, retry_after_seconds, utc_iso

ACCEPT = 'application/pdf,*/*;q=0.8'


def make_transport(p, *, credentials=None, resolver=None, opener=None, sleeper=time.sleep):
    """The shared Stage11B transport, configured with the discovery policy bounds and polite identity."""
    return SafeTransport(transport_policy(p), credentials=credentials, resolver=resolver, opener=opener, sleeper=sleeper,
                         user_agent=USER_AGENT, extra_headers={'Accept': ACCEPT})


class _Pacer:
    """Per-host pacing for provider API lookups, honouring Retry-After (seconds or HTTP-date) on 429 and 5xx."""

    def __init__(self, delay, clock, sleep, max_wait=0, now=now_utc, hosts=None, max_cooldown=86400):
        self.delay, self.clock, self.sleep, self.last = delay, clock, sleep, {}
        self.max_wait, self.now, self.not_before, self.hosts, self.max_cooldown = max_wait, now, {}, hosts, max_cooldown

    def _remaining(self, host):
        until = self.hosts.cooldown(host) if self.hosts is not None else None
        if not until:
            return 0.0
        return max(0.0, (datetime.fromisoformat(until.replace('Z', '+00:00')) - self.now()).total_seconds())

    def __call__(self, url):
        host = host_of(url)
        if self.hosts is not None and self.hosts.blocked(host):
            raise FetchError('HOST_BLOCKED', True, True)
        remaining = self._remaining(host)
        if remaining > self.max_wait:
            # A recorded provider deadline (this or an earlier command) is never shortened: the lookup waits.
            raise FetchError('HOST_COOLDOWN', True, True)
        if remaining > 0:
            self.not_before[host] = max(self.not_before.get(host, float('-inf')), self.clock() + remaining)
        wait = max(self.last.get(host, float('-inf')) + self.delay, self.not_before.get(host, float('-inf'))) - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last[host] = self.clock()

    def inspect(self, status, headers, head, url):
        if status != 429 and not 500 <= status <= 599:
            return
        host = host_of(url)
        withheld = headers.get('retry-after') == 'REDACTED'
        delay = None if withheld else retry_after_seconds(headers.get('retry-after'), self.now())
        if withheld or (delay is not None and delay > self.max_cooldown):
            # Unknown or longer than any cooldown this policy records: block until an operator unblocks the host.
            if self.hosts is not None:
                self.hosts.block(host, f'HTTP {status} Retry-After '
                                 + ('withheld (repeats a private value)' if withheld else 'exceeds max_cooldown_seconds')
                                 + ' (provider API)', status, url)
            raise FetchError('RATE_LIMITED', True, True)
        if delay is None:
            return
        if delay > 0 and self.hosts is not None:
            until = utc_iso(self.now() + timedelta(seconds=delay))
            self.hosts.cooldown_for(host, until, f'HTTP {status} Retry-After (provider API)', status, url)
        if delay > self.max_wait:
            # Longer than this command may wait: the lookup stays pending for a later discover or resume.
            raise FetchError('RATE_LIMITED', True, True)
        self.not_before[host] = max(self.not_before.get(host, float('-inf')), self.clock() + delay)


def lookup_gate(p, *, clock=None, sleep=None, now=None, hosts=None):
    """Provider API lookups are paced per host; robots.txt does not govern documented API endpoints."""
    return _Pacer(p['per_host_delay_seconds'], clock or time.monotonic, sleep or time.sleep, p['max_retry_delay'],
                  now or now_utc, hosts, p['max_cooldown_seconds'])


def query_values(urls):
    """Values of the sensitive query parameters of runtime URLs (those sanitize_url redacts), for the persistence
    guards. Ordinary parameters such as format=application/pdf stay usable; values under four characters are not
    distinctive enough for a substring guard, and retained response headers are reduced to their shape instead.
    """
    from urllib.parse import parse_qsl, unquote, urlsplit
    from ..fulltext_acquisition.safety import SENSITIVE
    values = set()
    for url in urls:
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=False):
            if (SENSITIVE.search(unquote(key)) or '@' in value) and len(value) >= 4:
                values.add(value)
    return tuple(sorted(values))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def runtime_urls_from(store, rid, paths):
    """Exact original URLs (for example signed links) re-read from the user's own input files.

    Only candidates whose stored URL was sanitized are matched, by the SHA-256 of the normalized URL. The returned
    mapping lives in memory for one command and is never written to the database, logs or exports.
    """
    from pathlib import Path
    from ..core import ValidationError as _Invalid
    from .inputs import MAX_INPUT_BYTES, read_table
    wanted = {}
    for c in store.candidates(rid):
        if c['runtime_url_required']:
            wanted.setdefault(c['url_identity_sha256'], []).append(c['candidate_id'])
    found = {}
    for path in map(Path, paths):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValidationError('Runtime URL source must be an ordinary input file within the byte limit')
        try:
            _, rows = read_table(path.name, path.read_bytes())
        except (ValueError, UnicodeError):
            raise ValidationError('Runtime URL source could not be parsed') from None
        for row in rows:
            # Nested values too: OpenAlex Work JSON keeps URLs inside locations and content_urls.
            for text in _strings(row):
                if not text.strip().lower().startswith(('http://', 'https://')):
                    continue
                try:
                    key = digest(normalize_url(text.strip()))
                except _Invalid:
                    continue
                for candidate_id in wanted.get(key, ()):
                    found[candidate_id] = text.strip()
    return found


class HostRegister:
    """Cached view of persistent host events; writes go straight to the database."""

    def __init__(self, store, rid, now, private=()):
        self.store, self.rid, self.now, self.cache, self.private = store, rid, now, {}, tuple(private)

    def _state(self, host):
        if host not in self.cache:
            self.cache[host] = self.store.host_state(host)
        return self.cache[host]

    def blocked(self, host):
        state = self._state(host)
        return state['reason'] if state['blocked'] else None

    def cooldown(self, host):
        until = self._state(host)['cooldown_until']
        return until if until and until > utc_iso(self.now()) else None

    def block(self, host, reason, status, url):
        if not self.blocked(host):
            self.store.host_event(host, 'BLOCK', reason, http_status=status, url=url, run_id=self.rid, private=self.private)
            self.cache.pop(host, None)

    def cooldown_for(self, host, until, reason, status, url):
        self.store.host_event(host, 'COOLDOWN', reason, http_status=status, url=url, until_utc=until, run_id=self.rid,
                              private=self.private)
        self.cache.pop(host, None)


class Downloader:
    def __init__(self, store, rid, transport, *, clock=time.monotonic, sleep=time.sleep, now=now_utc,
                 runtime_urls=None, progress=None):
        self.store, self.rid, self.t = store, rid, transport
        self.p = store.policy(rid)
        self.runtime_urls = dict(runtime_urls or {})
        # Values a remote page could reflect back: credentials and every query value of a runtime (signed) URL.
        self.private = tuple(v for v in (transport.key, transport.email) if v) + query_values(self.runtime_urls.values())
        self.hosts = HostRegister(store, rid, now, private=self.private)
        self.polite = Politeness(self.p, self.hosts, fetch_robots=self._robots, clock=clock, sleep=sleep, now=now)
        self.progress = progress

    def _robots(self, url, gate, inspect):
        body, _ = self.store.fetch(self.rid, self.t, url, 'ROBOTS', self.p['max_robots_bytes'], 'ROBOTS:' + host_of(url),
                                   self.p, gate=gate, inspect=inspect, private=self.private)
        return body

    def _available(self, host):
        # A short remaining cooldown is waited for by the gate; only a long one makes the host unavailable now.
        return not (self.hosts.blocked(host) or self.polite.cooldown_remaining(host) > self.p['max_retry_delay'] or
                    host in self.polite.paused or self.polite.failures.get(host, 0) >= self.p['host_failure_threshold'])

    def needs_try(self, candidate, retry_failed):
        tried = self.store.attempts(self.rid, candidate['candidate_id'])
        if not self._available(host_of(candidate['url'])):
            return False
        if not tried:
            return True
        if any(a['outcome'] == 'ACQUIRED' for a in tried) or len(tried) >= self.p['max_attempts_per_candidate']:
            return False
        last = tried[-1]
        reason = (last['failure_class'] or 'INTERRUPTED') if last['outcome'] else 'INTERRUPTED'
        if reason == 'RUNTIME_URL_REQUIRED' and candidate['candidate_id'] in self.runtime_urls:
            return True
        kind = outcome_class(reason)
        if kind == 'TRANSIENT':
            return True
        if kind == 'BLOCKED':
            chain = (last['result'] or {}).get('chain') or [host_of(candidate['url'])]
            return not any(self.hosts.blocked(h) for h in chain)
        return retry_failed

    def run(self, *, limit=None, retry_failed=False):
        run = self.store.get(self.rid)
        if run['state'] in ('OPEN', 'DISCOVERING'):
            raise ValidationError('Run discover before downloading')
        summary = dict(works_considered=0, works_attempted=0, acquired=0, failed_attempts=0, deferred_by_limit=0,
                       skipped_with_pdf=0, deferred_unavailable_host=0, budget_exhausted=False)
        if run['state'] == 'CLOSED':
            return dict(summary, run=self.store.inspect(self.rid))
        works = self.store.works(self.rid)
        linked = {l['work_id'] for l in self.store.links(self.rid)}
        for index, work in enumerate(works, 1):
            summary['works_considered'] += 1
            if work['work_id'] in linked:
                summary['skipped_with_pdf'] += 1
                continue
            eligible = [c for c in self.store.candidates(self.rid, work['work_id']) if c['eligible']][:self.p['per_item_candidate_limit']]
            todo = [c for c in eligible if self.needs_try(c, retry_failed)]
            if not todo:
                if any(not self._available(host_of(c['url'])) for c in eligible):
                    summary['deferred_unavailable_host'] += 1
                continue
            if limit is not None and summary['works_attempted'] >= limit:
                summary['deferred_by_limit'] += 1
                continue
            used = self.store.budget(self.rid)
            if used['items'] >= self.p['global_budget']['items'] and not any(self.store.attempts(self.rid, c['candidate_id']) for c in eligible):
                summary['budget_exhausted'] = True
                break
            if used['artifacts'] >= self.p['global_budget']['artifacts']:
                # Any further success could add a new artifact beyond the frozen cap.
                summary['budget_exhausted'] = True
                break
            summary['works_attempted'] += 1
            outcome = None
            for c in todo:
                if not self.needs_try(c, retry_failed):
                    continue
                result = self.attempt(c)
                outcome = result['failure_class'] or 'ACQUIRED'
                if result['outcome'] == 'ACQUIRED':
                    summary['acquired'] += 1
                    if self.p['stop_after_first_success']:
                        break
                else:
                    summary['failed_attempts'] += 1
                    if result['failure_class'] == 'BUDGET_EXHAUSTED':
                        summary['budget_exhausted'] = True
                        break
            if self.progress:
                self.progress(index, len(works), work, outcome)
            if summary['budget_exhausted']:
                break
        summary['hosts_paused_this_invocation'] = dict(sorted(self.polite.paused.items()))
        return dict(summary, run=self.store.inspect(self.rid))

    def _artifact_room(self, artifact_sha256):
        """Refuse a new distinct artifact once the frozen artifact budget is used; known bytes are always fine."""
        known = self.store._one("SELECT 1 FROM pdf_work_artifact WHERE run_id=? AND source='AUTO' AND artifact_sha256=?",
                                (self.rid, artifact_sha256))
        if not known and self.store.budget(self.rid)['artifacts'] >= self.p['global_budget']['artifacts']:
            raise FetchError('BUDGET_EXHAUSTED')

    def attempt(self, c):
        store, rid = self.store, self.rid
        previous = store.attempts(rid, c['candidate_id'])
        aid = new_id('pda')
        with store.write():
            store._insert('pdf_attempt', dict(run_id=rid, attempt_id=aid, candidate_id=c['candidate_id'], ordinal=len(previous),
                                              started_at_utc=utc_now()))
        sink = []
        result = dict(schema='pdf-attempt-result/1', candidate_id=c['candidate_id'], outcome='FAILED', failure_class=None,
                      artifact_sha256=None, http=None, request_ids=sink, chain=[], version_claim=c['version'])
        reason = None
        try:
            url = self.runtime_urls.get(c['candidate_id'], c['url'])
            if c['runtime_url_required'] and c['candidate_id'] not in self.runtime_urls:
                raise FetchError('RUNTIME_URL_REQUIRED')
            if digest(normalize_url(url)) != c['url_identity_sha256']:
                raise FetchError('RUNTIME_URL_REQUIRED')
            if host_of(url) == 'content.openalex.org' and not self.t.key:
                raise FetchError('CREDENTIAL_REQUIRED')
            self.polite.begin()
            body, http = store.fetch(rid, self.t, url, 'PDF', self.p['max_pdf_bytes'], c['candidate_id'], self.p,
                                     gate=self.polite.gate, inspect=self.polite.inspect_pdf, sink=sink, private=self.private)
            validation = validate_pdf(body, self.p)
            self._artifact_room(validation['artifact_sha256'])
            stored = store.cas.put_bytes(body)
            result.update(outcome='ACQUIRED', artifact_sha256=stored.artifact_sha256, http=http, chain=list(self.polite.chain))
            with store.write():
                self._artifact_room(stored.artifact_sha256)
                store.register_pdf(stored, validation)
                store._insert('pdf_attempt_result', dict(run_id=rid, attempt_id=aid, outcome='ACQUIRED', failure_class=None,
                                                         artifact_sha256=stored.artifact_sha256, result_json=canonical_json(result),
                                                         result_sha256=digest(result), finished_at_utc=utc_now()))
                store.add_link(rid, c['corpus_item_id'], stored.artifact_sha256, 'AUTO', attempt_id=aid,
                               basis='AUTOMATIC_DOWNLOAD_FROZEN_PLAN', version_claim=c['version'],
                               verification='PROVIDER_VERSION_CLAIM_UNVERIFIED' if c['version'] != 'UNKNOWN' else 'VERSION_UNKNOWN_UNVERIFIED')
            self.polite.note('ACQUIRED')
            return result
        except FetchError as exc:
            reason = normalize_reason(exc.reason)
        except ValidationError as exc:
            reason = ('PDF_SIZE_INVALID' if str(exc) == 'PDF_SIZE_INVALID' else
                      'PRIVATE_RESPONSE_REFUSED' if str(exc).startswith('Private') else 'PDF_VALIDATION_FAILED')
        self.polite.note(reason)
        if reason == 'RATE_LIMITED' and self.polite.chain:
            self.polite.paused[self.polite.chain[-1]] = 'RATE_LIMITED'
        result.update(failure_class=reason, retry_class=outcome_class(reason), chain=list(self.polite.chain),
                      http=store.request_result(rid, sink[-1]) if sink else None)
        with store.write():
            store._insert('pdf_attempt_result', dict(run_id=rid, attempt_id=aid, outcome='FAILED', failure_class=reason,
                                                     artifact_sha256=None, result_json=canonical_json(result),
                                                     result_sha256=digest(result), finished_at_utc=utc_now()))
        return result
