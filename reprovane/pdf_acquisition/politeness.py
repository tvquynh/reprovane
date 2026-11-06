"""Per-host politeness for discovery downloads: robots.txt (RFC 9309), pacing with Crawl-delay,
bot-check/CAPTCHA detection, Retry-After cooldowns and persistent blocked hosts.

Detection and robots matching reuse the accepted v4.1.0 implementation in reprovane.fulltext. The tool
never tries to get past a bot check, login, paywall or robots.txt rule.
"""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ..fulltext import _CHALLENGE_TITLES, RESOLVER_HOSTS, Robots, detect_bot_check, host_of
from ..fulltext_acquisition.transport import FetchError, REDIRECT_STATUSES

# Outcome classes. Transient outcomes are retried on resume (bounded); blocked outcomes only after the
# operator unblocks the host; permanent outcomes only with --retry-failed (still bounded).
TRANSIENT = {'RATE_LIMITED', 'HOST_COOLDOWN', 'HOST_PAUSED', 'HOST_UNREACHABLE', 'NETWORK_ERROR', 'REQUEST_TIMEOUT',
             'HTTP_5XX', 'TRUNCATED', 'ROBOTS_UNREACHABLE', 'INTERRUPTED', 'BUDGET_EXHAUSTED', 'CRAWL_DELAY_TOO_LONG'}
BLOCKED = {'BOT_CHECK', 'HOST_BLOCKED'}
TRANSPORT_REASONS = {'SERVER_ERROR': 'HTTP_5XX', 'TRANSPORT_ERROR': 'NETWORK_ERROR', 'MAX_BYTES': 'TOO_LARGE',
                     'REDIRECT_LIMIT': 'TOO_MANY_REDIRECTS', 'TRUNCATED_BODY': 'TRUNCATED',
                     'ANTI_BOT_BLOCKED': 'BOT_CHECK', 'GLOBAL_BUDGET_EXHAUSTED': 'BUDGET_EXHAUSTED',
                     'INVALID_LENGTH': 'TRUNCATED', 'DNS_UNAVAILABLE': 'NETWORK_ERROR'}
_TITLED_CHECK = re.compile(r"bot-check page '(.*)' \(HTTP (\d{3})\)", re.S)
# Order used to pick the reason shown for a work with several failed candidates (v4.1.0 order extended).
PRIORITY = ['BOT_CHECK', 'HOST_BLOCKED', 'ROBOTS_DISALLOWED', 'RATE_LIMITED', 'HOST_COOLDOWN', 'HOST_PAUSED',
            'CRAWL_DELAY_TOO_LONG',
            'ROBOTS_UNREACHABLE', 'HOST_UNREACHABLE', 'NETWORK_ERROR', 'REQUEST_TIMEOUT', 'HTTP_5XX', 'TRUNCATED',
            'INTERRUPTED', 'NOT_PDF', 'PDF_VALIDATION_FAILED', 'TOO_LARGE', 'TOO_MANY_REDIRECTS', 'AUTH_REQUIRED',
            'UNSAFE_URL', 'HTTPS_DOWNGRADE', 'ENCODING_REFUSED', 'RUNTIME_URL_REQUIRED', 'CREDENTIAL_REQUIRED',
            'BUDGET_EXHAUSTED', 'NOT_ATTEMPTED']


def fixed_reason(reason):
    """A bot-check reason without remote free text: a quoted page title becomes its known challenge phrase.

    The other detector reasons (Cloudflare, AWS WAF, TDM reservation, markers, plain 403) are fixed strings already.
    """
    match = _TITLED_CHECK.fullmatch(reason)
    if not match:
        return reason
    lowered = match[1].lower()
    phrase = next((p for p in _CHALLENGE_TITLES if lowered.startswith(p)), 'challenge page')
    return f"bot-check page '{phrase}' (HTTP {match[2]})"


def outcome_class(reason):
    if reason in TRANSIENT:
        return 'TRANSIENT'
    if reason in BLOCKED:
        return 'BLOCKED'
    return 'PERMANENT'


def normalize_reason(reason):
    return TRANSPORT_REASONS.get(reason, reason)


def pick(reasons):
    reasons = [r for r in reasons if r]
    known = [r for r in PRIORITY if r in reasons]
    return known[0] if known else (sorted(reasons)[0] if reasons else 'NOT_ATTEMPTED')


def retry_after_seconds(value, now):
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())


def utc_iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class Politeness:
    """One invocation's politeness state. Persistent host events go through the store callbacks."""

    def __init__(self, policy, hosts, *, fetch_robots, clock, sleep, now):
        self.p = policy
        self.hosts = hosts                # HostRegister: blocked(host), cooldown(host), block(...), cooldown_for(...)
        self.fetch_robots = fetch_robots  # callable(url, gate, inspect) -> bytes, raises FetchError
        self.clock, self.sleep, self.now = clock, sleep, now
        self.robots = {}
        self.last = {}
        self.not_before = {}
        self.failures = {}
        self.paused = {}
        self.chain = []
        self.events = []

    # -- pacing -------------------------------------------------------------------------------------------
    def pace(self, host):
        """Wait for the per-host delay, the robots.txt Crawl-delay and any short Retry-After, never less.

        A Crawl-delay longer than max_crawl_delay_seconds is never shortened: the gate pauses that host instead.
        """
        delay = self.p['per_host_delay_seconds']
        rules = self.robots.get(host)
        if rules is not None and rules.crawl_delay():
            delay = max(delay, rules.crawl_delay())
        wait = max(self.last.get(host, float('-inf')) + delay, self.not_before.get(host, float('-inf'))) - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last[host] = self.clock()

    # -- gates -----------------------------------------------------------------------------------------------
    def begin(self):
        self.chain = []

    def _availability(self, host):
        reason = self.hosts.blocked(host)
        if reason:
            raise FetchError('HOST_BLOCKED', terminal=True)
        remaining = self.cooldown_remaining(host)
        if remaining > 0:
            if remaining > self.p['max_retry_delay']:
                raise FetchError('HOST_COOLDOWN', True, True)
            # A short recorded deadline (possibly from an earlier command) is waited for, never shortened.
            self.not_before[host] = max(self.not_before.get(host, float('-inf')), self.clock() + max(0.0, remaining))
        if host in self.paused:
            raise FetchError(self.paused[host], True, True)
        if self.failures.get(host, 0) >= self.p['host_failure_threshold']:
            raise FetchError('HOST_UNREACHABLE', True, True)

    def cooldown_remaining(self, host):
        """Seconds left of the host's recorded cooldown (0 when none)."""
        until = self.hosts.cooldown(host)
        if not until:
            return 0.0
        return max(0.0, (datetime.fromisoformat(until.replace('Z', '+00:00')) - self.now()).total_seconds())

    def gate(self, url):
        """Before every PDF connection (first URL, each redirect hop, each retry)."""
        host = host_of(url)
        if host not in self.chain:
            self.chain.append(host)
        self._availability(host)
        if host not in self.robots:
            self._load_robots(url, host)
            self._availability(host)
        rules = self.robots[host]
        if rules is None:
            raise FetchError('ROBOTS_UNREACHABLE', True, True)
        if not rules.allowed(url):
            raise FetchError('ROBOTS_DISALLOWED', terminal=True)
        if (rules.crawl_delay() or 0) > self.p['max_crawl_delay_seconds']:
            # The host asks for more time between requests than this run will wait: defer, never shorten.
            self.paused[host] = 'CRAWL_DELAY_TOO_LONG'
            raise FetchError('CRAWL_DELAY_TOO_LONG', True, True)
        self.pace(host)

    def robots_gate(self, url):
        """Before every robots.txt connection, including redirect hops: block and cooldown of that host, then pacing."""
        host = host_of(url)
        reason = self.hosts.blocked(host)
        if reason:
            raise FetchError('HOST_BLOCKED', terminal=True)
        remaining = self.cooldown_remaining(host)
        if remaining > self.p['max_retry_delay']:
            raise FetchError('HOST_COOLDOWN', True, True)
        if remaining > 0:
            self.not_before[host] = max(self.not_before.get(host, float('-inf')), self.clock() + remaining)
        self.pace(host)

    def _load_robots(self, url, host):
        parts = urlsplit(url)
        target = f'{parts.scheme}://{parts.netloc}/robots.txt'
        rules = None
        try:
            body = self.fetch_robots(target, self.robots_gate, self._robots_inspect(host))
            rules = Robots(body.decode('utf-8', errors='replace'))
        except FetchError as exc:
            reason = normalize_reason(exc.reason)
            if reason in ('RATE_LIMITED', 'HOST_COOLDOWN'):
                # Rules unknown: pause the host for this invocation and read robots.txt again next time.
                self.paused[host] = 'RATE_LIMITED'
                return
            if reason.startswith('HTTP_4') or reason in ('AUTH_REQUIRED', 'BOT_CHECK', 'HOST_BLOCKED'):
                # RFC 9309: an unavailable (4xx) robots.txt means no rules. A block is re-checked by the gate.
                rules = Robots(allow_all=True)
            else:
                # RFC 9309: an unreachable robots.txt means complete disallow; retried on a later invocation.
                self.paused[host] = 'ROBOTS_UNREACHABLE'
        self.robots[host] = rules

    # -- response inspection --------------------------------------------------------------------------------
    def _rate_limit(self, host, status, headers, url):
        """Honour Retry-After (seconds or HTTP-date) and never schedule a request earlier than the host asked.

        Reasons are fixed text: the deadline is kept in until_utc, never echoed. A directive the transport withheld
        because it repeats a private value blocks the host, so no request is ever made earlier than asked.
        """
        if headers.get('retry-after') == 'REDACTED':
            self.hosts.block(host, f'HTTP {status} Retry-After withheld (repeats a private value)', status, url)
            raise FetchError('RATE_LIMITED' if status == 429 else 'HTTP_5XX', True, True)
        delay = retry_after_seconds(headers.get('retry-after'), self.now())
        if delay is not None and delay <= self.p['max_retry_delay']:
            # Short delay: the next request to this host (retry, robots.txt or PDF) waits at least that long, also
            # after an interruption or in a later command, because the deadline is recorded as a host event.
            self.not_before[host] = max(self.not_before.get(host, float('-inf')), self.clock() + delay)
            if delay > 0:
                until = self.now() + timedelta(seconds=delay)
                self.hosts.cooldown_for(host, utc_iso(until), f'HTTP {status} Retry-After', status, url)
        elif delay is not None and delay <= self.p['max_cooldown_seconds']:
            until = self.now() + timedelta(seconds=delay)
            self.hosts.cooldown_for(host, utc_iso(until), f'HTTP {status} Retry-After', status, url)
            raise FetchError('RATE_LIMITED' if status == 429 else 'HTTP_5XX', True, True)
        elif delay is not None:
            # Longer than this policy may wait: block until an operator unblocks, rather than return too early.
            self.hosts.block(host, f'HTTP {status} Retry-After exceeds max_cooldown_seconds', status, url)
            raise FetchError('RATE_LIMITED' if status == 429 else 'HTTP_5XX', True, True)

    def block_chain(self, reason, url, status, chain=None):
        """Block the host that served a bot check and every host that redirected to it (resolvers paused)."""
        for index, host in enumerate(reversed(self.chain if chain is None else chain)):
            if host in RESOLVER_HOSTS:
                if index == 0:
                    self.paused[host] = 'HOST_PAUSED'
                continue
            reason = fixed_reason(reason)
            self.hosts.block(host, reason if index == 0 else 'redirects to a bot check: ' + reason, status, url)

    def _robots_inspect(self, host):
        def inspect(status, headers, head, url):
            responder = host_of(url)  # after a redirect this is not the host whose rules are being read
            if status == 429:
                self._rate_limit(responder, status, headers, url)
                raise FetchError('RATE_LIMITED', True, True)
            if 500 <= status <= 599 and 'retry-after' in headers:
                # A short delay sets the not-before time used by the robots gate before the transport retries.
                self._rate_limit(responder, status, headers, url)
            if status not in REDIRECT_STATUSES:
                check = detect_bot_check(status, headers, head, plain_403=False)
                if check:
                    self.block_chain(check, url, status, chain=[host_of(url)])
                    raise FetchError('BOT_CHECK', terminal=True)
        return inspect

    def inspect_pdf(self, status, headers, head, url):
        host = host_of(url)
        if status in REDIRECT_STATUSES:
            check = detect_bot_check(status, headers, b'', plain_403=False)
            if check:
                self.block_chain(check, url, status)
                raise FetchError('BOT_CHECK', terminal=True)
            return
        if status == 429:
            self._rate_limit(host, status, headers, url)
            return
        pdf = head.startswith(b'%PDF-')
        check = detect_bot_check(status, headers, b'' if pdf else head, plain_403=self.p['block_on_plain_403'])
        if check:
            self.block_chain(check, url, status)
            raise FetchError('BOT_CHECK', terminal=True)
        if 500 <= status <= 599 and 'retry-after' in headers:
            self._rate_limit(host, status, headers, url)
            return
        if status == 200 and not pdf:
            raise FetchError('PDF_VALIDATION_FAILED' if b'%PDF-' in head[:1024] else 'NOT_PDF', terminal=True)

    # -- bookkeeping -------------------------------------------------------------------------------------------
    def note(self, reason):
        host = self.chain[-1] if self.chain else None
        if host is None:
            return
        if reason in ('NETWORK_ERROR', 'REQUEST_TIMEOUT'):
            self.failures[host] = self.failures.get(host, 0) + 1
        elif reason not in ('HOST_BLOCKED', 'HOST_COOLDOWN', 'HOST_PAUSED', 'HOST_UNREACHABLE', 'ROBOTS_UNREACHABLE', 'ROBOTS_DISALLOWED'):
            self.failures[host] = 0


def now_utc():
    return datetime.now(timezone.utc)
